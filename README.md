# livestream-server

A lightweight HTTP video streaming server built from scratch in Python.

> **Learning project** — Built to understand how video streaming works at the protocol level.

Stream video from any source — files, cameras, RTSP feeds, screen capture — to any browser. No plugins, no complex setup. One FFmpeg process, unlimited viewers.

## Features

- **Any video source** — MP4 files, RTSP cameras, USB cameras, screen capture, HTTP streams, growing files (OBS)
- **One FFmpeg, unlimited viewers** — Encode once, fan out to all clients. 1 viewer or 100, same CPU cost
- **Perfect A/V sync** — Single muxed fMP4 stream (H.264 + AAC), browser handles sync natively
- **Desktop + mobile** — MSE playback on desktop, native fMP4 fallback on mobile (iOS Safari, Android)
- **Ambient light UI** — Modern player with video-reactive ambient glow, glassmorphism, responsive design
- **Software encoding** — `libx264` ultrafast preset, works on any machine without GPU
- **Backpressure** — Slow clients get chunks dropped automatically, no memory bloat
- **Monitoring** — `/stats` endpoint for stream health

## Quick start

### Requirements

- Python 3.12+
- FFmpeg (with libx264 + AAC)

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

# Or pip
pip install -r requirements.txt
python server.py
```

Open `http://localhost:8000` in your browser.

## Video sources

The server auto-detects source types and configures FFmpeg accordingly:

| Source type      | Detection                                       | Behavior                             |
| ---------------- | ----------------------------------------------- | ------------------------------------ |
| **File**         | Default                                         | Loops infinitely, paced at real-time |
| **RTSP/HTTP**    | `rtsp://`, `http://`, etc.                      | No loop, no pacing (already live)    |
| **Camera**       | `/dev/video*`, `avfoundation:*` (name or index) | No loop, no pacing                   |
| **Growing file** | `GROWING_FILE=1` env var                        | Paced, no loop                       |

```bash
# Local file (loops forever)
VIDEO_FILE=video.mp4 uv run server.py

# RTSP camera
VIDEO_FILE=rtsp://192.168.1.100:554/stream uv run server.py

# HTTP/HLS stream
VIDEO_FILE="https://example.com/live/stream.m3u8" uv run server.py

# USB camera (Linux)
VIDEO_FILE=/dev/video0 uv run server.py

# macOS camera (by name or index)
VIDEO_FILE="avfoundation:0" uv run server.py
VIDEO_FILE="avfoundation:OBS Virtual Camera" uv run server.py

# macOS camera + microphone
VIDEO_FILE="avfoundation:0:0" uv run server.py

# macOS screen capture
VIDEO_FILE="avfoundation:Capture screen 0" uv run server.py

# OBS recording in progress
GROWING_FILE=1 VIDEO_FILE=/path/to/obs-recording.mkv uv run server.py
```

> **Tip (macOS):** Run `ffmpeg -f avfoundation -list_devices true -i ""` to list available devices.

### Stream quality

`VIDEO_QUALITY` controls resolution and compression together (0 to 1):

| Quality | Resolution      | CRF | Use case         |
| ------- | --------------- | --- | ---------------- |
| `1.0`   | 100% (original) | 18  | LAN, best quality |
| `0.75`  | ~81%            | 24  | Balanced         |
| `0.5`   | ~63%            | 29  | Remote viewers   |
| `0.25`  | ~44%            | 35  | Low bandwidth    |
| `0.0`   | 25%             | 40  | Minimal          |

```bash
VIDEO_QUALITY=0.75 uv run server.py

# Or fine-grained control
VIDEO_CRF=28 VIDEO_RESOLUTION=1280x720 uv run server.py
```

## API

| Endpoint     | Description                                    |
| ------------ | ---------------------------------------------- |
| `/`          | Web player (responsive, ambient glow, controls) |
| `/stream`    | HTTP fMP4 stream (init segment + media chunks) |
| `/stats`     | JSON stats (clients, chunks, stream health)    |
| `/player.js` | Player JavaScript                              |
| `/player.css`| Player styles                                  |

## How it works

```
                                            ┌─── Client 1 (browser)
                                            │
┌─────────────┐     ┌──────────┐     ┌──────────────┐
│ Video Source │────▶│  FFmpeg  │────▶│ Broadcaster  │──┼─── Client 2 (browser)
│ (any input) │     │(1 process)│    │    (HTTP)    │  │
└─────────────┘     └──────────┘     └──────────────┘  │
                    H.264 + AAC               └─── Client N (browser)
                    in fMP4
```

1. **FFmpeg** decodes any source, re-encodes to H.264 + AAC in fragmented MP4 (`libx264` software encoding)
2. **Broadcaster** reads fMP4 chunks from FFmpeg stdout, caches the init segment (ftyp + moov)
3. **HTTP handler** sends init segment + media chunks to each browser client via `/stream`
4. **Browser** plays the stream using one of two paths:
   - **Desktop**: `fetch()` + MSE SourceBuffer in `sequence` mode — full buffer control
   - **Mobile**: direct `<video src="/stream">` — browser's native fMP4 demuxer handles it

### Why fMP4 over MJPEG + PCM

|                  | Old (MJPEG + PCM)        | Current (HTTP + fMP4)   |
| ---------------- | ------------------------ | ----------------------- |
| Connections      | 2 (video + audio)        | 1 (muxed stream)        |
| FFmpeg processes | 2                        | 1                       |
| A/V sync         | Manual wall-clock pacing | Native browser sync     |
| Video codec      | MJPEG (10–50x larger)    | H.264 (efficient)       |
| Audio codec      | Raw PCM (uncompressed)   | AAC (compressed)        |
| Client code      | Web Audio API + img tag  | MSE or native `<video>` |

### Encoding

- **Baseline profile, level 3.1** — maximum browser compatibility, supports up to 1080p@30fps
- **`ultrafast` preset** — minimal CPU, prioritizes speed over compression
- **`zerolatency` tune** — no encoding latency
- **`yuv420p` pixel format** — required by all mobile hardware decoders
- **CRF quality** — adjustable via `VIDEO_QUALITY` or `VIDEO_CRF`

### One FFmpeg, many clients

The expensive work (decode + encode + mux) happens once:

```
50 clients, naive:  50 × FFmpeg = 50× CPU
50 clients, here:    1 × FFmpeg =  1× CPU
```

Slow clients get chunks dropped via backpressure — without affecting others.

### The init segment pattern

Fragmented MP4 splits into an **init segment** (ftyp + moov — stream metadata, no media data) and **media segments** (moof + mdat — actual frames). The server caches the init segment and sends it first to every new client, then streams media chunks. This lets clients join mid-stream.

### Why MSE on desktop, direct src on mobile

MSE (MediaSource Extensions) with `sequence` mode remaps timestamps so every client starts at time 0 — ideal for live streams. But MSE + `fetch().body.getReader()` is unreliable on mobile Safari and some Android browsers.

The fallback: `<video src="/stream">` works on all mobile browsers because the server outputs valid fMP4 with proper headers. The browser's native MP4 demuxer handles it directly.

### Why ASGI

WSGI (Flask, Django) uses one thread per request. Streaming connections stay open for minutes — you'd run out of threads. ASGI with asyncio handles thousands of long-lived connections on a single thread.

## Configuration

| Variable           | Default     | Description                                                      |
| ------------------ | ----------- | ---------------------------------------------------------------- |
| `VIDEO_FILE`       | `video.mp4` | Path to video file, device, or stream URL                        |
| `VIDEO_FPS`        | `30`        | Output frame rate                                                |
| `VIDEO_QUALITY`    | —           | Overall quality 0–1 (controls CRF + resolution together)        |
| `VIDEO_CRF`        | `23`        | H.264 CRF 0–51, lower = better (ignored if `VIDEO_QUALITY` set) |
| `VIDEO_RESOLUTION` | (original)  | e.g. `1280x720` (ignored if `VIDEO_QUALITY` set)                |
| `AUDIO_BITRATE`    | `128k`      | AAC audio bitrate                                                |
| `GROWING_FILE`     | `false`     | Set `1` for files being actively written (OBS)                   |
| `HOST`             | `0.0.0.0`  | Server bind address                                              |
| `PORT`             | `8000`      | Server port                                                      |
| `MAX_CLIENTS`      | `100`       | Maximum concurrent viewers                                       |

## Project structure

```
├── main.py                # ASGI app — routing + static file serving
├── server.py              # Uvicorn runner + graceful shutdown
├── public/                # Static frontend (served directly)
│   ├── index.html         # Player UI (Tailwind CDN, responsive)
│   ├── player.js          # MSE + direct-src playback, ambient light, telemetry
│   └── player.css         # Styles, animations, glassmorphism
├── lib/
│   ├── config.py          # Configuration + source type detection
│   └── templates.py       # (legacy, unused — replaced by public/)
└── services/
    ├── broadcaster.py     # FFmpeg process management, fMP4 output
    ├── connection.py      # Client queues, backpressure, init segment cache
    └── handlers.py        # HTTP stream handler + stats endpoint
```

## Known limitations

- **Modern browser required** — MSE (Chrome 23+, Firefox 42+, Safari 8+, Edge 12+) or native fMP4 support
- No seeking (live only, not VOD)
- No HTTPS (use a reverse proxy like nginx)
- Not tested beyond ~10 concurrent clients
- **macOS camera/mic**: Requires Terminal permissions in System Settings > Privacy & Security
- **Reload + cameras**: Camera processes may not stop on Ctrl+C with `--reload`. Use `RELOAD=false`:
  ```bash
  RELOAD=false VIDEO_FILE="avfoundation:0" uv run server.py
  ```

## License

MIT
