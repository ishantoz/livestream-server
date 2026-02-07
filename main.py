"""
Live Video Streaming Server

A high-performance ASGI server for streaming video with synchronized audio.
Supports multiple concurrent clients with efficient memory management.

Usage:
    uvicorn main:app --reload
    
Endpoints:
    /       - Web player interface
    /live   - MJPEG video stream
    /audio  - WAV audio stream  
    /stats  - Server statistics (JSON)
"""

import asyncio
import logging
from typing import Dict, Any, Callable, Awaitable

from lib import config, get_player_html
from services import (
    ensure_broadcaster_running,
    VideoStreamHandler,
    AudioStreamHandler,
    StatsHandler,
    connection_manager,
    broadcaster,
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Type aliases
Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]


class StreamingApp:
    """
    Main ASGI application for video streaming.
    
    Provides routing and request handling for:
    - Video streaming (MJPEG)
    - Audio streaming (WAV)
    - Web player interface
    - Server statistics
    """
    
    def __init__(self):
        self._routes = {
            "/live": VideoStreamHandler.handle,
            "/audio": AudioStreamHandler.handle,
            "/stats": StatsHandler.handle,
        }
        connection_manager.set_max_clients(config.server.max_clients)
        logger.info(f"StreamingApp initialized with max {config.server.max_clients} clients")
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI application entry point."""
        if scope["type"] == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return
        
        if scope["type"] != "http":
            return
        
        # Ensure broadcaster is running
        await ensure_broadcaster_running()
        
        # Get request path
        path = scope.get("path", "/")
        
        # Route request
        handler = self._routes.get(path)
        
        if handler:
            await handler(scope, receive, send)
        else:
            await self._serve_index(scope, receive, send)
    
    async def _handle_lifespan(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle ASGI lifespan events for startup/shutdown."""
        while True:
            message = await receive()
            
            if message["type"] == "lifespan.startup":
                logger.info("ASGI lifespan: startup")
                await send({"type": "lifespan.startup.complete"})
                
            elif message["type"] == "lifespan.shutdown":
                logger.info("ASGI lifespan: shutdown - stopping broadcaster")
                await broadcaster.stop()
                logger.info("Broadcaster stopped, completing shutdown")
                await send({"type": "lifespan.shutdown.complete"})
                return
    
    async def _serve_index(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve the web player interface."""
        html = get_player_html()
        body = html.encode("utf-8")
        
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-cache"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })


# Create ASGI application instance
app = StreamingApp()


# For backwards compatibility and direct running
async def application(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI application callable for backwards compatibility."""
    await app(scope, receive, send)


