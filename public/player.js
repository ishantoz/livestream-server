/**
 * Live Stream Player
 *
 * Fetches fMP4 via HTTP, decodes with MediaSource Extensions,
 * renders audio through Web Audio API GainNode.
 */

(() => {
  "use strict";

  const HIDE_DELAY = 3500;

  // ── State ───────────────────────────────────────────────
  let isStreaming = false;
  let isMuted = true;
  let volume = 70;
  let hideTimeout = null;
  let streamReader = null;
  let streamStartAt = null;

  let audioCtx = null;
  let gainNode = null;

  let uptimeInterval = null;
  let bufferInterval = null;

  // Ambient light
  let ambientRAF = null;
  let ambientCtx = null;

  // ── DOM ─────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  const video = $("video");
  const overlay = $("overlay");
  const overlayText = $("overlayText");
  const overlayBtn = $("overlayBtn");
  const controls = $("controls");
  const playerWrapper = $("playerWrapper");
  const playerGlow = $("playerGlow");
  const playIcon = $("playIcon");
  const pauseIcon = $("pauseIcon");
  const volumeIcon = $("volumeIcon");
  const muteIcon = $("muteIcon");
  const expandIcon = $("expandIcon");
  const compressIcon = $("compressIcon");
  const liveBadge = $("liveBadge");
  const offlineBadge = $("offlineBadge");
  const headerDot = $("headerDot");
  const headerStatus = $("headerStatus");
  const statusDot = $("statusDot");
  const statusText = $("statusText");
  const volumeSlider = $("volume");
  const clockEl = $("clock");
  const uptimeEl = $("uptimeText");
  const bufferEl = $("bufferText");
  const liveDurationEl = $("liveDuration");
  const ambientCanvas = $("ambientCanvas");

  // ── Ambient Light ───────────────────────────────────────
  // Draws the video onto a tiny canvas at ~10 fps.
  // CSS blur + saturate on the canvas creates the glow.

  function initAmbient() {
    if (ambientCtx) return;
    ambientCtx = ambientCanvas.getContext("2d", { willReadFrequently: false });
  }

  function startAmbient() {
    initAmbient();
    ambientCanvas.classList.add("active");

    let lastDraw = 0;
    const INTERVAL = 100; // ~10 fps — plenty for a glow effect

    function draw(ts) {
      ambientRAF = requestAnimationFrame(draw);
      if (ts - lastDraw < INTERVAL) return;
      lastDraw = ts;

      if (video.readyState >= 2 && video.videoWidth > 0) {
        ambientCtx.drawImage(
          video,
          0,
          0,
          ambientCanvas.width,
          ambientCanvas.height,
        );
      }
    }
    ambientRAF = requestAnimationFrame(draw);
  }

  function stopAmbient() {
    if (ambientRAF) {
      cancelAnimationFrame(ambientRAF);
      ambientRAF = null;
    }
    ambientCanvas.classList.remove("active");
  }

  // ── Clock ───────────────────────────────────────────────
  function updateClock() {
    const now = new Date();
    clockEl.textContent = now.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }
  updateClock();
  setInterval(updateClock, 30000);

  // ── Audio ───────────────────────────────────────────────
  function setupAudio() {
    if (audioCtx) return;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaElementSource(video);
      gainNode = audioCtx.createGain();
      source.connect(gainNode);
      gainNode.connect(audioCtx.destination);
      gainNode.gain.value = 0;
    } catch (e) {
      console.error("Audio setup failed:", e);
    }
  }

  function resumeAudio() {
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
  }

  // ── Telemetry ───────────────────────────────────────────
  function fmt(ms) {
    const s = Math.floor(ms / 1000);
    const h = String(Math.floor(s / 3600)).padStart(2, "0");
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const sec = String(s % 60).padStart(2, "0");
    return `${h}:${m}:${sec}`;
  }

  function startTelemetry() {
    streamStartAt = Date.now();

    uptimeInterval = setInterval(() => {
      if (!streamStartAt) return;
      const elapsed = Date.now() - streamStartAt;
      uptimeEl.textContent = fmt(elapsed);
      liveDurationEl.textContent = fmt(elapsed);
    }, 1000);

    bufferInterval = setInterval(() => {
      if (!video || video.readyState < 1) {
        bufferEl.textContent = "--";
        return;
      }
      const b = video.buffered;
      if (b.length > 0) {
        const ahead = +(b.end(b.length - 1) - video.currentTime).toFixed(1);
        if (ahead > 3) {
          bufferEl.textContent = "Good";
          bufferEl.style.color = "#34d399";
        } else if (ahead > 1) {
          bufferEl.textContent = ahead + "s";
          bufferEl.style.color = "#fbbf24";
        } else {
          bufferEl.textContent = "Low";
          bufferEl.style.color = "#f87171";
        }
      } else {
        bufferEl.textContent = "Empty";
        bufferEl.style.color = "";
      }
    }, 500);
  }

  function stopTelemetry() {
    clearInterval(uptimeInterval);
    clearInterval(bufferInterval);
    streamStartAt = null;
    uptimeEl.textContent = "--:--:--";
    bufferEl.textContent = "--";
    bufferEl.style.color = "";
    liveDurationEl.textContent = "00:00:00";
  }

  // ── Stream ───────────────────────────────────────────────
  // Two playback paths:
  //   1. MSE (desktop): fetch + ReadableStream + SourceBuffer — buffer control
  //   2. Direct src (mobile/fallback): <video src="/stream"> — native fMP4
  // Server outputs valid fMP4 so both work. Direct src is more reliable
  // on mobile where MSE + fetch streaming is flaky or unsupported.

  function canUseMSE() {
    if (typeof MediaSource === "undefined") return false;
    if (typeof ReadableStream === "undefined") return false;
    // Mobile browsers: prefer direct src — MSE + fetch is unreliable
    if (/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)) return false;
    // Check codec support (level 3.1 = 42C01F, level 3.0 = 42C01E)
    const codecs = [
      'video/mp4; codecs="avc1.42C01F,mp4a.40.2"',
      'video/mp4; codecs="avc1.42C01F"',
      'video/mp4; codecs="avc1.42C01E,mp4a.40.2"',
      'video/mp4; codecs="avc1.42C01E"',
    ];
    return codecs.some((c) => MediaSource.isTypeSupported(c));
  }

  async function startStream() {
    if (isStreaming && !video.paused) return; // truly playing — skip
    isStreaming = true;
    setStatus("connecting", "Connecting…");
    updateUI();
    setupAudio();

    if (canUseMSE()) {
      startMSE();
    } else {
      startDirect();
    }
  }

  // ── Path 1: Direct <video src> (mobile + fallback) ──────
  // Point the video at /stream. Browser's native MP4 demuxer handles
  // the fMP4 stream. Works on ALL browsers including iOS Safari.

  function startDirect() {
    // Only set src if not already pointing at /stream
    if (!video.src || !video.src.endsWith("/stream")) {
      video.src = "/stream";
    }
    video.muted = true;
    video.play().then(() => {
      // playing — UI updates via the "playing" event listener
    }).catch(() => {
      // Autoplay blocked (mobile) — reset state so overlay tap works
      isStreaming = false;
      overlayText.textContent = "Tap to play";
      updateUI();
    });
  }

  // ── Path 2: MSE + fetch (desktop) ───────────────────────

  function startMSE() {
    const ms = new MediaSource();
    ms.addEventListener("sourceopen", async () => {
      const codecs = [
        'video/mp4; codecs="avc1.42C01F,mp4a.40.2"',
        'video/mp4; codecs="avc1.42C01F"',
        'video/mp4; codecs="avc1.42C01E,mp4a.40.2"',
        'video/mp4; codecs="avc1.42C01E"',
      ];
      let sb;
      for (const c of codecs) {
        if (MediaSource.isTypeSupported(c)) {
          try { sb = ms.addSourceBuffer(c); break; } catch (_) { /* skip */ }
        }
      }
      if (!sb) {
        // MSE failed — fall back to direct src
        console.warn("MSE codec unsupported, falling back to direct src");
        startDirect();
        return;
      }
      sb.mode = "sequence";

      try {
        const res = await fetch("/stream");
        if (!res.ok) throw new Error("HTTP " + res.status);

        streamReader = res.body.getReader();
        video.play().catch(() => { overlayText.textContent = "Tap to play"; });

        let appends = 0;
        while (true) {
          const { done, value } = await streamReader.read();
          if (done) break;

          if (sb.updating)
            await new Promise((r) => sb.addEventListener("updateend", r, { once: true }));

          try {
            sb.appendBuffer(value);
          } catch (e) {
            if (e.name === "QuotaExceededError") {
              if (sb.updating)
                await new Promise((r) => sb.addEventListener("updateend", r, { once: true }));
              if (sb.buffered.length > 0) {
                sb.remove(0, Math.max(0, video.currentTime - 2));
                await new Promise((r) => sb.addEventListener("updateend", r, { once: true }));
              }
              sb.appendBuffer(value);
            } else throw e;
          }

          if (++appends % 50 === 0 && sb.buffered.length > 0) {
            const ct = video.currentTime;
            if (ct > 8) {
              if (sb.updating)
                await new Promise((r) => sb.addEventListener("updateend", r, { once: true }));
              try {
                sb.remove(0, ct - 5);
                await new Promise((r) => sb.addEventListener("updateend", r, { once: true }));
              } catch (_) { /* ignore */ }
            }
          }
        }
      } catch (e) {
        if (e.name !== "AbortError") console.error("Stream error:", e);
      }

      if (isStreaming) {
        setStatus("disconnected", "Stream ended");
        isStreaming = false;
        streamReader = null;
        updateUI();
        stopTelemetry();
        stopAmbient();
      }
    });

    video.src = URL.createObjectURL(ms);
    video.muted = true;
  }

  function stopStream() {
    isStreaming = false;
    if (streamReader) {
      streamReader.cancel().catch(() => {});
      streamReader = null;
    }
    const src = video.src;
    video.pause();
    video.removeAttribute("src");
    video.load();
    if (src && src.startsWith("blob:")) URL.revokeObjectURL(src);
    setStatus("disconnected", "Offline");
    updateUI();
    stopTelemetry();
    stopAmbient();
  }

  function togglePlay() {
    resumeAudio();
    if (!isStreaming) startStream();
    else if (video.paused) video.play().catch(() => {});
    else stopStream();
  }

  // ── Video Events ────────────────────────────────────────
  video.addEventListener("playing", () => {
    isStreaming = true;
    if (!isMuted && gainNode) {
      video.muted = false;
      gainNode.gain.value = volume / 100;
      resumeAudio();
    }
    setStatus("connected", "Streaming");
    updateUI();
    if (!streamStartAt) startTelemetry();
    startAmbient();
  });

  video.addEventListener("waiting", () =>
    setStatus("connecting", "Buffering…"),
  );
  video.addEventListener("pause", () => {
    if (isStreaming) setStatus("connecting", "Paused");
  });
  video.addEventListener("stalled", () =>
    setStatus("connecting", "Buffering…"),
  );

  video.addEventListener("error", () => {
    const err = video.error;
    if (err && err.code !== MediaError.MEDIA_ERR_ABORTED) {
      console.error("Video error:", err.code, err.message);
      setStatus("disconnected", "Stream error");
      isStreaming = false;
      streamReader = null;
      updateUI();
      stopTelemetry();
      stopAmbient();
    }
  });

  // ── UI ──────────────────────────────────────────────────
  function setStatus(state, text) {
    statusDot.className = "status-dot " + state;
    statusText.textContent = text;

    // Header pill
    if (headerDot && headerStatus) {
      headerDot.className = "w-1.5 h-1.5 rounded-full";
      headerStatus.textContent = text;
      if (state === "connected") {
        headerDot.classList.add("bg-ok");
        headerStatus.style.color = "#34d399";
      } else if (state === "connecting") {
        headerDot.classList.add("bg-warn");
        headerStatus.style.color = "#fbbf24";
      } else {
        headerDot.classList.add("bg-zinc-600");
        headerStatus.style.color = "";
      }
    }
  }

  function updateUI() {
    const playing = isStreaming && !video.paused;

    overlay.classList.toggle("overlay-hidden", isStreaming);
    playIcon.classList.toggle("hidden", playing);
    pauseIcon.classList.toggle("hidden", !playing);
    liveBadge.style.display = isStreaming ? "flex" : "none";
    offlineBadge.style.display = isStreaming ? "none" : "flex";
    playerGlow.classList.toggle("player-glow-on", isStreaming);
    playerGlow.classList.toggle("player-glow-off", !isStreaming);
    liveDurationEl.classList.toggle("hidden", !isStreaming);

    if (playing) showControls();
    else {
      controls.classList.add("visible");
      clearTimeout(hideTimeout);
    }
  }

  function showControls() {
    controls.classList.add("visible");
    playerWrapper.classList.remove("hide-cursor");
    clearTimeout(hideTimeout);
    if (isStreaming && !video.paused) {
      hideTimeout = setTimeout(() => {
        controls.classList.remove("visible");
        playerWrapper.classList.add("hide-cursor");
      }, HIDE_DELAY);
    }
  }

  // ── Volume ──────────────────────────────────────────────
  function setVolume(v) {
    volume = +v;
    resumeAudio();
    if (v > 0 && isMuted) {
      isMuted = false;
      video.muted = false;
    }
    if (gainNode) gainNode.gain.value = isMuted ? 0 : v / 100;
    updateVolumeIcon();
  }

  function updateVolumeIcon() {
    const muted = isMuted || volume === 0;
    volumeIcon.classList.toggle("hidden", muted);
    muteIcon.classList.toggle("hidden", !muted);
  }

  function toggleMute() {
    resumeAudio();
    isMuted = !isMuted;
    video.muted = isMuted;
    if (gainNode) gainNode.gain.value = isMuted ? 0 : volume / 100;
    updateVolumeIcon();
  }

  // ── Fullscreen ──────────────────────────────────────────
  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      (
        playerWrapper.requestFullscreen ||
        playerWrapper.webkitRequestFullscreen ||
        playerWrapper.msRequestFullscreen
      ).call(playerWrapper);
    } else {
      (
        document.exitFullscreen ||
        document.webkitExitFullscreen ||
        document.msExitFullscreen
      ).call(document);
    }
  }

  function updateFsIcon() {
    const fs = !!document.fullscreenElement;
    expandIcon.classList.toggle("hidden", fs);
    compressIcon.classList.toggle("hidden", !fs);
  }

  document.addEventListener("fullscreenchange", updateFsIcon);
  document.addEventListener("webkitfullscreenchange", updateFsIcon);

  // ── Events ──────────────────────────────────────────────
  // Overlay tap: always force-start (reset stuck state from failed autoplay)
  overlayBtn.addEventListener("click", () => {
    isStreaming = false;
    resumeAudio();
    startStream();
  });
  $("playBtn").addEventListener("click", togglePlay);
  $("muteBtn").addEventListener("click", toggleMute);
  $("fsBtn").addEventListener("click", toggleFullscreen);
  volumeSlider.addEventListener("input", (e) => setVolume(e.target.value));

  document.addEventListener("keydown", (e) => {
    if (e.code === "Space") {
      e.preventDefault();
      togglePlay();
    } else if (e.code === "KeyM") toggleMute();
    else if (e.code === "KeyF") toggleFullscreen();
    else if (e.code === "ArrowUp") {
      e.preventDefault();
      setVolume(Math.min(100, volume + 10));
      volumeSlider.value = volume;
    } else if (e.code === "ArrowDown") {
      e.preventDefault();
      setVolume(Math.max(0, volume - 10));
      volumeSlider.value = volume;
    }
  });

  // Mouse / touch
  playerWrapper.addEventListener("mousemove", showControls);
  playerWrapper.addEventListener("mouseleave", () => {
    if (isStreaming) controls.classList.remove("visible");
  });
  controls.addEventListener("mouseenter", () => clearTimeout(hideTimeout));
  controls.addEventListener("mouseleave", () => {
    if (isStreaming) {
      hideTimeout = setTimeout(() => {
        controls.classList.remove("visible");
        playerWrapper.classList.add("hide-cursor");
      }, HIDE_DELAY);
    }
  });

  // Touch: tap player to toggle controls
  let lastTap = 0;
  playerWrapper.addEventListener(
    "touchstart",
    (e) => {
      if (e.target.closest(".ctrl-btn, .volume-slider, #overlayBtn")) return;
      const now = Date.now();
      if (now - lastTap < 300) {
        toggleFullscreen();
        e.preventDefault();
        return;
      }
      lastTap = now;
      if (controls.classList.contains("visible")) {
        controls.classList.remove("visible");
      } else {
        showControls();
      }
    },
    { passive: false },
  );

  // Auto-start (desktop only — mobile requires user gesture)
  window.addEventListener("load", () => {
    if (!/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)) {
      startStream();
    }
  });
})();
