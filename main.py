import logging
from pathlib import Path
from typing import Dict, Any, Callable, Awaitable

from lib import config
from services import (
    ensure_broadcaster_running,
    HttpStreamHandler,
    StatsHandler,
    connection_manager,
    broadcaster,
)

# Directory holding static assets (index.html, player.js, player.css)
PUBLIC_DIR = Path(__file__).resolve().parent / "public"

# MIME types for static files
MIME_TYPES: Dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


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
    
    Routes:
    - /stream      → HTTP fMP4 stream
    - /stats       → Server statistics (JSON)
    - /player.js   → Player JavaScript
    - /player.css  → Player styles
    - /            → Web player interface (index.html)
    """
    
    def __init__(self):
        self._api_routes = {
            "/stream": HttpStreamHandler.handle,
            "/stats": StatsHandler.handle,
        }
        connection_manager.set_max_clients(config.server.max_clients)
        logger.info(f"StreamingApp initialized with max {config.server.max_clients} clients")
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI application entry point."""
        if scope["type"] == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return
        
        if scope["type"] == "http":
            await ensure_broadcaster_running()
            
            path = scope.get("path", "/")
            handler = self._api_routes.get(path)
            
            if handler:
                await handler(scope, receive, send)
            else:
                await self._serve_static(path, scope, receive, send)
            return
    
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
    
    async def _serve_static(self, path: str, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve static files from public/ directory."""
        # Map / to index.html
        filename = "index.html" if path == "/" else path.lstrip("/")
        
        # Prevent directory traversal
        file_path = (PUBLIC_DIR / filename).resolve()
        if not str(file_path).startswith(str(PUBLIC_DIR)):
            await self._send_error(send, 403, b"Forbidden")
            return
        
        if not file_path.is_file():
            await self._send_error(send, 404, b"Not found")
            return
        
        # Determine content type
        ext = file_path.suffix.lower()
        content_type = MIME_TYPES.get(ext, "application/octet-stream")
        
        body = file_path.read_bytes()
        
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", content_type.encode()),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-cache"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
    
    @staticmethod
    async def _send_error(send: Send, status: int, body: bytes) -> None:
        """Send an HTTP error response."""
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode()),
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
