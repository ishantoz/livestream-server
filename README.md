# livestream-server

A lightweight HTTP-based video streaming server built from scratch in Python.

> **Learning project** — Built to understand how video streaming works at the protocol level. Experimental, not for production use.

Stream video from any source — files, live cameras, screen recordings — to any browser. No plugins, no complex setup. HTTP streaming + MSE playback.

## Features

- **Works with any video source** — MP4 files, RTSP cameras, Raspberry Pi camera, OBS recordings, HTTP streams, or any growing video file
- **Single FFmpeg, unlimited viewers** — Video processing happens once, then fans out to all clients. 1 viewer or 100, same CPU cost
- **Perfect audio/video sync** — Single muxed stream (H.264 + AAC in fMP4), browser handles sync natively
- **HTTP streaming + MSE** — fMP4 chunks streamed over HTTP, decoded via MediaSource Extensions for proper audio/video sync
- **Software encoding (libx264)** — Uses `libx264` with `ultrafast` preset, works on any machine — no GPU or special hardware required
- **Multiple concurrent clients** — Async architecture handles many viewers without blocking
- **Zero client setup** — Open URL in any modern browser, streaming starts immediately
- **Adjustable quality** — Single `VIDEO_QUALITY` knob (0–1) controls resolution + compression together
- **Backpressure handling** — Automatically drops chunks for slow clients to prevent memory bloat
- **Monitoring** — Built-in `/stats` endpoint tracks stream health

## Use cases

**Good for:**
- IP cameras / CCTV monitoring
- Raspberry Pi or embedded device streams
- Internal dashboards and live monitoring
- Screen sharing within local network
- Streaming HTTP/HLS video sources to browsers
- Learning how streaming protocols work

**Not ideal for:**
- Video calls (needs sub-150ms latency)
- Public internet streaming (no CDN, no adaptive bitrate)
- Mobile networks (no reconnection handling)

## Quick start

### Requirements

- Python 3.12+
- FFmpeg (with libx264 and AAC support)

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

# HTTP/HLS stream (auto-detected as live, browser User-Agent sent)
VIDEO_FILE="https://example.com/live/stream.m3u8" uv run server.py

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

The same server code handles all sources — FFmpeg abstracts the input, server just reads fMP4 chunks and streams them.

### Stream quality

Use `VIDEO_QUALITY` to control the overall broadcast quality with a single value from `0` to `1`. This adjusts **both** resolution and H.264 compression (CRF) together.

| Quality | Resolution      | CRF | Use case           |
| ------- | --------------- | --- | ------------------ |
| `1.0`   | 100% (original) | 18  | LAN / best quality |
| `0.75`  | ~81%            | 24  | Balanced           |
| `0.5`   | ~63%            | 29  | Remote viewers     |
| `0.25`  | ~44%            | 35  | Low bandwidth      |
| `0.0`   | 25%             | 40  | Minimal bandwidth  |

```bash
# Best quality
VIDEO_QUALITY=1 uv run server.py

# Balanced
VIDEO_QUALITY=0.75 uv run server.py

# Low bandwidth
VIDEO_QUALITY=0.5 uv run server.py

# Works with any source
VIDEO_QUALITY=0.5 VIDEO_FILE="avfoundation:0" uv run server.py
```

For fine-grained control, use `VIDEO_RESOLUTION` and `VIDEO_CRF` separately:

```bash
# Explicit resolution (e.g., 720p)
VIDEO_RESOLUTION="1280x720" uv run server.py

# Explicit H.264 CRF (0=lossless, 51=worst, default 23)
VIDEO_CRF=28 uv run server.py
```

## API

| Endpoint  | Description                                               |
| --------- | --------------------------------------------------------- |
| `/`       | Web player with controls (play/pause, volume, fullscreen) |
| `/stream` | HTTP stream — muxed fMP4 (init segment + media chunks)    |
| `/stats`  | JSON stats: clients, chunks sent, stream health           |

## How it works

```
                                              ┌─── Client 1 (browser)
                                              │
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Video Source │────▶│   FFmpeg    │────▶│ Broadcaster │──┼─── Client 2 (browser)
│ (any input) │     │ (1 process) │     │   (HTTP)    │  │
└─────────────┘     └─────────────┘     └─────────────┘  │
                    H.264 + AAC                └─── Client N (browser)
                    in fMP4
```

1. **FFmpeg** decodes any source, re-encodes to H.264 video + AAC audio in fragmented MP4 using `libx264` (software encoding)
2. **Broadcaster** reads raw fMP4 chunks from FFmpeg's stdout, caches the init segment
3. **HTTP stream** sends muxed fMP4 bytes to each connected browser client via `/stream`
4. **Browser** uses `fetch()` to read the HTTP stream and **MSE** (MediaSource Extensions) to decode — audio and video are **synced by the browser** automatically. MSE's `sequence` mode ensures every client starts at time 0 regardless of when they join

### Why this is better than MJPEG + PCM

The previous architecture sent MJPEG frames and raw PCM audio as two separate HTTP streams. This had issues:

|                  | Old (MJPEG + PCM)        | New (HTTP + fMP4)       |
| ---------------- | ------------------------ | ----------------------- |
| Connections      | 2 (video + audio)        | 1 (HTTP stream)         |
| FFmpeg processes | 2 (or FIFO workaround)   | 1                       |
| A/V sync         | Manual wall-clock pacing | Native browser sync     |
| Video codec      | MJPEG (10-50x larger)    | H.264 (efficient)       |
| Audio codec      | Raw PCM (uncompressed)   | AAC (compressed)        |
| Client code      | Web Audio API + img tag  | MSE + `<video>` element |

### Software encoding (libx264)

The server uses `libx264` software encoding with the `ultrafast` preset and `zerolatency` tune. This works on **any machine** — no GPU, no special drivers, no platform-specific setup. Just install FFmpeg and go.

- **Baseline profile** — maximum browser compatibility (every browser supports it)
- **`ultrafast` preset** — minimal CPU usage, prioritizes speed over compression efficiency
- **`zerolatency` tune** — eliminates encoding latency for real-time streaming
- **CRF-based quality** — controllable via `VIDEO_QUALITY` or `VIDEO_CRF`

### One FFmpeg, many clients

The expensive work — decoding, H.264 encoding, AAC encoding — happens **once**, regardless of how many clients are watching.

```
Processing cost:  O(1)  — fixed, doesn't grow with clients
Network cost:     O(n)  — each client gets a copy of the chunk bytes
```

If a client falls behind (slow connection), the server drops chunks for that client via backpressure — without affecting other viewers or the FFmpeg pipeline.

### Why HTTP + MSE (not raw `<video src>`)?

The server sends a continuous HTTP response with `Content-Type: video/mp4` and no `Content-Length`. The client uses `fetch()` to read this stream and feeds raw bytes into a MediaSource Extensions (MSE) SourceBuffer.

Why not just `<video src="/stream">`? Because the browser's native MP4 parser can't handle joining a live fMP4 stream mid-way — it expects timestamps to start at 0. MSE in `sequence` mode remaps timestamps automatically, so every client starts at 0 regardless of when they connect. MSE also handles audio decoding properly for streaming fMP4.

The MSE code is minimal (~80 lines) — just `fetch` + `appendBuffer` in a loop. No WebSocket, no complex state management.

### Why ASGI?

Traditional WSGI (Flask, Django) uses one thread per request. For streaming, connections stay open for minutes or hours — you'd run out of threads fast.

ASGI with asyncio lets thousands of connections share a single thread. While one client waits for the next chunk, others can receive data. Essential for long-lived streaming connections.

## Configuration

Environment variables:

| Variable           | Default      | Description                                                              |
| ------------------ | ------------ | ------------------------------------------------------------------------ |
| `VIDEO_FILE`       | `video.mp4`  | Path to video file, device, or stream URL                                |
| `VIDEO_FPS`        | `30`         | Output frame rate                                                        |
| `VIDEO_QUALITY`    | *(none)*     | Overall quality 0–1 (1=best). Controls CRF + resolution together         |
| `VIDEO_CRF`        | `23`         | H.264 CRF 0–51, lower=better (ignored if `VIDEO_QUALITY` is set)         |
| `VIDEO_RESOLUTION` | *(original)* | Explicit resolution, e.g. `1280x720` (ignored if `VIDEO_QUALITY` is set) |
| `AUDIO_BITRATE`    | `128k`       | AAC audio bitrate                                                        |
| `GROWING_FILE`     | `false`      | Set to `1` for files being actively written (e.g., OBS)                  |
| `HOST`             | `0.0.0.0`    | Server bind address                                                      |
| `PORT`             | `8000`       | Server port                                                              |
| `MAX_CLIENTS`      | `100`        | Maximum concurrent viewers                                               |

## Project structure

```
├── main.py              # ASGI application entry point + routing
├── server.py            # Uvicorn runner + graceful shutdown
├── lib/
│   ├── config.py        # Configuration + source type detection
│   └── templates.py     # HTML/JS web player (MSE + fetch)
└── services/
    ├── broadcaster.py   # FFmpeg process management (fMP4 output, libx264)
    ├── connection.py    # Client queue + backpressure + init segment cache
    └── handlers.py      # HTTP stream + web endpoint handlers
```

## Key insights

### Muxed streaming beats split streams

Sending audio and video as separate streams means you have to synchronize them yourself — and getting that right is hard. By muxing them into a single fMP4 container, the browser's media pipeline handles sync natively. One connection, one FFmpeg process, zero sync code.

### Process once, deliver many

The most common mistake in building a streaming server is spawning a new FFmpeg process per client. That means if 50 people watch, you decode and re-encode the video 50 times.

This server runs **one FFmpeg process total**. The broadcaster reads from that single pipe and copies the chunk bytes into each client's queue. The heavy work (decode → encode → mux) happens once.

```
50 clients, naive approach:    50 × FFmpeg = 50× CPU
50 clients, this server:        1 × FFmpeg = 1× CPU
```

### The init segment pattern

Fragmented MP4 has a crucial property: the **init segment** (containing `ftyp` and `moov` atoms) describes the stream format but contains no media data. It must be sent to each client **before** any media chunks. The server caches this init segment and sends it to every new HTTP streaming client on connect. After that, only media chunks (containing `moof` and `mdat` atoms) are streamed.

## Known limitations

- **Requires modern browser** — MSE support needed (Chrome 23+, Firefox 42+, Safari 8+, Edge 12+)
- No seeking (it's live, not VOD)
- Not tested beyond ~10 concurrent clients
- No HTTPS (add via reverse proxy like nginx)
- **macOS camera/microphone**: Requires granting permissions to Terminal in System Settings > Privacy & Security
- **Reload mode + cameras**: When using `--reload`, camera processes may not stop on Ctrl+C. Use `RELOAD=false` for camera sources:
  ```bash
  RELOAD=false VIDEO_FILE="avfoundation:0" uv run server.py
  ```

## License

MIT
