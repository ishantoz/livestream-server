import uvicorn
import os
import signal
import asyncio
import logging
from services.broadcaster import broadcaster


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_MODULE = os.getenv("APP_MODULE", "main:app")  # module:app
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))
RELOAD = os.getenv("RELOAD", "false").lower() in ("true", "1", "yes")


class GracefulShutdown:
    """Handle graceful shutdown on SIGTERM/SIGINT."""
    
    def __init__(self):
        self.shutdown_requested = False
        self._server = None
    
    def set_server(self, server):
        self._server = server
    
    def handle_signal(self, signum, frame):
        """Handle shutdown signals gracefully."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, initiating graceful shutdown...")
        self.shutdown_requested = True
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._cleanup())
        except Exception as e:
            logger.warning(f"Error during shutdown cleanup: {e}")
    
    async def _cleanup(self):
        """Clean up resources before shutdown."""
        try:
            from services.broadcaster import broadcaster
            await broadcaster.stop()
            logger.info("Broadcaster stopped cleanly")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")


shutdown_handler = GracefulShutdown()


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    if not RELOAD or os.environ.get("UVICORN_STARTED"):
        signal.signal(signal.SIGTERM, shutdown_handler.handle_signal)
        signal.signal(signal.SIGINT, shutdown_handler.handle_signal)
        logger.info("Signal handlers registered for graceful shutdown")


if __name__ == "__main__":
    setup_signal_handlers()
    
    uvicorn.run(
        APP_MODULE,
        host=HOST,
        port=PORT,
        reload=RELOAD,
    )
