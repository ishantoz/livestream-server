"""
Client connection management for HTTP streaming.
Handles efficient tracking and cleanup of client connections.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional, Any
import logging
import time


logger = logging.getLogger(__name__)


@dataclass
class ClientStats:
    """Statistics for a single client connection."""
    connected_at: float = field(default_factory=time.time)
    chunks_sent: int = 0
    bytes_sent: int = 0
    chunks_dropped: int = 0  # Backpressure tracking
    last_activity: float = field(default_factory=time.time)
    
    def update(self, bytes_count: int) -> None:
        """Update stats after sending data."""
        self.chunks_sent += 1
        self.bytes_sent += bytes_count
        self.last_activity = time.time()
    
    def record_drop(self) -> None:
        """Record a dropped chunk due to backpressure."""
        self.chunks_dropped += 1


class ClientQueue:
    """
    Wrapper around asyncio.Queue with backpressure and stats tracking.
    Each HTTP streaming client gets one queue for muxed fMP4 chunks.
    """
    
    def __init__(self, maxsize: int = 4):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._stats = ClientStats()
        self._active = True
        self._id = id(self)
    
    @property
    def id(self) -> int:
        return self._id
    
    @property
    def stats(self) -> ClientStats:
        return self._stats
    
    @property
    def is_active(self) -> bool:
        return self._active
    
    def put_nowait(self, item: bytes) -> bool:
        """
        Put item in queue, dropping OLDEST if full (ring buffer backpressure).
        Returns True if successful, False if client is inactive.
        
        Unlike draining the whole queue, this only drops one item at a time
        to maintain continuous segment delivery for fMP4 streaming.
        """
        if not self._active:
            return False
            
        try:
            # Only drop if queue is full — keep as many segments as possible
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                    self._stats.chunks_dropped += 1
                except asyncio.QueueEmpty:
                    pass
            
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
    Manages HTTP streaming client connections for fMP4 streaming.
    
    Features:
    - Single client type (muxed audio+video chunks)
    - Caches fMP4 init segment for new clients
    - Backpressure via queue-based dropping
    - Connection statistics and monitoring
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
            
        self._clients: Dict[int, ClientQueue] = {}
        self._lock = asyncio.Lock()
        self._max_clients: int = 100
        self._init_segment: Optional[bytes] = None
        self._initialized = True
        
        logger.info("ConnectionManager initialized")
    
    @property
    def client_count(self) -> int:
        return len(self._clients)
    
    @property
    def init_segment(self) -> Optional[bytes]:
        """Get the cached fMP4 init segment (moov atom)."""
        return self._init_segment
    
    def set_init_segment(self, data: bytes) -> None:
        """Cache the fMP4 init segment for new clients."""
        self._init_segment = data
        logger.debug(f"Init segment cached ({len(data)} bytes)")
    
    def set_max_clients(self, max_clients: int) -> None:
        """Set maximum number of allowed clients."""
        self._max_clients = max_clients
    
    async def register_client(self, maxsize: int = 4) -> Optional[ClientQueue]:
        """Register a new streaming client."""
        async with self._lock:
            if self.client_count >= self._max_clients:
                logger.warning(f"Max clients ({self._max_clients}) reached, rejecting connection")
                return None
            
            client = ClientQueue(maxsize=maxsize)
            self._clients[client.id] = client
            
            logger.debug(f"Registered client {client.id} (total: {self.client_count})")
            return client
    
    async def unregister_client(self, client: ClientQueue) -> None:
        """Unregister a client connection."""
        async with self._lock:
            client.close()
            self._clients.pop(client.id, None)
            
            logger.debug(
                f"Unregistered client {client.id} "
                f"(sent {client.stats.chunks_sent} chunks, {client.stats.bytes_sent} bytes, "
                f"total: {self.client_count})"
            )
    
    def broadcast(self, chunk: bytes) -> int:
        """
        Broadcast fMP4 chunk to all connected clients.
        Returns number of clients that received the chunk.
        """
        sent_count = 0
        dead_clients = []
        
        for client_id, client in self._clients.items():
            if client.put_nowait(chunk):
                sent_count += 1
            else:
                dead_clients.append(client_id)
        
        for client_id in dead_clients:
            self._clients.pop(client_id, None)
        
        return sent_count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics including backpressure metrics."""
        total_dropped = sum(
            c.stats.chunks_dropped for c in self._clients.values()
        )
        
        return {
            "clients": self.client_count,
            "max_clients": self._max_clients,
            "chunks_dropped": total_dropped,
            "init_segment_cached": self._init_segment is not None,
        }
    
    async def cleanup_inactive(self, timeout: float = 30.0) -> int:
        """
        Clean up clients that have been inactive for too long.
        Returns number of clients removed.
        """
        async with self._lock:
            current_time = time.time()
            dead = [
                cid for cid, client in self._clients.items()
                if current_time - client.stats.last_activity > timeout
            ]
            for cid in dead:
                self._clients.pop(cid, None)
            
            if dead:
                logger.info(f"Cleaned up {len(dead)} inactive clients")
            
            return len(dead)


connection_manager = ConnectionManager()
