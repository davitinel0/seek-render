"""Hook gate for short vertical videos (Reels, Shorts, TikTok), run on the narration timings BEFORE rendering.

Most short-video viewers decide in the first 3-5 seconds. A slow, hesitant first line loses them before the idea lands.
Measured on the first line of the timings JSON written by voice/align.py:
  - start of the first word                    FAIL if > --max-start  (default 0.30 s: the hook starts at once)
  - speaking rate of the first line            FAIL if < --min-wpm    (default 170 words per minute)
  - longest pause inside the first line        FAIL if > --max-pause  (default 0.35 s)
  - end of the first sentence (. ? !)          FAIL if > --max-first  (default 6.0 s)
  - silence between line 1 and line 2          FAIL if > --max-gap    (default 0.35 s: the answer follows immediately)
  - whole-video rate, pauses included          FAIL if < --min-total-wpm (default 165; 0 disables)
Pauses between lines longer than 0.45 s are counted and reported, not failed.
A slow take can usually be fixed without a new recording: voice/tighten_pauses.py (shorter pauses, optional --tempo).

Usage:
  python tools/check_hook.py timings.json [--min-wpm 170] [--max-start 0.3] [--max-pause 0.35] [--max-first 6]
                                          [--max-gap 0.35] [--min-total-wpm 165]
Exit code 1 on FAIL, 0 on PASS.
"""
import argparse, json, sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="Gate for the first seconds of a short video's narration.")
    ap.add_argument("timings")
    ap.add_argument("--min-wpm", type=float, default=170.0); ap.add_argument("--max-start", type=float, default=0.30)
    ap.add_argument("--max-pause", type=float, default=0.35); ap.add_argument("--max-first", type=float, default=6.0)
    ap.add_argument("--max-gap", type=float, default=0.35); ap.add_argument("--min-total-wpm", type=float, default=165.0)
    a = ap.parse_args()
    t = json.loads(Path(a.timings).read_text("utf-8")); L = t["lines"]
    if not L or not L[0]["words"]: sys.exit("FAIL: the first line has no timed words")
    l = L[0]; ws = l["words"]
    s0, e0 = ws[0]["s"], ws[-1].get("e", l["end"])
    wpm = len(ws) / max(0.1, e0 - s0) * 60
    gmax = max([b["s"] - x.get("e", b["s"]) for x, b in zip(ws, ws[1:])] or [0])
    first = next((w.get("e", w["s"]) for w in ws if str(w["w"]).rstrip().endswith(("?", ".", "!", "…"))), e0)
    total_words = sum(len(x["words"]) for x in L); wall = total_words / t["total"] * 60
    long_pauses = [b["start"] - x["end"] for x, b in zip(L, L[1:]) if b["start"] - x["end"] > 0.45]
    fails = []
    if s0 > a.max_start: fails.append(f"first word starts at {s0:.2f} s (max {a.max_start:.2f})")
    if wpm < a.min_wpm: fails.append(f"first line at {wpm:.0f} wpm (min {a.min_wpm:.0f})")
    if gmax > a.max_pause: fails.append(f"{gmax:.2f} s pause inside the first line (max {a.max_pause:.2f})")
    if first > a.max_first: fails.append(f"first sentence ends at {first:.1f} s (max {a.max_first:.1f})")
    if len(L) > 1 and L[1]["start"] - l["end"] > a.max_gap:
        fails.append(f"{L[1]['start'] - l['end']:.2f} s of silence after the first line (max {a.max_gap:.2f})")
    if a.min_total_wpm and wall < a.min_total_wpm: fails.append(f"whole video at {wall:.0f} wpm with pauses (min {a.min_total_wpm:.0f})")
    print(f"hook: \"{' '.join(w['w'] for w in ws)}\"")
    print(f"start {s0:.2f} s | hook {wpm:.0f} wpm | longest pause {gmax:.2f} s | first sentence ends {first:.1f} s | "
          f"whole video {wall:.0f} wpm | pauses > 0.45 s between lines: {len(long_pauses)}")
    print("FAIL: " + "; ".join(fails) if fails else "PASS")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
