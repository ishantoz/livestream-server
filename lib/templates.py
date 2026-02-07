def get_player_html() -> str:
    """Return the HTML for the video player interface."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Stream</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body { 
            background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 100%);
            min-height: 100vh;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #fff;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 10px;
        }
        
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        
        .logo {
            font-size: 24px;
            font-weight: 700;
            background: linear-gradient(90deg, #4a9eff, #a855f7);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .live-badge {
            display: none;
            align-items: center;
            gap: 8px;
            background: rgba(239, 68, 68, 0.2);
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 14px;
            color: #ef4444;
        }
        
        .live-dot {
            width: 8px;
            height: 8px;
            background: #ef4444;
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.5; transform: scale(1.2); }
        }
        
        .player-wrapper {
            position: relative;
            background: #000;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
            aspect-ratio: 16 / 9;
            max-height: calc(100vh - 80px);
        }
        
        #video {
            width: 100%;
            height: 100%;
            display: block;
            object-fit: contain;
            background: #000;
        }
        
        .controls {
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            background: linear-gradient(transparent, rgba(0,0,0,0.9));
            padding: 60px 24px 24px;
            display: flex;
            align-items: center;
            gap: 20px;
            opacity: 0;
            transition: opacity 0.3s;
            pointer-events: none;
        }
        
        .controls.visible {
            opacity: 1;
            pointer-events: auto;
        }
        
        .player-wrapper.hide-cursor { cursor: none; }
        
        .btn {
            width: 36px;
            height: 36px;
            aspect-ratio: 1;
            flex-shrink: 0;
            border-radius: 50%;
            border: none;
            background: rgba(255,255,255,0.15);
            backdrop-filter: blur(10px);
            color: #fff;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
        }
        
        .btn:hover {
            background: rgba(255,255,255,0.25);
            transform: scale(1.05);
        }
        
        .btn-primary { background: #4a9eff; }
        .btn-primary:hover { background: #3a8eef; }
        .btn svg { width: 18px; height: 18px; fill: currentColor; }
        
        .volume-wrapper {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-left: auto;
        }
        
        .volume-slider {
            -webkit-appearance: none;
            width: 100px;
            height: 4px;
            background: rgba(255,255,255,0.2);
            border-radius: 2px;
            cursor: pointer;
        }
        
        .volume-slider::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 14px;
            height: 14px;
            background: #fff;
            border-radius: 50%;
            cursor: pointer;
        }
        
        .status-bar {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 16px;
            margin-top: 24px;
            padding: 16px;
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
        }
        
        .status-text {
            color: rgba(255,255,255,0.6);
            font-size: 14px;
        }
        
        .status-indicator { display: flex; align-items: center; gap: 8px; }
        .status-indicator.connected { color: #22c55e; }
        .status-indicator.disconnected { color: #ef4444; }
        .status-indicator.connecting { color: #eab308; }
        
        .overlay {
            position: absolute;
            inset: 0;
            background: rgba(0,0,0,0.7);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 20px;
            transition: opacity 0.3s;
        }
        
        .overlay.hidden { opacity: 0; pointer-events: none; }
        
        .overlay-btn {
            width: 64px;
            height: 64px;
            aspect-ratio: 1;
            border-radius: 50%;
            border: none;
            background: #4a9eff;
            color: #fff;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s;
            box-shadow: 0 10px 40px rgba(74, 158, 255, 0.4);
        }
        
        .overlay-btn:hover { transform: scale(1.1); background: #3a8eef; }
        .overlay-btn svg { width: 28px; height: 28px; fill: currentColor; margin-left: 3px; }
        .overlay-text { color: rgba(255,255,255,0.8); font-size: 18px; }
        
        .player-wrapper:fullscreen,
        .player-wrapper:-webkit-full-screen {
            background: #000;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .player-wrapper:fullscreen #video,
        .player-wrapper:-webkit-full-screen #video {
            max-height: 100vh;
            width: auto;
            max-width: 100vw;
        }
        
        .player-wrapper:fullscreen .controls { position: fixed; }
        .player-wrapper:fullscreen .overlay { position: fixed; }
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <div class="logo">LiveStream</div>
            <div class="live-badge" id="liveBadge">
                <div class="live-dot"></div>
                <span>LIVE</span>
            </div>
        </header>
        
        <div class="player-wrapper">
            <img id="video" alt="" />
            
            <div class="overlay" id="overlay">
                <button class="overlay-btn" onclick="togglePlay()">
                    <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                </button>
                <div class="overlay-text">Click to start stream</div>
            </div>
            
            <div class="controls">
                <button class="btn btn-primary" onclick="togglePlay()">
                    <svg id="playIcon" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                    <svg id="pauseIcon" viewBox="0 0 24 24" style="display:none"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                </button>
                
                <button class="btn" onclick="toggleMute()">
                    <svg id="volumeIcon" viewBox="0 0 24 24"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>
                    <svg id="muteIcon" viewBox="0 0 24 24" style="display:none"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>
                </button>
                
                <div class="volume-wrapper">
                    <input type="range" class="volume-slider" id="volume" min="0" max="100" value="70" oninput="setVolume(this.value)">
                </div>
                
                <button class="btn" onclick="toggleFullscreen()">
                    <svg id="expandIcon" viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
                    <svg id="compressIcon" viewBox="0 0 24 24" style="display:none"><path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>
                </button>
            </div>
        </div>
        
        <div class="status-bar">
            <div class="status-indicator disconnected" id="statusIndicator">
                <span class="status-text" id="statusText">Ready to stream</span>
            </div>
        </div>
    </div>
    
    <script>
        const HIDE_DELAY = 3000;
        let audioCtx = null, gainNode = null, isPlaying = false, isMuted = false;
        let abortCtrl = null, volume = 70, hideTimeout = null;
        
        const $ = id => document.getElementById(id);
        const $$ = sel => document.querySelector(sel);
        
        function showControls() {
            const c = $$('.controls'), p = $$('.player-wrapper');
            c.classList.add('visible');
            p.classList.remove('hide-cursor');
            clearTimeout(hideTimeout);
            if (isPlaying) hideTimeout = setTimeout(() => {
                c.classList.remove('visible');
                p.classList.add('hide-cursor');
            }, HIDE_DELAY);
        }
        
        function updateUI() {
            const playing = isPlaying;
            $('overlay').classList.toggle('hidden', playing);
            $('playIcon').style.display = playing ? 'none' : 'block';
            $('pauseIcon').style.display = playing ? 'block' : 'none';
            $('liveBadge').style.display = playing ? 'flex' : 'none';
            $('statusIndicator').className = 'status-indicator ' + (playing ? 'connected' : 'disconnected');
            $('statusText').textContent = playing ? 'Connected - Streaming' : 'Disconnected';
            if (playing) showControls();
            else { $$('.controls').classList.add('visible'); clearTimeout(hideTimeout); }
        }
        
        function setVolume(v) {
            volume = +v;
            if (gainNode && !isMuted) gainNode.gain.value = v / 100;
            updateVolumeIcon();
        }
        
        function updateVolumeIcon() {
            const muted = isMuted || volume === 0;
            $('volumeIcon').style.display = muted ? 'none' : 'block';
            $('muteIcon').style.display = muted ? 'block' : 'none';
        }
        
        function toggleMute() {
            isMuted = !isMuted;
            if (gainNode) gainNode.gain.value = isMuted ? 0 : volume / 100;
            updateVolumeIcon();
        }
        
        async function togglePlay() {
            isPlaying ? stopStream() : await startStream();
        }
        
        function stopStream() {
            isPlaying = false;
            
            // Capture current frame as still image before disconnecting
            const video = $('video');
            if (video.src && video.naturalWidth > 0) {
                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = video.naturalWidth;
                    canvas.height = video.naturalHeight;
                    canvas.getContext('2d').drawImage(video, 0, 0);
                    video.src = canvas.toDataURL('image/jpeg', 0.95);
                } catch (e) {
                    // If canvas capture fails, just keep last src (will stop when connection closes)
                }
            }
            
            abortCtrl?.abort();
            audioCtx?.close();
            audioCtx = gainNode = null;
            updateUI();
        }
        
        async function startStream() {
            isPlaying = true;
            let hasAudio = true;
            updateUI();
            $('statusIndicator').className = 'status-indicator connecting';
            $('statusText').textContent = 'Connecting...';
            
            // Start video immediately
            $('video').src = '/live?' + Date.now();
            
            // Try to start audio (optional - may not be available for camera sources)
            try {
                audioCtx = new AudioContext({ sampleRate: 44100 });
                gainNode = audioCtx.createGain();
                gainNode.gain.value = isMuted ? 0 : volume / 100;
                gainNode.connect(audioCtx.destination);
                
                abortCtrl = new AbortController();
                const res = await fetch('/audio', { signal: abortCtrl.signal });
                
                // Check if we got actual audio data
                if (!res.ok || !res.body) {
                    throw new Error('No audio stream available');
                }
                
                const reader = res.body.getReader();
                
                let hdrSkipped = false, hdrBuf = new Uint8Array(0);
                let nextTime = audioCtx.currentTime + 0.1;
                let gotAudioData = false;
                
                updateUI();
                
                while (isPlaying) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    
                    // If we read but get no data after header, audio might be disabled
                    if (!gotAudioData && value && value.length > 0) {
                        gotAudioData = true;
                    }
                    
                    let data = value;
                    if (!hdrSkipped) {
                        hdrBuf = new Uint8Array([...hdrBuf, ...value]);
                        if (hdrBuf.length >= 44) { data = hdrBuf.slice(44); hdrSkipped = true; }
                        else continue;
                    }
                    
                    if (data.length < 4) continue;
                    
                    const samples = Math.floor(data.length / 4);
                    const buf = audioCtx.createBuffer(2, samples, 44100);
                    const L = buf.getChannelData(0), R = buf.getChannelData(1);
                    const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
                    
                    for (let i = 0; i < samples; i++) {
                        L[i] = view.getInt16(i * 4, true) / 32768;
                        R[i] = view.getInt16(i * 4 + 2, true) / 32768;
                    }
                    
                    const src = audioCtx.createBufferSource();
                    src.buffer = buf;
                    src.connect(gainNode);
                    if (nextTime < audioCtx.currentTime) nextTime = audioCtx.currentTime + 0.05;
                    src.start(nextTime);
                    nextTime += buf.duration;
                }
            } catch (e) {
                // Audio failed - continue with video only
                if (e.name !== 'AbortError') {
                    console.debug('Audio unavailable (video-only mode):', e.message);
                    hasAudio = false;
                    // Don't show error - just indicate video-only mode
                    if (isPlaying) {
                        $('statusText').textContent = 'Connected (video only)';
                    }
                }
            }
        }
        
        function toggleFullscreen() {
            const p = $$('.player-wrapper');
            if (!document.fullscreenElement) {
                (p.requestFullscreen || p.webkitRequestFullscreen || p.msRequestFullscreen).call(p);
            } else {
                (document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen).call(document);
            }
        }
        
        function updateFsIcon() {
            const fs = !!document.fullscreenElement;
            $('expandIcon').style.display = fs ? 'none' : 'block';
            $('compressIcon').style.display = fs ? 'block' : 'none';
        }
        
        document.addEventListener('fullscreenchange', updateFsIcon);
        document.addEventListener('webkitfullscreenchange', updateFsIcon);
        
        document.addEventListener('keydown', e => {
            if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
            else if (e.code === 'KeyM') toggleMute();
            else if (e.code === 'KeyF') toggleFullscreen();
            else if (e.code === 'ArrowUp') { e.preventDefault(); setVolume(Math.min(100, volume + 10)); $('volume').value = volume; }
            else if (e.code === 'ArrowDown') { e.preventDefault(); setVolume(Math.max(0, volume - 10)); $('volume').value = volume; }
        });
        
        $$('.player-wrapper').addEventListener('mousemove', showControls);
        $$('.player-wrapper').addEventListener('mouseleave', () => isPlaying && $$('.controls').classList.remove('visible'));
        $$('.controls').addEventListener('mouseenter', () => clearTimeout(hideTimeout));
        $$('.controls').addEventListener('mouseleave', () => isPlaying && (hideTimeout = setTimeout(() => {
            $$('.controls').classList.remove('visible');
            $$('.player-wrapper').classList.add('hide-cursor');
        }, HIDE_DELAY)));
        
        // Auto-play on page load
        window.addEventListener('load', () => startStream());
    </script>
</body>
</html>'''
