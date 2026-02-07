# livestream-server

A lightweight HTTP-based video streaming server built from scratch in Python.

> **Learning project** — Built to understand how video streaming works at the protocol level. Experimental, not for production use.

Stream video from any source — files, live cameras, screen recordings — to any browser. No plugins, no WebRTC, no complex setup. Just HTTP.

## Features

- **Works with any video source** — MP4 files, RTSP cameras, Raspberry Pi camera, OBS recordings, or any growing video file
- **Single FFmpeg, unlimited viewers** — Video processing happens once, then fans out to all clients. 1 viewer or 100, same CPU cost
- **Browser-native playback** — Uses MJPEG for video (just an `<img>` tag) and Web Audio API for sound
- **Multiple concurrent clients** — Async architecture handles many viewers without blocking
- **Zero client setup** — Open URL in any browser, streaming starts immediately
- **Adjustable quality** — Single `VIDEO_QUALITY` knob (0–1) controls resolution + compression together
- **Backpressure handling** — Automatically drops frames for slow clients to prevent memory bloat
- **Sync monitoring** — Built-in `/stats` endpoint tracks audio/video drift

## Use cases

**Good for:**
- IP cameras / CCTV monitoring
- Raspberry Pi or embedded device streams
- Internal dashboards and live monitoring
- Screen sharing within local network
- Learning how streaming protocols work

**Not ideal for:**
- Video calls (needs sub-150ms latency)
- Public internet streaming (no CDN, no adaptive bitrate)
- Mobile networks (no reconnection handling)

## Quick start

### Requirements

- Python 3.12+
- FFmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows
winget install ffmpeg
```

### Run

```bash
# Using uv (recommended)
uv sync
uv run server.py

# Or using pip
pip install -r requirements.txt
python server.py
```

Open `http://localhost:8000` in your browser.

## Video sources

The server auto-detects source types and uses appropriate FFmpeg flags:

| Source type          | Detection                                       | Behavior                               |
| -------------------- | ----------------------------------------------- | -------------------------------------- |
| **File**             | Default                                         | Loops infinitely, paced at real-time   |
| **RTSP/HTTP stream** | `rtsp://`, `http://`, etc.                      | No loop, no pacing (already real-time) |
| **Camera device**    | `/dev/video*`, `avfoundation:*` (name or index) | No loop, no pacing                     |
| **Growing file**     | `GROWING_FILE=1` env var                        | Paced playback, no loop                |

```bash
# Local file (loops forever)
VIDEO_FILE=video.mp4 uv run server.py

# RTSP camera (auto-detected as live)
VIDEO_FILE=rtsp://192.168.1.100:554/stream uv run server.py

# Raspberry Pi / USB camera (auto-detected as device)
VIDEO_FILE=/dev/video0 uv run server.py

# OBS recording in progress (set GROWING_FILE=1 to prevent EOF)
GROWING_FILE=1 VIDEO_FILE=/path/to/obs-recording.mkv uv run server.py

# macOS — OBS Virtual Camera (by name or index)
VIDEO_FILE="avfoundation:OBS Virtual Camera" uv run server.py
VIDEO_FILE="avfoundation:1" uv run server.py

# macOS — built-in camera
VIDEO_FILE="avfoundation:0" uv run server.py

# macOS — camera + microphone (video_device:audio_device)
VIDEO_FILE="avfoundation:0:0" uv run server.py

# macOS — screen capture
VIDEO_FILE="avfoundation:Capture screen 0" uv run server.py
```

> **Tip (macOS):** Run `ffmpeg -f avfoundation -list_devices true -i ""` to see available device names and indices.

The same server code handles all sources — FFmpeg abstracts the input, server just reads frames and streams them.

### Stream quality

Use `VIDEO_QUALITY` to control the overall broadcast quality with a single value from `0` to `1`. This adjusts **both** resolution and JPEG compression together — the easiest way to reduce bandwidth.

| Quality | Resolution      | JPEG        | Use case           |
| ------- | --------------- | ----------- | ------------------ |
| `1.0`   | 100% (original) | Best (2)    | LAN / best quality |
| `0.75`  | ~81%            | Good (9)    | Balanced           |
| `0.5`   | ~63%            | Medium (16) | Remote viewers     |
| `0.25`  | ~44%            | Low (24)    | Low bandwidth      |
| `0.0`   | 25%             | Worst (31)  | Minimal bandwidth  |

```bash
# Best quality (original resolution, best JPEG)
VIDEO_QUALITY=1 uv run server.py

# Balanced (good for most cases)
VIDEO_QUALITY=0.75 uv run server.py

# Low bandwidth (smaller + more compressed)
VIDEO_QUALITY=0.5 uv run server.py

# Works with any source
VIDEO_QUALITY=0.5 VIDEO_FILE="avfoundation:0" uv run server.py
```

For fine-grained control, use `VIDEO_RESOLUTION` and `JPEG_QUALITY` separately instead:

```bash
# Explicit resolution (e.g., 720p)
VIDEO_RESOLUTION="1280x720" uv run server.py

# Explicit JPEG compression (2=best, 31=worst)
JPEG_QUALITY=10 uv run server.py
```

If nothing is set, the original source quality is used.

## API

| Endpoint | Description                                               |
| -------- | --------------------------------------------------------- |
| `/`      | Web player with controls (play/pause, volume, fullscreen) |
| `/live`  | Raw MJPEG stream — embed with `<img src="/live">`         |
| `/audio` | Raw PCM audio stream (WAV format)                         |
| `/stats` | JSON stats: clients, frames, sync drift                   |

## How it works

```
                                              ┌─── Client 1 (browser)
                                              │
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Video Source │────▶│   FFmpeg    │────▶│ Broadcaster │──┼─── Client 2 (browser)
│ (any input) │     │ (1 process) │     │ (fan-out)   │  │
└─────────────┘     └─────────────┘     └─────────────┘  │
                                              └─── Client N (browser)
```

1. **FFmpeg** runs as a **single process** — decodes the source, re-encodes to MJPEG frames + PCM audio
2. **Broadcaster** reads frames from that one process and fans them out to all connected clients
3. **Each client** gets an async queue — the same frame bytes are pushed to every viewer
4. **Browser** renders MJPEG in an `<img>` tag, plays audio via Web Audio API

### One FFmpeg, many clients

The expensive work — decoding, scaling, JPEG compression — happens **once**, regardless of how many clients are watching. Whether 1 viewer or 100, there is still only one FFmpeg process running.

```
Processing cost:  O(1)  — fixed, doesn't grow with clients
Network cost:     O(n)  — each client gets a copy of the frame bytes
```

Adding more viewers only increases network I/O (sending the same bytes to each connection), which is cheap. The CPU-heavy video processing never duplicates. This is the core advantage of the broadcast architecture.

If a client falls behind (slow connection), the server drops frames for that client via backpressure — without affecting other viewers or the FFmpeg pipeline.

### Why HTTP instead of WebRTC?

This is a **broadcast model** — server sends to clients, not peer-to-peer.

|           | HTTP (this project)       | WebRTC                     |
| --------- | ------------------------- | -------------------------- |
| Direction | One-way (server → client) | Two-way                    |
| Latency   | 1-3 seconds               | < 150ms                    |
| Setup     | Open URL                  | STUN/TURN, ICE negotiation |
| Use case  | Watching streams          | Video calls                |

For one-way broadcast where slight delay is acceptable, HTTP is simpler and works everywhere.

### Why ASGI?

Traditional WSGI (Flask, Django) uses one thread per request. For streaming, connections stay open for minutes or hours — you'd run out of threads fast.

ASGI with asyncio lets thousands of connections share a single thread. While one client waits for the next frame, others can receive data. Essential for long-lived streaming connections.

## Configuration

Environment variables:

| Variable           | Default      | Description                                                              |
| ------------------ | ------------ | ------------------------------------------------------------------------ |
| `VIDEO_FILE`       | `video.mp4`  | Path to video file, device, or stream URL                                |
| `VIDEO_FPS`        | `30`         | Output frame rate                                                        |
| `VIDEO_QUALITY`    | *(none)*     | Overall quality 0–1 (1=best). Controls resolution + JPEG together        |
| `VIDEO_RESOLUTION` | *(original)* | Explicit resolution, e.g. `1280x720` (ignored if `VIDEO_QUALITY` is set) |
| `JPEG_QUALITY`     | `5`          | MJPEG quality 2–31, lower=better (ignored if `VIDEO_QUALITY` is set)     |
| `GROWING_FILE`     | `false`      | Set to `1` for files being actively written (e.g., OBS)                  |
| `HOST`             | `0.0.0.0`    | Server bind address                                                      |
| `PORT`             | `8000`       | Server port                                                              |
| `MAX_CLIENTS`      | `100`        | Maximum concurrent viewers                                               |

## Project structure

```
├── main.py              # ASGI application entry point
├── server.py            # Uvicorn runner + graceful shutdown
├── lib/
│   ├── config.py        # Configuration + source type detection
│   ├── wav.py           # WAV header generation for audio
│   └── templates.py     # HTML/JS web player
└── services/
    ├── broadcaster.py   # FFmpeg process management
    ├── connection.py    # Client queue + backpressure
    └── handlers.py      # HTTP endpoint handlers
```

## Key insights

### HTTP is a streaming protocol

Most people think HTTP = request → response → close. But streaming shows HTTP can be a continuous transport channel:

1. Open connection once
2. Keep sending data forever
3. Client keeps rendering

**Streaming isn't a special protocol — it's controlled buffering over long-lived HTTP connections.** This is the same principle behind Server-Sent Events, HTTP/2 push, and chunked transfer encoding.

### Process once, deliver many

The most common mistake in building a streaming server is spawning a new FFmpeg process per client. That means if 50 people watch, you decode and re-encode the video 50 times — destroying CPU and memory.

This server runs **one FFmpeg process total**. The broadcaster reads from that single pipe and copies the frame bytes into each client's queue. The heavy work (decode → scale → compress) happens once. Only the lightweight part (copying bytes to N sockets) scales with viewers.

```
50 clients, naive approach:    50 × FFmpeg = 50× CPU
50 clients, this server:        1 × FFmpeg = 1× CPU
```

## Known limitations

- Audio may drift out of sync over long sessions (monitor via `/stats`)
- No seeking or pause (it's live, not VOD)
- Not tested beyond ~10 concurrent clients
- No HTTPS (add via reverse proxy like nginx)
- **macOS camera/microphone**: Requires granting permissions to Terminal in System Settings > Privacy & Security
- **Reload mode + cameras**: When using `--reload`, camera processes may not stop on Ctrl+C. Use `RELOAD=false` for camera sources:
  ```bash
  RELOAD=false VIDEO_FILE="avfoundation:0" uv run server.py
  ```

## License

MIT
