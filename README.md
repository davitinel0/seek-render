# seek-render

Render any HTML canvas animation to an MP4, frame-exact, with motion blur, using headless Chrome and the browser's own H.264 encoder (WebCodecs). Fast on a normal desktop GPU.

It is the renderer behind a one-person studio that publishes long documentaries (30–60 min) and short vertical reels, all animated in code. On a Radeon RX 580, a reel that took 1,167 s in software mode renders in 45 s with the hardware encoder, with identical images.

## How it works

1. Your page draws into `<canvas id="c">` and exposes `window.seek(t)`: a **pure function of time**. Same `t`, same picture, no matter the order of calls.
2. The renderer opens the page in Chrome, calls `seek()` for every frame, and averages several sub-frames per frame for **motion blur** (360° shutter). Fewer samples right after a hard cut, more on fast camera moves, if your page tells it where cuts and camera speed are (optional hooks below).
3. The canvas is encoded **inside the page** with WebCodecs H.264 (hardware when available), streamed to Node in chunks, and wrapped into MP4 by ffmpeg **without re-encoding**.
4. Encoding is GOP-safe: chunks of each 2-second GOP are held until it flushes cleanly. If the hardware encoder fails mid-render (it happens), only that GOP is redone, falling back to the software encoder for it.

No screen recording, no dropped frames, no real-time constraint: a 40-minute film renders the same way as a 6-second clip.

## Install

Requirements: Node 18+, Google Chrome installed (for the GPU path), ffmpeg on the PATH.

```
npm install
npx playwright install chromium   # only needed for --software
```

## Usage

```
node render.mjs examples/demo.html demo.mp4 --fps 30 --sub 4
```

| Option | Default | What it does |
|---|---|---|
| `--fps N` | 60 | Output frame rate |
| `--sub N` | 4 | Sub-frames averaged per frame (motion blur). 1 = off |
| `--from s` / `--to s` | 0 / end | Render only a time window (useful to fix one part of a long film) |
| `--vert` | off | Native 9:16 (1080×1920) instead of 16:9 (1920×1080); the page gets `?vert=1` |
| `--map edit.json` | — | Render an **edit** of the film: `{"segs": [[t0, t1], ...], "hold": 1.5}` plays the segments in order with hard cuts, then holds the last frame |
| `--software` | off | Bundled Chromium + software encoder (slow, but works anywhere) |
| `--angle X` | `d3d11` on Windows | ANGLE backend for the GPU path |

## The page contract

Required:
- `<canvas id="c">` sized to the output (1920×1080, or 1080×1920 with `?vert=1`)
- `window.seek(t)` — draw the frame at time `t` (seconds)
- `window.DURATION` — total length in seconds

Optional hooks:
- `window.ready` — a Promise the renderer awaits before the first frame (fonts, images)
- `window.prepareFrame(t)` — async, called before each `seek(t)`; use it to seek `<video>` elements to the exact frame when you draw real footage into the canvas
- `window.cutIn(t0, t1)` — return the time of a hard cut inside `[t0, t1)`, or `null`; that frame is then not blended across two shots
- `window.camSpeed(t, dt)` — how fast the camera moves around `t`; faster moves get more sub-frames

## QA helper

`tools/lumacheck.py video.mp4 [step]` samples the encoded file and flags near-black frames. Bugs that build up over a long render (an overlay that accumulates between frames, a GPU that drops out halfway) often do not show in previews, only in the final file.

## License

MIT
