"""
Client connection management.
Handles efficient tracking and cleanup of client connections.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional, Any
from enum import Enum
import logging
import time


logger = logging.getLogger(__name__)


class StreamType(Enum):
    """Type of stream a client is subscribed to."""
    VIDEO = "video"
    AUDIO = "audio"


@dataclass
class ClientStats:
    """Statistics for a single client connection."""
    connected_at: float = field(default_factory=time.time)
    frames_sent: int = 0
    bytes_sent: int = 0
    frames_dropped: int = 0  # Backpressure tracking
    last_activity: float = field(default_factory=time.time)
    
    def update(self, bytes_count: int) -> None:
        """Update stats after sending data."""
        self.frames_sent += 1
        self.bytes_sent += bytes_count
        self.last_activity = time.time()
    
    def record_drop(self) -> None:
        """Record a dropped frame due to backpressure."""
        self.frames_dropped += 1


class ClientQueue:
    """
    Wrapper around asyncio.Queue with automatic cleanup and stats tracking.
    Uses a context manager pattern for proper resource management.
    """
    
    def __init__(
        self,
        maxsize: int = 2,
        stream_type: StreamType = StreamType.VIDEO
    ):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._stream_type = stream_type
        self._stats = ClientStats()
        self._active = True
        self._id = id(self)
    
    @property
    def id(self) -> int:
        return self._id
    
    @property
    def stream_type(self) -> StreamType:
        return self._stream_type
    
    @property
    def stats(self) -> ClientStats:
        return self._stats
    
    @property
    def is_active(self) -> bool:
        return self._active
    
    def put_nowait(self, item: bytes) -> bool:
        """
        Put item in queue, dropping oldest if full (backpressure).
        Returns True if successful, False if client is inactive.
        """
        if not self._active:
            return False
            
        try:
            dropped = 0
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    dropped += 1
                except asyncio.QueueEmpty:
                    break
            
            if dropped > 0:
                self._stats.frames_dropped += dropped
            
            self._queue.put_nowait(item)
            self._stats.update(len(item))
            return True
        except Exception:
            return False
    
    async def get(self, timeout: float = 5.0) -> Optional[bytes]:
        """Get item from queue with timeout."""
        if not self._active:
            return None
            
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None
    
    def close(self) -> None:
        """Mark this queue as closed."""
        self._active = False
    
    def qsize(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()


class ConnectionManager:
    """
    Manages client connections for video and audio streams.
    
    Features:
    - Efficient client tracking with automatic cleanup
    - Memory-efficient queue management
    - Connection statistics and monitoring
    - Thread-safe operations
    """
    
    _instance: Optional["ConnectionManager"] = None
    
    def __new__(cls) -> "ConnectionManager":
        """Singleton pattern for global connection management."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._video_clients: Dict[int, ClientQueue] = {}
        self._audio_clients: Dict[int, ClientQueue] = {}
        self._lock = asyncio.Lock()
        self._max_clients: int = 100
        self._initialized = True
        
        logger.info("ConnectionManager initialized")
    
    @property
    def video_client_count(self) -> int:
        return len(self._video_clients)
    
    @property
    def audio_client_count(self) -> int:
        return len(self._audio_clients)
    
    @property
    def total_client_count(self) -> int:
        return self.video_client_count + self.audio_client_count
    
    def set_max_clients(self, max_clients: int) -> None:
        """Set maximum number of allowed clients."""
        self._max_clients = max_clients
    
    async def register_video_client(self, maxsize: int = 2) -> Optional[ClientQueue]:
        """Register a new video client."""
        return await self._register_client(StreamType.VIDEO, maxsize)
    
    async def register_audio_client(self, maxsize: int = 10) -> Optional[ClientQueue]:
        """Register a new audio client."""
        return await self._register_client(StreamType.AUDIO, maxsize)
    
    async def _register_client(
        self,
        stream_type: StreamType,
        maxsize: int
    ) -> Optional[ClientQueue]:
        """Internal method to register a client."""
        async with self._lock:
            if self.total_client_count >= self._max_clients:
                logger.warning(f"Max clients ({self._max_clients}) reached, rejecting connection")
                return None
            
            client = ClientQueue(maxsize=maxsize, stream_type=stream_type)
            
            if stream_type == StreamType.VIDEO:
                self._video_clients[client.id] = client
            else:
                self._audio_clients[client.id] = client
            
            logger.debug(f"Registered {stream_type.value} client {client.id}")
            return client
    
    async def unregister_client(self, client: ClientQueue) -> None:
        """Unregister a client connection."""
        async with self._lock:
            client.close()
            
            if client.stream_type == StreamType.VIDEO:
                self._video_clients.pop(client.id, None)
            else:
                self._audio_clients.pop(client.id, None)
            
            logger.debug(
                f"Unregistered {client.stream_type.value} client {client.id} "
                f"(sent {client.stats.frames_sent} frames, {client.stats.bytes_sent} bytes)"
            )
    
    def broadcast_video(self, frame: bytes) -> int:
        """
        Broadcast video frame to all connected video clients.
        Returns number of clients that received the frame.
        """
        sent_count = 0
        dead_clients = []
        
        for client_id, client in self._video_clients.items():
            if client.put_nowait(frame):
                sent_count += 1
            else:
                dead_clients.append(client_id)
        
        for client_id in dead_clients:
            self._video_clients.pop(client_id, None)
        
        return sent_count
    
    def broadcast_audio(self, chunk: bytes) -> int:
        """
        Broadcast audio chunk to all connected audio clients.
        Returns number of clients that received the chunk.
        """
        sent_count = 0
        dead_clients = []
        
        for client_id, client in self._audio_clients.items():
            if client.qsize() < 10 and client.put_nowait(chunk):
                sent_count += 1
            elif not client.is_active:
                dead_clients.append(client_id)
        
        for client_id in dead_clients:
            self._audio_clients.pop(client_id, None)
        
        return sent_count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics including backpressure metrics."""
        total_dropped = sum(
            c.stats.frames_dropped 
            for c in list(self._video_clients.values()) + list(self._audio_clients.values())
        )
        
        return {
            "video_clients": self.video_client_count,
            "audio_clients": self.audio_client_count,
            "total_clients": self.total_client_count,
            "max_clients": self._max_clients,
            "frames_dropped": total_dropped,
        }
    
    async def cleanup_inactive(self, timeout: float = 30.0) -> int:
        """
        Clean up clients that have been inactive for too long.
        Returns number of clients removed.
        """
        async with self._lock:
            current_time = time.time()
            removed = 0
            
            for clients in [self._video_clients, self._audio_clients]:
                dead = [
                    cid for cid, client in clients.items()
                    if current_time - client.stats.last_activity > timeout
                ]
                for cid in dead:
                    clients.pop(cid, None)
                    removed += 1
            
            if removed:
                logger.info(f"Cleaned up {removed} inactive clients")
            
            return removed


connection_manager = ConnectionManager()
