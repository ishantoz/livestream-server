"""
Request handlers for the streaming server.
HTTP stream handler for fMP4 video, stats endpoint.
"""

import asyncio
import json
import logging
from typing import Any, Callable, Awaitable, Dict, List, Tuple

from lib.config import config
from services.connection import connection_manager
from services.broadcaster import broadcaster


logger = logging.getLogger(__name__)


# Type aliases for ASGI
Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]
Headers = List[Tuple[bytes, bytes]]


class HttpStreamHandler:
    """Handler for HTTP fMP4 video streaming.
    
    Protocol:
    1. Client requests GET /stream
    2. Server responds with Content-Type: video/mp4 (no Content-Length = streaming)
    3. Server sends cached init segment (ftyp + moov)
    4. Server streams fMP4 media chunks (moof + mdat) as HTTP response body
    5. Connection stays open until client disconnects
    
    The browser's native <video> element plays fMP4 directly — no JavaScript,
    no MediaSource Extensions, no WebSocket. Perfect A/V sync handled by browser.
    """
    
    @staticmethod
    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        """Handle HTTP stream request."""
        # Register client with backpressure queue
        client = await connection_manager.register_client(
            maxsize=config.video.chunk_buffer_size
        )
        
        if client is None:
            # Server full — return 503
            body = b"Server at capacity, try again later"
            await send({
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        
        # Send response headers — no Content-Length means chunked/streaming
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"video/mp4"),
                (b"cache-control", b"no-cache, no-store"),
                (b"access-control-allow-origin", b"*"),
            ],
        })
        
        # Wait for init segment (broadcaster might still be starting)
        init = connection_manager.init_segment
        for _ in range(100):  # Up to 10 seconds
            if init is not None:
                break
            await asyncio.sleep(0.1)
            init = connection_manager.init_segment
        
        if init is None:
            logger.warning("No init segment available after 10s, closing stream")
            await connection_manager.unregister_client(client)
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        
        # Send init segment (ftyp + moov) — browser needs this first
        try:
            await send({
                "type": "http.response.body",
                "body": init,
                "more_body": True,
            })
        except Exception:
            await connection_manager.unregister_client(client)
            return
        
        # Monitor for client disconnect
        disconnect_event = asyncio.Event()
        
        async def watch_disconnect():
            try:
                while True:
                    msg = await receive()
                    if msg["type"] == "http.disconnect":
                        disconnect_event.set()
                        return
            except (asyncio.CancelledError, Exception):
                disconnect_event.set()
        
        disconnect_task = asyncio.create_task(watch_disconnect())
        
        try:
            # Stream fMP4 chunks to client
            while not disconnect_event.is_set():
                chunk = await client.get(timeout=config.server.connection_timeout)
                
                if chunk is None:
                    # Timeout — connection still alive, just no data yet
                    continue
                
                try:
                    await send({
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": True,
                    })
                except (Exception, asyncio.CancelledError):
                    break
                    
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            disconnect_task.cancel()
            try:
                await disconnect_task
            except asyncio.CancelledError:
                pass
            await connection_manager.unregister_client(client)
            # Close HTTP response
            try:
                await send({
                    "type": "http.response.body",
                    "body": b"",
                    "more_body": False,
                })
            except Exception:
                pass


class StatsHandler:
    """Handler for server statistics (HTTP)."""
    
    @staticmethod
    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        """Handle stats request."""
        stream_stats = broadcaster.stats
        
        stats = {
            "broadcaster": {
                "state": broadcaster.state.value,
                "running": broadcaster.is_running,
            },
            "stream": {
                "elapsed_seconds": round(stream_stats.elapsed, 2),
                "chunks_sent": stream_stats.chunks_sent,
                "bytes_sent": stream_stats.bytes_sent,
            },
            "connections": connection_manager.get_stats(),
            "config": {
                "video_fps": config.video.fps,
                "video_crf": config.video.effective_crf,
                "audio_bitrate": config.video.audio_bitrate,
            }
        }
        
        body = json.dumps(stats, indent=2).encode()
        
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"access-control-allow-origin", b"*"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
