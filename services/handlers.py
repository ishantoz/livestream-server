"""
HTTP request handlers for the streaming server.
Implements ASGI handlers for video, audio, and web interface.
"""

import asyncio
import logging
from typing import Any, Callable, Awaitable, Dict, List, Tuple

from lib.config import config
from lib.wav import create_streaming_wav_header, WavParams
from services.connection import connection_manager
from services.broadcaster import broadcaster


logger = logging.getLogger(__name__)


# Type aliases for ASGI
Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]
Headers = List[Tuple[bytes, bytes]]


class BaseHandler:
    """Base class for HTTP handlers."""
    
    @staticmethod
    async def send_response(
        send: Send,
        status: int,
        headers: Headers,
        body: bytes = b""
    ) -> None:
        """Send a complete HTTP response."""
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
    
    @staticmethod
    async def send_error(send: Send, status: int, message: str) -> None:
        """Send an error response."""
        body = f'{{"error": "{message}"}}'.encode()
        await BaseHandler.send_response(
            send,
            status,
            [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            body
        )


class VideoStreamHandler(BaseHandler):
    """Handler for MJPEG video streaming."""
    
    @staticmethod
    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        """Handle video stream request."""
        # Register client
        client = await connection_manager.register_video_client(
            maxsize=config.video.frame_buffer_size
        )
        
        if client is None:
            await VideoStreamHandler.send_error(send, 503, "Server at capacity")
            return
        
        # Send headers for MJPEG stream
        headers: Headers = [
            (b"content-type", b"multipart/x-mixed-replace; boundary=frame"),
            (b"connection", b"keep-alive"),
            (b"cache-control", b"no-cache, no-store, must-revalidate"),
            (b"pragma", b"no-cache"),
            (b"expires", b"0"),
            (b"access-control-allow-origin", b"*"),
        ]
        
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": headers,
        })
        
        try:
            # Send current frame immediately if available
            current = broadcaster.current_frame
            if current:
                await send({
                    "type": "http.response.body",
                    "body": current,
                    "more_body": True,
                })
            
            # Stream frames
            while True:
                frame = await client.get(timeout=config.server.connection_timeout)
                
                if frame is None:
                    # Timeout - send empty to keep connection alive
                    continue
                
                try:
                    await send({
                        "type": "http.response.body",
                        "body": frame,
                        "more_body": True,
                    })
                except (Exception, asyncio.CancelledError):
                    break
                    
        except (Exception, asyncio.CancelledError) as e:
            logger.debug(f"Video client disconnected: {e}")
        finally:
            await connection_manager.unregister_client(client)


class AudioStreamHandler(BaseHandler):
    """Handler for WAV audio streaming."""
    
    @staticmethod
    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        """Handle audio stream request."""
        # Register client
        client = await connection_manager.register_audio_client(
            maxsize=config.audio.buffer_size
        )
        
        if client is None:
            await AudioStreamHandler.send_error(send, 503, "Server at capacity")
            return
        
        # Send headers for WAV stream
        headers: Headers = [
            (b"content-type", b"audio/wav"),
            (b"connection", b"keep-alive"),
            (b"cache-control", b"no-cache, no-store, must-revalidate"),
            (b"access-control-allow-origin", b"*"),
        ]
        
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": headers,
        })
        
        # Send WAV header
        wav_header = create_streaming_wav_header(WavParams(
            sample_rate=config.audio.sample_rate,
            channels=config.audio.channels,
            bits_per_sample=config.audio.bits_per_sample,
        ))
        
        await send({
            "type": "http.response.body",
            "body": wav_header,
            "more_body": True,
        })
        
        try:
            # Stream audio chunks
            while True:
                chunk = await client.get(timeout=config.server.connection_timeout)
                
                if chunk is None:
                    continue
                
                try:
                    await send({
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": True,
                    })
                except (Exception, asyncio.CancelledError):
                    break
                    
        except (Exception, asyncio.CancelledError) as e:
            logger.debug(f"Audio client disconnected: {e}")
        finally:
            await connection_manager.unregister_client(client)


class StatsHandler(BaseHandler):
    """Handler for server statistics."""
    
    @staticmethod
    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        """Handle stats request."""
        import json
        
        stream_stats = broadcaster.stats
        
        stats = {
            "broadcaster": {
                "state": broadcaster.state.value,
                "running": broadcaster.is_running,
            },
            "stream": {
                "elapsed_seconds": round(stream_stats.elapsed, 2),
                "video_frames": stream_stats.video_frames,
                "audio_chunks": stream_stats.audio_chunks,
                "video_timestamp": round(stream_stats.video_timestamp(), 3),
                "audio_timestamp": round(stream_stats.audio_timestamp(), 3),
                "sync_drift": round(stream_stats.video_timestamp() - stream_stats.audio_timestamp(), 3),
            },
            "connections": connection_manager.get_stats(),
            "config": {
                "video_fps": config.video.fps,
                "audio_sample_rate": config.audio.sample_rate,
            }
        }
        
        body = json.dumps(stats, indent=2).encode()
        
        await StatsHandler.send_response(
            send,
            200,
            [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"access-control-allow-origin", b"*"),
            ],
            body
        )
