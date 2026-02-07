"""
Media broadcasting service.
Handles video and audio extraction from source files using ffmpeg.
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
    
    This is a safety net — catches orphaned FFmpeg processes that weren't
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
class FrameData:
    """Container for a video frame with timestamp for sync."""
    jpeg_data: bytes
    timestamp: float = field(default_factory=time.time)
    frame_number: int = 0


@dataclass
class StreamStats:
    """Statistics for stream synchronization using wall-clock time."""
    start_time: float = field(default_factory=time.time)
    video_frames: int = 0
    audio_chunks: int = 0
    # Track bytes sent for accurate audio timestamp
    audio_bytes_sent: int = 0
    
    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time
    
    def video_timestamp(self) -> float:
        """Get video position based on frame count and FPS."""
        return self.video_frames / config.video.fps
    
    def audio_timestamp(self) -> float:
        """Get audio position based on actual bytes sent."""
        bytes_per_second = (
            config.audio.sample_rate *
            config.audio.channels *
            config.audio.bytes_per_sample
        )
        if bytes_per_second == 0:
            return 0.0
        return self.audio_bytes_sent / bytes_per_second


class MediaBroadcaster:
    """
    Singleton service that broadcasts video and audio streams.
    
    Features:
    - Single ffmpeg process for video, single for audio (synced by timing)
    - Efficient frame parsing and distribution
    - Automatic reconnection on failure
    - Graceful shutdown handling
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
        self._audio_config = config.audio
        
        self._state = BroadcasterState.STOPPED
        self._task: Optional[asyncio.Task] = None
        self._video_process: Optional[asyncio.subprocess.Process] = None
        self._audio_process: Optional[asyncio.subprocess.Process] = None
        
        self._current_frame: Optional[bytes] = None
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
    def current_frame(self) -> Optional[bytes]:
        return self._current_frame
    
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
                quality_info = (
                    f"quality: {q} "
                    f"(scale: {self._video_config.effective_scale:.0%}, "
                    f"jpeg: {self._video_config.effective_jpeg_quality})"
                )
            elif self._video_config.resolution:
                quality_info = f"resolution: {self._video_config.resolution}"
            else:
                quality_info = "quality: original"
            
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
        
        # Kill processes
        await self._kill_processes()
        
        # Wait for task to complete
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        self._state = BroadcasterState.STOPPED
        logger.info("Broadcaster stopped")
    
    async def _kill_processes(self) -> None:
        """Terminate all ffmpeg processes. Uses SIGKILL for reliability."""
        for name, process in [("video", self._video_process), ("audio", self._audio_process)]:
            if process and process.returncode is None:
                pid = process.pid
                try:
                    # Send SIGKILL directly — FFmpeg with device capture
                    # (avfoundation) often ignores SIGTERM
                    process.kill()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=3.0)
                        logger.debug(f"{name} process killed (PID {pid})")
                    except asyncio.TimeoutError:
                        # Last resort: os.kill
                        logger.warning(f"{name} PID {pid} didn't die, force killing via OS")
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except (ProcessLookupError, OSError):
                            pass
                except ProcessLookupError:
                    pass
                except Exception as e:
                    logger.debug(f"Error stopping {name} process: {e}")
                finally:
                    _ffmpeg_pids.discard(pid)
        
        self._video_process = None
        self._audio_process = None
    
    async def _broadcast_loop(self) -> None:
        """Main broadcast loop that manages ffmpeg processes."""
        self._state = BroadcasterState.RUNNING
        
        while not self._shutdown_event.is_set():
            try:
                await self._run_broadcast_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Broadcast cycle error: {e}")
                await asyncio.sleep(1.0)
        
        self._state = BroadcasterState.STOPPED
    
    async def _run_broadcast_cycle(self) -> None:
        """Run one cycle of broadcasting (until error or shutdown)."""
        # Reset stats for new cycle
        self._stats = StreamStats()
        
        # Start ffmpeg processes
        self._video_process = await self._create_video_process()
        
        # For device sources (cameras), audio may not be available
        # Only create audio process for file/stream sources
        has_audio = not self._video_config.is_live_source or self._has_audio_stream()
        if has_audio:
            self._audio_process = await self._create_audio_process()
        else:
            self._audio_process = None
            if not hasattr(self, '_audio_disabled_logged'):
                logger.info("Audio disabled for video-only device source")
                self._audio_disabled_logged = True
        
        # Shared clock for both streams — set AFTER both processes are created
        loop = asyncio.get_event_loop()
        stream_start = loop.time()
        
        try:
            # Run video reader (always)
            video_task = asyncio.create_task(
                self._read_video_frames(self._video_process, stream_start)
            )
            
            tasks = [video_task]
            
            # Add audio reader if available
            if self._audio_process:
                audio_task = asyncio.create_task(
                    self._read_audio_chunks(self._audio_process, stream_start)
                )
                tasks.append(audio_task)
            
            # Wait for video to complete (audio ending won't stop video)
            # For video-only sources, just wait for video
            if len(tasks) == 1:
                await video_task
            else:
                # Wait for video to finish; if audio ends first, keep video running
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # If video task completed, cancel audio
                # If audio task completed first, let video continue
                if video_task in done:
                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                else:
                    # Audio ended but video still running - wait for video
                    logger.debug("Audio stream ended, video continuing")
                    await video_task
                    
        finally:
            await self._kill_processes()
    
    def _has_audio_stream(self) -> bool:
        """Check if source likely has audio. Conservative - returns True for files/streams."""
        source_type = self._video_config.source_type
        
        # Files and streams usually have audio
        if source_type in (SourceType.FILE, SourceType.LIVE_STREAM, SourceType.GROWING_FILE):
            return True
        
        # Devices (cameras) usually don't have embedded audio
        # User can override by setting VIDEO_FILE to include audio device
        # e.g., "avfoundation:0:1" for video device 0 + audio device 1
        path = self._video_config.file_path
        if path.startswith("avfoundation:") and ":" in path.split("avfoundation:", 1)[1]:
            # Has format "avfoundation:video:audio" - audio specified
            return True
        
        return False
    
    def _build_input_args(self, audio_only: bool = False) -> list[str]:
        """
        Build FFmpeg input arguments based on source type.
        
        Args:
            audio_only: If True, build args for audio-only capture (used for 
                       avfoundation where video and audio need separate processes)
        """
        args = []
        source_type = self._video_config.source_type
        path = self._video_config.file_path
        
        # Add pacing flag only for files (live sources are already real-time)
        if not self._video_config.is_live_source:
            args.extend(["-re"])
        
        # Add looping only for regular files
        if self._video_config.can_loop:
            args.extend(["-stream_loop", "-1"])
        
        # Add format specifier and options for devices
        if source_type == SourceType.DEVICE:
            if path.startswith("avfoundation:"):
                args.extend(["-f", "avfoundation"])
                
                # Extract device spec from "avfoundation:X" or "avfoundation:X:Y"
                device_spec = path.split(":", 1)[1]
                
                # Check if both video and audio devices specified (e.g., "0:1")
                if ":" in device_spec:
                    video_dev, audio_dev = device_spec.split(":", 1)
                    if audio_only:
                        # Audio-only: use ":audio_dev" format
                        path = f":{audio_dev}"
                    else:
                        # Video-only: use "video_dev" format  
                        path = video_dev
                else:
                    # Single device (video only)
                    path = device_spec
                
                # Don't force framerate — let FFmpeg auto-negotiate with device
                # Some devices (e.g., OBS Virtual Camera) only support specific rates
                        
            elif path.startswith("/dev/video"):
                args.extend(["-f", "v4l2"])
                # v4l2 also benefits from explicit framerate
                if not audio_only:
                    args.extend(["-framerate", str(self._video_config.fps)])
        
        # Add RTSP transport options for reliability
        if source_type == SourceType.LIVE_STREAM and path.lower().startswith("rtsp://"):
            args.extend(["-rtsp_transport", "tcp"])
        
        # Set browser User-Agent for HTTP/HTTPS sources to avoid being blocked
        if source_type == SourceType.LIVE_STREAM and path.lower().startswith(("http://", "https://")):
            args.extend([
                "-user_agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36",
            ])
        
        args.extend(["-i", path])
        return args
    
    async def _create_video_process(self) -> asyncio.subprocess.Process:
        """Create ffmpeg process for video extraction."""
        input_args = self._build_input_args()
        
        # Build scale filter — VIDEO_QUALITY (0-1) takes priority over VIDEO_RESOLUTION
        output_args = []
        scale = self._video_config.effective_scale
        if scale is not None and scale < 1.0:
            # Scale by factor, keep dimensions divisible by 2
            # trunc(iw*s/2)*2 rounds down to nearest even number
            output_args.extend([
                "-vf",
                f"scale=trunc(iw*{scale:.4f}/2)*2:trunc(ih*{scale:.4f}/2)*2"
            ])
            logger.info(f"Video scaled to {scale:.0%} (quality={self._video_config.quality})")
        elif self._video_config.resolution:
            # Explicit resolution (e.g., "1280x720")
            try:
                w, h = self._video_config.resolution.lower().split("x")
                output_args.extend(["-vf", f"scale={w}:{h}"])
                logger.info(f"Video resolution set to {w}x{h}")
            except ValueError:
                logger.warning(f"Invalid VIDEO_RESOLUTION '{self._video_config.resolution}', using original")
        
        # Use effective JPEG quality (derived from VIDEO_QUALITY or JPEG_QUALITY)
        jpeg_q = self._video_config.effective_jpeg_quality
        
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *input_args,
            *output_args,
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", str(jpeg_q),
            "-r", str(self._video_config.fps),
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            preexec_fn=_make_child_die_with_parent,
        )
        _ffmpeg_pids.add(process.pid)
        logger.debug(f"Video FFmpeg started (PID {process.pid}, q:v={jpeg_q})")
        return process
    
    async def _create_audio_process(self) -> asyncio.subprocess.Process:
        """Create ffmpeg process for audio extraction."""
        # For avfoundation with separate audio device, use audio_only mode
        # This makes the input "-i :audio_device" instead of "-i video:audio"
        is_avfoundation_with_audio = (
            self._video_config.source_type == SourceType.DEVICE and
            self._video_config.file_path.startswith("avfoundation:") and
            ":" in self._video_config.file_path.split("avfoundation:", 1)[1]
        )
        
        input_args = self._build_input_args(audio_only=is_avfoundation_with_audio)
        
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *input_args,
            "-vn",                          # No video
            "-acodec", "pcm_s16le",
            "-ar", str(self._audio_config.sample_rate),
            "-ac", str(self._audio_config.channels),
            "-f", "s16le",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            preexec_fn=_make_child_die_with_parent,
        )
        _ffmpeg_pids.add(process.pid)
        logger.debug(f"Audio FFmpeg started (PID {process.pid})")
        return process
    
    async def _read_video_frames(self, process: asyncio.subprocess.Process, stream_start: float) -> None:
        """Read and broadcast video frames from ffmpeg, clock-synced."""
        buffer = b""
        frame_interval = 1 / self._video_config.fps
        loop = asyncio.get_event_loop()
        
        while not self._shutdown_event.is_set():
            # Read chunk from ffmpeg
            try:
                chunk = await process.stdout.read(65536)
                if not chunk:
                    break
            except (Exception, asyncio.CancelledError):
                break
            
            buffer += chunk
            
            # Parse JPEG frames from buffer
            while True:
                # Find JPEG start marker (FFD8)
                start = buffer.find(b'\xff\xd8')
                if start == -1:
                    buffer = b""
                    break
                
                # Find JPEG end marker (FFD9)
                end = buffer.find(b'\xff\xd9', start + 2)
                if end == -1:
                    buffer = buffer[start:]
                    break
                
                # Extract complete JPEG
                jpeg_data = buffer[start:end + 2]
                buffer = buffer[end + 2:]
                
                # Track frame count for sync
                self._stats.video_frames += 1
                timestamp = self._stats.video_timestamp()
                
                # Build MJPEG frame with timestamp header
                mjpeg_frame = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg_data)).encode() + b"\r\n"
                    b"X-Timestamp: " + f"{timestamp:.3f}".encode() + b"\r\n\r\n"
                    + jpeg_data + b"\r\n"
                )
                
                # Store and broadcast
                self._current_frame = mjpeg_frame
                connection_manager.broadcast_video(mjpeg_frame)
                
                # Clock-based rate limiting: sleep until we should show this frame
                expected_time = self._stats.video_frames * frame_interval
                actual_elapsed = loop.time() - stream_start
                drift = actual_elapsed - expected_time
                
                if drift < -0.001:
                    await asyncio.sleep(-drift)
    
    async def _read_audio_chunks(self, process: asyncio.subprocess.Process, stream_start: float) -> None:
        """Read and broadcast audio chunks from ffmpeg, paced to match video."""
        # Calculate chunk size for one video frame duration of audio
        frame_interval = 1 / self._video_config.fps
        chunk_size = int(
            self._audio_config.sample_rate *
            self._audio_config.channels *
            self._audio_config.bytes_per_sample *
            frame_interval
        )
        
        loop = asyncio.get_event_loop()
        
        while not self._shutdown_event.is_set():
            chunk_start = loop.time()
            
            try:
                audio_data = await process.stdout.read(chunk_size)
                if not audio_data:
                    break
                
                # Track audio chunks and bytes for sync monitoring
                self._stats.audio_chunks += 1
                self._stats.audio_bytes_sent += len(audio_data)
                
                connection_manager.broadcast_audio(audio_data)
                
                # Pace audio to match real-time playback
                # Calculate where we should be vs where we are
                expected_time = self._stats.audio_chunks * frame_interval
                actual_elapsed = loop.time() - stream_start
                drift = actual_elapsed - expected_time
                
                # If we're ahead (read faster than real-time), sleep to sync
                if drift < -0.001:
                    await asyncio.sleep(-drift)
                
            except (Exception, asyncio.CancelledError):
                break


# Global broadcaster instance
broadcaster = MediaBroadcaster()


async def ensure_broadcaster_running() -> bool:
    """Ensure the broadcaster is running. Call this before handling requests."""
    return await broadcaster.start()
