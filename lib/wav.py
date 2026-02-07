"""
WAV file format utilities.
Handles WAV header creation for audio streaming.
"""

import struct
from typing import NamedTuple


class WavParams(NamedTuple):
    """WAV file parameters."""
    sample_rate: int
    channels: int
    bits_per_sample: int


def create_streaming_wav_header(params: WavParams) -> bytes:
    """
    Create a WAV header optimized for infinite streaming.
    
    Uses maximum file size values to support continuous streaming
    without needing to know the total audio length in advance.
    
    Args:
        params: WAV file parameters
        
    Returns:
        44-byte WAV header
    """
    byte_rate = params.sample_rate * params.channels * (params.bits_per_sample // 8)
    block_align = params.channels * (params.bits_per_sample // 8)
    
    # Use max size for streaming (infinite length)
    data_size = 0xFFFFFFFF - 36
    
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        0xFFFFFFFF,      # File size (max for streaming)
        b'WAVE',
        b'fmt ',
        16,              # Subchunk1 size (PCM)
        1,               # Audio format (1 = PCM)
        params.channels,
        params.sample_rate,
        byte_rate,
        block_align,
        params.bits_per_sample,
        b'data',
        data_size,
    )
    
    return header


def create_wav_header(
    sample_rate: int,
    channels: int,
    bits_per_sample: int,
    data_size: int
) -> bytes:
    """
    Create a standard WAV header with known data size.
    
    Args:
        sample_rate: Audio sample rate in Hz
        channels: Number of audio channels
        bits_per_sample: Bits per sample (8, 16, 24, or 32)
        data_size: Size of the audio data in bytes
        
    Returns:
        44-byte WAV header
    """
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,  # File size
        b'WAVE',
        b'fmt ',
        16,              # Subchunk1 size
        1,               # Audio format (PCM)
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size,
    )
    
    return header
