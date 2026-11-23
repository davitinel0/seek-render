"""Shorten the pauses between narration lines without clipping the end of any line.

Cutting pauses from aligner word times alone clips voices: Whisper's word ends are often early, so the tail of the
last word (and its breath or reverb) falls inside the cut. This tool finds the real edges in the AUDIO instead:
  - after each line's last word it walks forward until the signal stays below --threshold-db (default -42 dBFS) for
    120 ms, the true end of the voice, and keeps --keep-after (0.15 s) after it;
  - before the next line it walks back from its first word the same way and keeps --keep-before (0.08 s) before the onset;
  - only the silence left in between is shortened to --max seconds (half removed on each side of its middle).
Optionally --tempo (0.9-1.12) speeds up or slows down the whole voice afterwards (ffmpeg atempo, pitch kept).
All word and line times are shifted to match, so subtitles and cues stay in sync. Every cut is printed.

Usage:
  python voice/tighten_pauses.py voice.wav timings.json out.wav out_timings.json [--max 0.3] [--tempo 1.0]
                                 [--threshold-db -42] [--keep-after 0.15] [--keep-before 0.08]
                                 [--spans spans.json --out-spans out_spans.json]
timings.json is the format written by voice/align.py. Input files are never overwritten unless you pass the same path.
"""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path
import numpy as np, soundfile as sf


def main():
    ap = argparse.ArgumentParser(description="Shorten pauses between narration lines, measured on the audio.")
    ap.add_argument("wav"); ap.add_argument("timings"); ap.add_argument("out_wav"); ap.add_argument("out_timings")
    ap.add_argument("--max", type=float, default=0.3, help="longest pause kept between lines, seconds (default 0.3)")
    ap.add_argument("--tempo", type=float, default=1.0, help="speed factor applied afterwards, 0.9-1.12 (pitch kept)")
    ap.add_argument("--threshold-db", type=float, default=-42.0, help="level under which the signal counts as silence")
    ap.add_argument("--keep-after", type=float, default=0.15); ap.add_argument("--keep-before", type=float, default=0.08)
    ap.add_argument("--spans"); ap.add_argument("--out-spans")
    a = ap.parse_args()
    if not 0.9 <= a.tempo <= 1.12: sys.exit("--tempo must be between 0.9 and 1.12 (beyond that the voice sounds processed)")

    A, sr = sf.read(a.wav, dtype="float32"); T = json.loads(Path(a.timings).read_text("utf-8"))
    mono = A if A.ndim == 1 else A.mean(1)
    hop = int(0.01 * sr)   # 10 ms envelope, 20 ms window
    env = np.array([np.sqrt(np.mean(mono[i:i + 2 * hop] ** 2)) + 1e-9 for i in range(0, len(mono), hop)])
    quiet = 20 * np.log10(env) < a.threshold_db
    hold = 12   # 120 ms of silence

    def tail_end(t):   # first moment after t where it stays quiet for 120 ms
        i = int(t * 100)
        while i < len(quiet) - hold and not quiet[i:i + hold].all(): i += 1
        return i / 100

    def onset(t):      # last moment before t where it was quiet for 120 ms
        i = int(t * 100)
        while i > hold and not quiet[i - hold:i].all(): i -= 1
        return i / 100

    L = T["lines"]; cuts = []
    for x, y in zip(L, L[1:]):
        e = min(tail_end(x["end"]) + a.keep_after, y["start"]); s = max(onset(y["start"]) - a.keep_before, e)
        gap = s - e
        if gap > a.max:
            c = gap - a.max; cuts.append((e + (gap - c) / 2, c))
            print(f"  line {x.get('i')}->{y.get('i')}: voice ends {e - a.keep_after:.2f}s, next starts {s + a.keep_before:.2f}s, cut {c:.2f}s")

    shift = lambda t: t - sum(min(c, max(0.0, t - s0)) for s0, c in cuts)
    keep = np.ones(len(A), bool)
    for s0, c in cuts: keep[int(s0 * sr): int((s0 + c) * sr)] = False
    A2 = A[keep]
    if a.tempo != 1.0:
        with tempfile.TemporaryDirectory() as td:
            i_, o_ = Path(td) / "i.wav", Path(td) / "o.wav"; sf.write(i_, A2, sr)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(i_), "-af", f"atempo={a.tempo}", str(o_)], check=True)
            A2 = sf.read(o_, dtype="float32")[0]
    f = lambda t: round(shift(t) / a.tempo, 3)
    sf.write(a.out_wav, A2, sr)
    for l in L:
        l["start"], l["end"] = f(l["start"]), f(l["end"])
        for w in l["words"]:
            w["s"] = f(w["s"])
            if "e" in w: w["e"] = f(w["e"])
    T["total"] = round(len(A2) / sr, 3); Path(a.out_timings).write_text(json.dumps(T, ensure_ascii=False, indent=1), "utf-8")
    if a.spans:   # optional spans file (the --spans input of align.py), shifted the same way
        sp = json.loads(Path(a.spans).read_text("utf-8")); lst = sp["spans"] if isinstance(sp, dict) else sp
        for s in lst: s["a"], s["b"] = f(s["a"]), f(s["b"])
        if isinstance(sp, dict): sp["total"] = T["total"]
        Path(a.out_spans or a.spans).write_text(json.dumps(sp, ensure_ascii=False, indent=1), "utf-8")
    words = sum(len(l["words"]) for l in L)
    print(f"{len(cuts)} pauses shortened (-{sum(c for _, c in cuts):.1f} s), tempo {a.tempo}: "
          f"total {T['total']:.1f} s, {words / T['total'] * 60:.0f} wpm -> {a.out_wav}")


if __name__ == "__main__":
    main()
