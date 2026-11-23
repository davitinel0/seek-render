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

## Toolkit

Small command-line tools around the renderer, for narrated videos made in code: voice timing, subtitles, mixing, pre-render gates and topic research. Each file is standalone and documents its options in its header (`--help` works on every Python tool).

Requirements: ffmpeg and ffprobe on the PATH, Python 3.10+ with `pip install -r requirements.txt` (each tool only needs the packages it imports).

| Tool | What it does | Usage |
|---|---|---|
| `tools/strip.mjs` | Filmstrip of a seekable page: frames from t0 to t1, tiled into one JPEG with time stamps | `node tools/strip.mjs page.html strip.jpg 0 30 --fps 2 --cols 8` |
| `tools/lumacheck.py` | Flags near-black frames in the encoded file | `python tools/lumacheck.py video.mp4 3` |
| `tools/check_hook.py` | Gate for the first seconds of a short video's narration (start, pace, pauses) | `python tools/check_hook.py timings.json` |
| `tools/check_description.py` | Gate for a YouTube description: length, `<` `>`, chapter rules | `python tools/check_description.py description.md` |
| `voice/align.py` | Word timestamps of a narration (faster-whisper), grouped into script lines | `python voice/align.py voice.wav timings.json --script script.txt --language en` |
| `voice/make_srt.py` | `.srt` subtitles from the timings | `python voice/make_srt.py timings.json subs.srt --script-text` |
| `voice/tighten_pauses.py` | Shortens pauses between lines, measured on the audio, and shifts the timings | `python voice/tighten_pauses.py voice.wav timings.json out.wav out.json --max 0.3` |
| `audio/mix.py` | Voice + ducked music + sound effects, two-pass loudnorm to -14 LUFS, true peak under -1 dBTP | `python audio/mix.py --voice voice.wav --music music.wav --sfx sfx.json --video silent.mp4 --out final.mp4` |
| `audio/cinematic_hits.py` | Synthesizes low impacts and whooshes that still read on phone speakers | `python audio/cinematic_hits.py sfx/ --variants 3` |
| `research/channel_outliers.py` | Ranks a channel's videos by views / channel median | `python research/channel_outliers.py @handle --limit 200` |
| `research/new_outlier_channels.py` | Finds channels whose first upload is recent and that already have a breakout video | `python research/new_outlier_channels.py --q "phones" --days 30` |

A typical narrated video: record or synthesize `voice.wav` → `voice/align.py` → `voice/tighten_pauses.py` (optional) → `tools/check_hook.py` for shorts → build the page from the timings → `tools/strip.mjs` to judge motion → `render.mjs` → `audio/mix.py` → `tools/lumacheck.py` → `voice/make_srt.py` → `tools/check_description.py` before upload.

### tools/

QA that runs before or after a render. `strip.mjs` uses the same page contract as `render.mjs` (it takes `--vert` and `--software` too). A single still cannot show whether a shot moves; a strip at 2 fps can: if frames of one shot look identical, the shot is static. `lumacheck.py` samples the encoded file: bugs that build up over a long render (an overlay that accumulates between frames, a GPU that drops out halfway) often do not show in previews, only in the final file. The two `check_*` gates exit with code 1 on failure, so they can stop a script or a CI job.

### voice/

All three tools share one timings format, written by `align.py`:

```json
{"total": 61.2,
 "lines": [{"i": 0, "text": "Line as written in the script.", "start": 0.12, "end": 3.40,
            "words": [{"w": "Line", "s": 0.12, "e": 0.31}]}]}
```

`align.py` takes `--model` as a faster-whisper model name (`small` by default, downloaded on first use) or a local model folder, and `--language` (auto-detected if omitted; set it, or numbers and short lines can be recognised in the wrong language). Lines can come from a script file (one line per line), from known audio spans (`--spans`, when the narration is one clip per line), or from Whisper's own segments. `tighten_pauses.py` finds the real end of each line in the signal, not from Whisper's word ends, which are often early and would clip the last syllable.

### audio/

`mix.py` takes a voice file, an optional music bed, an optional JSON list of sound effects and an optional silent video. Effects are placed at absolute times or anchored to a word of a line in the timings, so they follow the narration when it is re-timed. Ducking has a short-video and a long-video profile (the long one releases slowly so the music does not swell back in every breath). It reports how many dB the music sits under the voice; 10–14 dB is a comfortable range for documentaries. `cinematic_hits.py` writes its sounds as WAV files you can list in the effects JSON.

### research/

Topic research with yt-dlp, no API key and no login. Ranking by views divided by the channel's median, rather than by raw views, puts small and big channels on one scale and surfaces topics and packaging that already worked in a niche. Both tools cache what they fetch, so reruns are cheap. Respect YouTube's terms and keep request volume low; `new_outlier_channels.py` waits 1 s between requests.

## License

MIT
