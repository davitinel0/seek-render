"""Final audio mix for a narrated video: voice + music bed ducked under the voice + sound effects, normalised to
-14 LUFS (the YouTube / streaming reference) with true peak under -1 dBTP.

What it does:
  1. Places the sound effects on one track. Each event comes from a JSON list (below). Unless the event fixes them,
     every placement gets a random ±1.5 dB gain and 0.95-1.05 pitch, so a repeated sound is never the identical take,
     and is cut at "dur" seconds (default 2.0) with a 0.35 s fade, so a long sample cannot spill past its scene.
  2. Ducks the music under the voice with ffmpeg's sidechaincompress. Two profiles:
       short (<= 5 min, reels/Shorts): music x0.73, threshold 0.03, ratio 6, attack 40 ms, release 600 ms
       long  (>  5 min):               music x4.0,  threshold 0.006, ratio 8, attack 30 ms, release 1500 ms
     The long profile ducks harder and releases slowly, so the bed does not jump back up in every breath between
     sentences (a fast release makes the music swell in each pause, which viewers hear as "music too loud"). The
     pre-gains were calibrated on synthesized beds peaking around -6 dBFS; adjust with --music-gain-db for yours.
  3. Measures how many dB the ducked music sits under the voice while speaking (RMS over the spoken lines, from
     --timings, or from the voice's own activity) and prints it; 10-14 dB under is a comfortable target for
     documentaries, ~15-16 dB for very calm ones. Written to mix_check.json in the stems folder.
  4. Two-pass loudnorm to --lufs (default -14), then a 4x-oversampled limiter: linear loudnorm can push peaks above
     0 dBFS, the limiter keeps the true peak under -1 dBTP. Reports the final integrated loudness and true peak.

Sound effects JSON (--sfx): a list of events. "file" is relative to the JSON's folder (or absolute). The time is
either "t" (ABSOLUTE seconds in the video) or, with --timings from voice/align.py, "line" + "word" (+ "off"):
    [{"file": "sfx/hit-01.wav", "t": 0.0, "gain": 0.6},
     {"file": "sfx/low-whoosh-02.wav", "line": 3, "word": "bridge", "off": -0.2, "gain": 0.4},
     {"file": "rain.wav", "t": 12.0, "dur": 9.0, "gain": 0.12, "pitch": 1.0, "pan": -0.3}]
Anchoring on a word is safer than absolute times: when the narration is re-timed, the effect follows the word.

Usage:
  python audio/mix.py --voice voice.wav --out final.mp4 --video video_silent.mp4 [--music music.wav] [--sfx sfx.json]
                      [--timings timings.json] [--profile auto|short|long] [--music-gain-db 0] [--loop-music]
                      [--lufs -14] [--stems DIR] [--seed 11]
Without --video the output is audio only: .wav (PCM) or .m4a/.aac (AAC 320k). Requires ffmpeg on the PATH.
"""
import argparse, json, re, subprocess, sys, tempfile, unicodedata
from pathlib import Path
import numpy as np, soundfile as sf

SR = 48000


def load(p, sr=SR):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True)
    if r.returncode: sys.exit(f"cannot read {p}: {r.stderr.decode(errors='ignore')}")
    return np.frombuffer(r.stdout, np.float32).reshape(-1, 2).copy()


def duration(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def nrm(w):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFD", str(w).lower()).encode("ascii", "ignore").decode())


def word_time(line, word):
    """Start of the first word in the line that matches `word` (prefix match, accents and punctuation ignored)."""
    k = nrm(word)
    for w in line["words"]:
        x = nrm(w["w"])
        if x == k or (len(k) >= 4 and x.startswith(k[:max(3, len(k) - 1)])): return w["s"]
    return None


def place(trk, x, t0, gain=1.0, pitch=1.0, pan=None, dur=None, fade=0.35):
    """Add sample x (float32 [n, 2]) to the stereo track at t0 seconds. pitch = speed factor (1.1 = 10% higher and
    shorter). dur cuts it with a short fade-out. pan -1..1 (equal-power). Whatever falls outside the track is dropped."""
    if pitch != 1.0 and len(x) > 32:
        pos = np.arange(0, len(x) - 1, pitch); x = np.stack([np.interp(pos, np.arange(len(x)), x[:, c]) for c in range(2)], 1)
    if dur is not None:
        x = np.array(x[: int(dur * SR)], np.float32); f = min(len(x), int(fade * SR))
        if f: x[-f:] *= np.linspace(1, 0, f)[:, None]
    if pan is not None:
        m = x.mean(1); ang = (float(np.clip(pan, -1, 1)) + 1) * np.pi / 4; x = np.stack([m * np.cos(ang), m * np.sin(ang)], 1)
    s = int(round(t0 * SR)); a0 = max(0, -s); s = max(0, s); L = min(len(x) - a0, len(trk) - s)
    if L > 0: trk[s:s + L] += x[a0:a0 + L] * gain


def sfx_track(events, base, timings, total, seed, vary):
    out = np.zeros((int(total * SR) + 1, 2), np.float32); rng = np.random.default_rng(seed); cache = {}; ok = miss = 0
    for e in events:
        t0 = e.get("t")
        if t0 is None and timings and "line" in e and "word" in e:
            wt = word_time(timings["lines"][int(e["line"])], e["word"])
            t0 = None if wt is None else wt + e.get("off", 0.0)
        if t0 is None:
            print(f"  skipped (no time / word not found): {e}"); miss += 1; continue
        p = Path(e["file"]); p = p if p.is_absolute() else base / p
        if not p.exists(): print(f"  skipped (missing file): {p}"); miss += 1; continue
        if p not in cache: cache[p] = load(p)
        g = e.get("gain", 0.5) * (10 ** (rng.uniform(-1.5, 1.5) / 20) if vary else 1.0)
        pitch = e.get("pitch", float(rng.uniform(0.95, 1.05)) if vary else 1.0)
        place(out, cache[p], t0, g, pitch, e.get("pan"), e.get("dur", 2.0)); ok += 1
    print(f"sfx: {ok} placed, {miss} skipped")
    return out


def speech_mask(n, timings, voice):
    idx = np.zeros(n, bool)
    if timings:
        for l in timings["lines"]: idx[int(l["start"] * SR):int(l["end"] * SR)] = True
    else:   # voice activity: 20 ms frames within 30 dB of the loudest frame
        hop = int(0.02 * SR); fr = voice[: n // hop * hop].reshape(-1, hop); db = 10 * np.log10((fr ** 2).mean(1) + 1e-12)
        idx[: len(db) * hop] = np.repeat(db > db.max() - 30, hop)
    return idx


def main():
    ap = argparse.ArgumentParser(description="Voice + ducked music + sfx, loudness-normalised to -14 LUFS.")
    ap.add_argument("--voice", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--video", help="silent (or any) video: its picture is copied, its audio replaced")
    ap.add_argument("--music"); ap.add_argument("--sfx", help="JSON list of sound-effect events (see the docstring)")
    ap.add_argument("--timings", help="timings JSON from voice/align.py (word anchors + speech regions for the level check)")
    ap.add_argument("--profile", choices=["auto", "short", "long"], default="auto", help="auto = long when over 5 minutes")
    ap.add_argument("--music-gain-db", type=float, default=0.0, help="extra music gain before ducking")
    ap.add_argument("--loop-music", action="store_true", help="loop the music file if it is shorter than the voice")
    ap.add_argument("--lufs", type=float, default=-14.0); ap.add_argument("--tp", type=float, default=-1.0)
    ap.add_argument("--stems", help="keep the intermediate stems (sfx track, ducked music, premix) in this folder")
    ap.add_argument("--seed", type=int, default=11); ap.add_argument("--no-vary", action="store_true", help="no random gain/pitch on sfx")
    a = ap.parse_args()

    timings = json.loads(Path(a.timings).read_text("utf-8")) if a.timings else None
    total = duration(a.video) if a.video else (timings["total"] if timings else duration(a.voice))
    long_ = a.profile == "long" or (a.profile == "auto" and total > 300)
    vol, duck = (4.0, "threshold=0.006:ratio=8:attack=30:release=1500:knee=4") if long_ else \
                (0.73, "threshold=0.03:ratio=6:attack=40:release=600:knee=4")
    vol *= 10 ** (a.music_gain_db / 20)
    tmp = tempfile.TemporaryDirectory(); work = Path(a.stems) if a.stems else Path(tmp.name); work.mkdir(parents=True, exist_ok=True)

    events = json.loads(Path(a.sfx).read_text("utf-8")) if a.sfx else []
    fx = sfx_track(events, Path(a.sfx).resolve().parent if a.sfx else Path("."), timings, total, a.seed, not a.no_vary)
    sfx = work / "sfx_track.wav"; sf.write(sfx, fx, SR)

    music_in = (["-stream_loop", "-1", "-i", a.music] if a.loop_music else ["-i", a.music]) if a.music else \
               ["-f", "lavfi", "-t", f"{total:.3f}", "-i", f"anullsrc=r={SR}:cl=stereo"]
    graph = ("[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0,apad,asplit=3[v][vsc][vm];"      # mono voice centred; padded to the video length
             f"[1:a]aresample=48000,volume={vol:.4f}[m];"
             f"[m][vsc]sidechaincompress={duck},asplit=2[md][mdm];"
             "[2:a]aresample=48000[fx];"
             f"[v][md][fx]amix=inputs=3:normalize=0:duration=first,atrim=0:{total:.3f}[mix];"
             f"[mdm]atrim=0:{total:.3f}[mdo];[vm]atrim=0:{total:.3f}[vmo]")
    pre, mdw, vw = work / "premix.wav", work / "music_ducked.wav", work / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", a.voice, *music_in, "-i", str(sfx), "-filter_complex", graph,
                    "-map", "[mix]", str(pre), "-map", "[mdo]", str(mdw), "-map", "[vmo]", str(vw)], check=True)

    if a.music:   # music level under speech, relative to the voice
        vv = load(vw).mean(1); mm = load(mdw).mean(1); n = min(len(vv), len(mm)); idx = speech_mask(n, timings, vv[:n])
        db = lambda x: 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
        gap = db(vv[:n][idx]) - db(mm[:n][idx])
        (work / "mix_check.json").write_text(json.dumps({"music_under_voice_db": round(float(gap), 1), "profile": "long" if long_ else "short"}), "utf-8")
        print(f"music under the voice: {gap:.1f} dB ({'long' if long_ else 'short'} profile)")

    # loudnorm pass 1 (measure) + pass 2 (linear) to the target
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(pre), "-af", f"loudnorm=I={a.lufs}:TP={a.tp}:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr).group(0))
    ln = (f"loudnorm=I={a.lufs}:TP={a.tp}:LRA=11:measured_I={{input_i}}:measured_TP={{input_tp}}:measured_LRA={{input_lra}}:"
          "measured_thresh={input_thresh}:offset={target_offset}:linear=true").format(**m)
    lim = 10 ** ((a.tp - 1.0) / 20)   # limiter ceiling 1 dB under the true-peak target (inter-sample margin)
    af = ln + f",aresample=192000,alimiter=limit={lim:.3f}:attack=1:release=60:level=false,aresample=48000"
    ext = Path(a.out).suffix.lower()
    if a.video:
        cmd = ["-i", a.video, "-i", str(pre), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", af,
               "-c:a", "aac", "-b:a", "320k", "-shortest", "-movflags", "+faststart"]
    elif ext == ".wav":
        cmd = ["-i", str(pre), "-af", af, "-c:a", "pcm_s16le"]
    else:
        cmd = ["-i", str(pre), "-af", af, "-c:a", "aac", "-b:a", "320k"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", *cmd, a.out], check=True)
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", a.out, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    I = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1]; P = re.findall(r"Peak:\s+(-?[\d.inf]+) dBFS", r.stderr)
    print(f"final: {I} LUFS integrated, true peak {P[-1] if P else '?'} dBTP -> {a.out}")
    tmp.cleanup()


if __name__ == "__main__":
    main()
