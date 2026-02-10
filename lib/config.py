"""
Configuration settings for the streaming server.
Centralized configuration management with environment variable support.
"""

import os
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class SourceType(Enum):
    """Type of video source for FFmpeg configuration."""
    FILE = auto()           # Local video file (can loop, needs -re pacing)
    LIVE_STREAM = auto()    # RTSP/HTTP/etc streams (already real-time)
    DEVICE = auto()         # Camera devices (/dev/video0, avfoundation)
    GROWING_FILE = auto()   # File being written to (OBS recording)


def detect_source_type(path: str) -> SourceType:
    """
    Detect the type of video source from the path.
    
    This affects which FFmpeg flags we use:
    - FILE: -re (pacing) + -stream_loop (repeat)
    - LIVE_STREAM: no pacing, no loop (already real-time)
    - DEVICE: no pacing, no loop, may need -f flag
    - GROWING_FILE: -re for pacing, but no loop
    """
    path_lower = path.lower()
    
    # Protocol-based streams
    if any(path_lower.startswith(proto) for proto in [
        "rtsp://", "rtmp://", "http://", "https://", 
        "srt://", "udp://", "tcp://", "rtp://"
    ]):
        return SourceType.LIVE_STREAM
    
    # macOS screen/camera capture
    if path_lower.startswith("avfoundation:"):
        return SourceType.DEVICE
    
    # Linux device files
    if path.startswith("/dev/video"):
        return SourceType.DEVICE
    
    # Windows DirectShow (typically "video=Device Name")
    if "video=" in path_lower or path_lower.startswith("dshow:"):
        return SourceType.DEVICE
    
    # Check for GROWING_FILE env var hint
    if os.getenv("GROWING_FILE", "").lower() in ("1", "true", "yes"):
        return SourceType.GROWING_FILE
    
    # Default: treat as regular file
    return SourceType.FILE


@dataclass(frozen=True)
class VideoConfig:
    """Video streaming configuration."""
    file_path: str = "video.mp4"
    fps: int = 30
    crf: int = 23                      # H.264 CRF (0=lossless, 51=worst). Lower = better.
    audio_bitrate: str = "128k"        # AAC audio bitrate
    resolution: Optional[str] = None   # e.g., "1280x720", None = original
    quality: Optional[float] = None    # 0.0 (worst) to 1.0 (best) — controls CRF + resolution
    chunk_buffer_size: int = 64        # Client queue size (large for raw byte streaming)
    
    @property
    def effective_crf(self) -> int:
        """Get effective H.264 CRF value (0=lossless, 51=worst).
        
        If quality is set, maps 1.0→18 (high quality) and 0.0→40 (low quality).
        Otherwise uses the explicit crf setting.
        """
        if self.quality is not None:
            q = max(0.0, min(1.0, self.quality))
            return round(40 - q * 22)  # 1.0→18, 0.0→40
        return self.crf
    
    @property
    def effective_scale(self) -> Optional[float]:
        """Get resolution scale factor (0.0-1.0) from quality setting.
        
        Maps quality 1.0→1.0 (full res) and 0.0→0.25 (quarter res).
        Returns None if quality is not set (use explicit resolution instead).
        """
        if self.quality is not None:
            q = max(0.0, min(1.0, self.quality))
            return 0.25 + q * 0.75  # 1.0→1.0, 0.0→0.25
        return None
    
    @property
    def source_type(self) -> SourceType:
        """Detect source type from file path."""
        return detect_source_type(self.file_path)
    
    @property
    def is_live_source(self) -> bool:
        """Returns True if source is live (no pacing/looping needed)."""
        return self.source_type in (SourceType.LIVE_STREAM, SourceType.DEVICE)
    
    @property
    def can_loop(self) -> bool:
        """Returns True if source can be looped."""
        return self.source_type == SourceType.FILE


@dataclass(frozen=True)
class AudioConfig:
    """Audio streaming configuration (AAC output)."""
    bitrate: str = "128k"   # AAC bitrate


@dataclass(frozen=True)
class ServerConfig:
    """Server configuration."""
    host: str = "127.0.0.1"
    port: int = 8000
    connection_timeout: float = 5.0
    max_clients: int = 100


@dataclass
class AppConfig:
    """Main application configuration."""
    video: VideoConfig = field(default_factory=VideoConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    
    @classmethod
    def from_env(cls) -> "AppConfig":
        """Create configuration from environment variables."""
        return cls(
            video=VideoConfig(
                file_path=os.getenv("VIDEO_FILE", "video.mp4"),
                fps=int(os.getenv("VIDEO_FPS", "30")),
                crf=int(os.getenv("VIDEO_CRF", "23")),
                audio_bitrate=os.getenv("AUDIO_BITRATE", "128k"),
                resolution=os.getenv("VIDEO_RESOLUTION"),  # e.g., "1280x720"
                quality=float(os.environ["VIDEO_QUALITY"]) if "VIDEO_QUALITY" in os.environ else None,
            ),
            audio=AudioConfig(
                bitrate=os.getenv("AUDIO_BITRATE", "128k"),
            ),
            server=ServerConfig(
                host=os.getenv("SERVER_HOST", "127.0.0.1"),
                port=int(os.getenv("SERVER_PORT", "8000")),
                max_clients=int(os.getenv("MAX_CLIENTS", "100")),
            ),
        )


# Global configuration instance
config = AppConfig.from_env()
