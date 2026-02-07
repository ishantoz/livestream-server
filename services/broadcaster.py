"""
Media broadcasting service.
Single FFmpeg process outputs fragmented MP4 (H.264 + AAC) to stdout.
Chunks are broadcast to all HTTP streaming clients via the connection manager.
"""

import asyncio
import atexit
import logging
import os
import signal
import sys
import time
from typing import Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from lib.config import config, SourceType
from services.connection import connection_manager


logger = logging.getLogger(__name__)

# Track all FFmpeg process PIDs for cleanup on exit
_ffmpeg_pids: Set[int] = set()


def _cleanup_ffmpeg_processes():
    """Kill any remaining FFmpeg processes on exit.
    
    Safety net — catches orphaned FFmpeg processes that weren't
    cleaned up by the normal shutdown path (e.g., Ctrl+C during reload).
    """
    for pid in list(_ffmpeg_pids):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    _ffmpeg_pids.clear()


def _make_child_die_with_parent():
    """Preexec function: ensure FFmpeg dies if the Python parent is killed.
    
    On Linux: uses prctl PR_SET_PDEATHSIG so the kernel sends SIGKILL.
    On macOS: sets process group so atexit can kill the group.
    """
    if sys.platform == "linux":
        try:
            import ctypes
            PR_SET_PDEATHSIG = 1
            libc = ctypes.CDLL("libc.so.6")
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
        except Exception:
            pass


# Register cleanup handler
atexit.register(_cleanup_ffmpeg_processes)


class BroadcasterState(Enum):
    """State of the broadcaster service."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class StreamStats:
    """Statistics for the fMP4 broadcast stream."""
    start_time: float = field(default_factory=time.time)
    chunks_sent: int = 0
    bytes_sent: int = 0
    
    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time


class MediaBroadcaster:
    """
    Singleton service that broadcasts video+audio as fragmented MP4.
    
    Architecture:
    - Single FFmpeg process for ALL source types
    - Outputs fMP4 (H.264 + AAC) to stdout
    - Reads raw byte chunks and broadcasts to all HTTP streaming clients
    - Init segment (moov atom) cached for new client initialization
    - Browser plays via MSE + fetch — perfect A/V sync
    """
    
    _instance: Optional["MediaBroadcaster"] = None
    
    def __new__(cls) -> "MediaBroadcaster":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._video_config = config.video
        
        self._state = BroadcasterState.STOPPED
        self._task: Optional[asyncio.Task] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        
        self._shutdown_event = asyncio.Event()
        self._stats = StreamStats()
        self._initialized = True
        logger.info("MediaBroadcaster initialized")
    
    @property
    def stats(self) -> StreamStats:
        return self._stats
    
    @property
    def state(self) -> BroadcasterState:
        return self._state
    
    @property
    def is_running(self) -> bool:
        return self._state == BroadcasterState.RUNNING
    
    async def start(self) -> bool:
        """Start the broadcaster if not already running."""
        if self._state in (BroadcasterState.RUNNING, BroadcasterState.STARTING):
            return True
        
        self._state = BroadcasterState.STARTING
        self._shutdown_event.clear()
        
        try:
            self._task = asyncio.create_task(self._broadcast_loop())
            source_type = self._video_config.source_type
            
            # Build quality info string
            if self._video_config.quality is not None:
                q = self._video_config.quality
                scale = self._video_config.effective_scale
                quality_info = (
                    f"quality: {q} "
                    f"(CRF: {self._video_config.effective_crf}"
                    f"{f', scale: {scale:.0%}' if scale and scale < 1.0 else ''})"
                )
            elif self._video_config.resolution:
                quality_info = f"resolution: {self._video_config.resolution}, CRF: {self._video_config.crf}"
            else:
                quality_info = f"CRF: {self._video_config.crf}"
            
            logger.info(
                f"Broadcaster started - source: {self._video_config.file_path} "
                f"(type: {source_type.name}, live: {self._video_config.is_live_source}, "
                f"loop: {self._video_config.can_loop}, {quality_info})"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to start broadcaster: {e}")
            self._state = BroadcasterState.ERROR
            return False
    
    async def stop(self) -> None:
        """Stop the broadcaster gracefully."""
        if self._state == BroadcasterState.STOPPED:
            return
        
        logger.info("Stopping broadcaster...")
        self._state = BroadcasterState.STOPPING
        self._shutdown_event.set()
        
        # Kill FFmpeg process
        await self._kill_process()
        
        # Wait for task to complete
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        self._state = BroadcasterState.STOPPED
        logger.info("Broadcaster stopped")
    
    async def _kill_process(self) -> None:
        """Terminate the FFmpeg process. Uses SIGKILL for reliability."""
        if self._process and self._process.returncode is None:
            pid = self._process.pid
            try:
                self._process.kill()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=3.0)
                    logger.debug(f"FFmpeg process killed (PID {pid})")
                except asyncio.TimeoutError:
                    logger.warning(f"FFmpeg PID {pid} didn't die, force killing via OS")
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.debug(f"Error stopping FFmpeg process: {e}")
            finally:
                _ffmpeg_pids.discard(pid)
        
        self._process = None
    
    async def _broadcast_loop(self) -> None:
        """Main broadcast loop that manages the FFmpeg process."""
        self._state = BroadcasterState.RUNNING
        restart_delay = 1.0  # Exponential backoff for restarts
        
        while not self._shutdown_event.is_set():
            try:
                cycle_start = time.time()
                await self._run_broadcast_cycle()
                cycle_duration = time.time() - cycle_start
                
                # If cycle lasted a reasonable time, reset backoff
                if cycle_duration > 10:
                    restart_delay = 1.0
                else:
                    # Short cycle = FFmpeg failed quickly, increase backoff
                    logger.warning(f"FFmpeg exited after {cycle_duration:.1f}s, restarting in {restart_delay:.0f}s...")
                    await asyncio.sleep(restart_delay)
                    restart_delay = min(restart_delay * 2, 30.0)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Broadcast cycle error: {e}")
                await asyncio.sleep(restart_delay)
                restart_delay = min(restart_delay * 2, 30.0)
        
        self._state = BroadcasterState.STOPPED
    
    async def _run_broadcast_cycle(self) -> None:
        """Run one broadcast cycle: start FFmpeg, read chunks, broadcast."""
        self._stats = StreamStats()
        
        self._process = await self._create_ffmpeg_process()
        pid = self._process.pid
        
        try:
            await self._read_and_broadcast(self._process)
        finally:
            rc = self._process.returncode if self._process else None
            if rc is not None and rc != 0 and rc != -9:  # -9 = SIGKILL (normal shutdown)
                logger.warning(f"FFmpeg (PID {pid}) exited with code {rc}")
            await self._kill_process()
    
    def _build_input_args(self) -> list[str]:
        """Build FFmpeg input arguments based on source type."""
        args = []
        source_type = self._video_config.source_type
        path = self._video_config.file_path
        
        # Add pacing flag to match real-time playback speed.
        # Without -re, FFmpeg processes as fast as possible — for remote HTTP
        # files this means 3-5x real-time, causing all clients to desync.
        # Only skip -re for truly live sources (RTSP, devices) that already
        # produce data at real-time.
        if not self._video_config.is_live_source:
            args.extend(["-re"])
        elif path.lower().startswith(("http://", "https://")):
            # Remote HTTP video files download faster than real-time
            # and need pacing to prevent fast-forward and FFmpeg restart storms
            args.extend(["-re"])
        
        # Loop for regular files and remote HTTP video files
        # This prevents FFmpeg from exiting and restarting (which resets position)
        if self._video_config.can_loop:
            args.extend(["-stream_loop", "-1"])
        elif path.lower().startswith(("http://", "https://")):
            args.extend(["-stream_loop", "-1"])
        
        # Add format specifier and options for devices
        if source_type == SourceType.DEVICE:
            if path.startswith("avfoundation:"):
                args.extend(["-f", "avfoundation"])
                # For avfoundation, pass the device spec after the colon
                device_spec = path.split(":", 1)[1]
                path = device_spec
                        
            elif path.startswith("/dev/video"):
                args.extend(["-f", "v4l2"])
                args.extend(["-framerate", str(self._video_config.fps)])
        
        # Add RTSP transport options for reliability
        if source_type == SourceType.LIVE_STREAM and path.lower().startswith("rtsp://"):
            args.extend(["-rtsp_transport", "tcp"])
        
        # HTTP/HTTPS source options: User-Agent and reconnect resilience
        if source_type == SourceType.LIVE_STREAM and path.lower().startswith(("http://", "https://")):
            args.extend([
                "-user_agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
            ])
        
        args.extend(["-i", path])
        return args
    
    def _build_video_filter_args(self) -> list[str]:
        """Build FFmpeg video filter args for scaling."""
        args = []
        scale = self._video_config.effective_scale
        if scale is not None and scale < 1.0:
            args.extend([
                "-vf",
                f"scale=trunc(iw*{scale:.4f}/2)*2:trunc(ih*{scale:.4f}/2)*2"
            ])
        elif self._video_config.resolution:
            try:
                w, h = self._video_config.resolution.lower().split("x")
                args.extend(["-vf", f"scale={w}:{h}"])
            except ValueError:
                logger.warning(f"Invalid VIDEO_RESOLUTION '{self._video_config.resolution}', using original")
        return args
    
    async def _create_ffmpeg_process(self) -> asyncio.subprocess.Process:
        """Create single FFmpeg process outputting fragmented MP4.
        
        Output format: fMP4 with H.264 video + AAC audio
        - empty_moov: init segment at the start (no seek needed)
        - frag_keyframe: new fragment at each keyframe
        - default_base_moof: required for browser compatibility
        
        Uses libx264 software encoding (ultrafast preset) for maximum
        compatibility across all platforms.
        """
        input_args = self._build_input_args()
        video_filter_args = self._build_video_filter_args()
        crf = self._video_config.effective_crf
        fps = self._video_config.fps
        audio_bitrate = self._video_config.audio_bitrate
        
        cmd = [
            "ffmpeg",
            "-hide_banner",
            *input_args,
            # Video: H.264 Baseline profile for widest browser support
            # - level 3.1 supports up to 1080p@30fps (level 3.0 caps at 720p)
            # - yuv420p pixel format required by all mobile decoders
            *video_filter_args,
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.1",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-crf", str(crf),
            "-g", str(fps),      # Keyframe every 1 second
            "-r", str(fps),
            # Audio: AAC-LC stereo 44.1kHz (browser-compatible)
            "-c:a", "aac",
            "-ac", "2",
            "-ar", "44100",
            "-b:a", audio_bitrate,
            # Output: fragmented MP4 to stdout
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-frag_duration", "500000",  # Fragment every 500ms for smooth streaming
            "pipe:1",
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # Capture stderr for logging
            preexec_fn=_make_child_die_with_parent,
        )
        _ffmpeg_pids.add(process.pid)
        
        # Start background task to log FFmpeg stderr
        asyncio.create_task(self._log_ffmpeg_stderr(process))
        
        logger.info(f"FFmpeg started (PID {process.pid}) — fMP4 output (H.264 CRF={crf}, AAC {audio_bitrate})")
        return process
    
    async def _log_ffmpeg_stderr(self, process: asyncio.subprocess.Process) -> None:
        """Read and log FFmpeg stderr output for diagnostics."""
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                # Log errors/warnings prominently, other lines as debug
                lower = text.lower()
                if any(w in lower for w in ("error", "fatal", "failed", "invalid")):
                    logger.error(f"FFmpeg: {text}")
                elif "warning" in lower:
                    logger.warning(f"FFmpeg: {text}")
                else:
                    logger.debug(f"FFmpeg: {text}")
        except (asyncio.CancelledError, Exception):
            pass
    
    @staticmethod
    def _find_init_end(data: bytes) -> int:
        """Find the byte offset where the init segment ends.
        
        Walks MP4 top-level boxes (ftyp, moov, free, skip) and returns
        the position of the first non-init box (typically moof).
        Returns -1 if we haven't received enough data yet.
        """
        pos = 0
        while pos + 8 <= len(data):
            box_size = int.from_bytes(data[pos:pos + 4], 'big')
            box_type = data[pos + 4:pos + 8]
            
            if box_size < 8:
                return -1  # Invalid box, need more data
            
            # Init boxes: ftyp, moov, free, skip
            if box_type in (b'ftyp', b'moov', b'free', b'skip'):
                pos += box_size
            else:
                # First non-init box found (moof, mdat, etc.)
                return pos
        
        return -1  # Need more data
    
    async def _read_and_broadcast(self, process: asyncio.subprocess.Process) -> None:
        """Read fMP4 from FFmpeg and broadcast to all clients.
        
        Two phases:
        1. Init: accumulate bytes until ftyp+moov are captured, cache as init segment
        2. Media: stream raw bytes directly to clients — the browser's MSE
           SourceBuffer in 'sequence' mode reassembles MP4 boxes internally.
        
        Streaming raw bytes (instead of accumulating complete segments) eliminates
        server-side buffering delay and ensures continuous data delivery. Combined
        with 'sequence' mode on the client, this is immune to timestamp resets
        when the source video loops (-stream_loop -1).
        """
        init_captured = False
        init_buf = b""
        
        while not self._shutdown_event.is_set():
            try:
                chunk = await process.stdout.read(16384)  # 16KB for frequent delivery
                if not chunk:
                    break
            except (Exception, asyncio.CancelledError):
                break
            
            # Phase 1: Capture the init segment (ftyp + moov)
            if not init_captured:
                init_buf += chunk
                init_end = self._find_init_end(init_buf)
                
                if init_end > 0:
                    # Init segment is complete — cache it for new clients
                    connection_manager.set_init_segment(init_buf[:init_end])
                    logger.info(f"Init segment captured ({init_end} bytes)")
                    init_captured = True
                    
                    # Any leftover bytes after init are media data — send them
                    leftover = init_buf[init_end:]
                    if leftover:
                        self._stats.chunks_sent += 1
                        self._stats.bytes_sent += len(leftover)
                        connection_manager.broadcast(leftover)
                    init_buf = b""  # Free memory
                continue
            
            # Phase 2: Stream raw bytes — SourceBuffer (sequence mode) handles parsing
            self._stats.chunks_sent += 1
            self._stats.bytes_sent += len(chunk)
            connection_manager.broadcast(chunk)


# Global broadcaster instance
broadcaster = MediaBroadcaster()


async def ensure_broadcaster_running() -> bool:
    """Ensure the broadcaster is running. Call this before handling requests."""
    return await broadcaster.start()
