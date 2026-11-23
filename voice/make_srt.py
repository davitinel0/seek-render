"""Subtitles (.srt) from the timings JSON written by voice/align.py.

Cues follow the exact word times: a cue breaks at sentence ends (. ? ! … :), at a comma once it is past half length,
or before it would exceed --max-chars (default 42, the common broadcast limit for one subtitle line). Each cue stays
on screen at least --min-dur seconds.

By default the text is the recognised words. With --script-text the text comes from each line's "text" field (the
script, when align.py was given --script or --spans), so names and spelling are exactly as written; the script words
are put on Whisper's times when the word counts match, otherwise spread over the line by length.

Usage:
  python voice/make_srt.py timings.json out.srt [--script-text] [--max-chars 42] [--min-dur 0.8]
"""
import argparse, json
from pathlib import Path

ENDS = ".?!…:"


def ts(s):
    ms = int(round(max(0.0, s) * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def script_words(line):
    """The script words of a line placed on its timeline: Whisper times when the word counts match, else spread by length."""
    words = line["text"].replace("*", "").split(); ws = line["words"]
    if len(words) == len(ws): return [dict(w=t, s=x["s"], e=x["e"]) for t, x in zip(words, ws)]
    a, b = line["start"], line["end"]; tot = sum(len(t) + 1 for t in words) or 1; out, cur = [], a
    for t in words:
        d = (b - a) * (len(t) + 1) / tot; out.append(dict(w=t, s=cur, e=cur + d)); cur += d
    return out


def cues_of(lines, max_chars=42, use_script=False):
    cues = []
    for l in lines:
        words = script_words(l) if use_script and l.get("text") else l["words"]
        cur = []
        for w in words:
            n = len(" ".join(x["w"] for x in cur))
            if cur and (n + 1 + len(w["w"]) > max_chars or cur[-1]["w"][-1:] in ENDS or (cur[-1]["w"][-1:] == "," and n > max_chars // 2)):
                cues.append(cur); cur = []
            cur.append(w)
        if cur: cues.append(cur)   # a cue never spans two script lines
    return cues


def main():
    ap = argparse.ArgumentParser(description="SRT subtitles from align.py timings.")
    ap.add_argument("timings"); ap.add_argument("out")
    ap.add_argument("--script-text", action="store_true", help="use each line's script text instead of the recognised words")
    ap.add_argument("--max-chars", type=int, default=42); ap.add_argument("--min-dur", type=float, default=0.8)
    a = ap.parse_args()
    lines = json.loads(Path(a.timings).read_text("utf-8"))["lines"]
    cues = cues_of(lines, a.max_chars, a.script_text)
    with open(a.out, "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            end = max(c[-1]["e"], c[0]["s"] + a.min_dur)
            if i < len(cues): end = min(end, cues[i][0]["s"])   # never overlap the next cue
            f.write(f"{i}\n{ts(c[0]['s'])} --> {ts(end)}\n{' '.join(x['w'] for x in c)}\n\n")
    print(len(cues), "cues ->", a.out)


if __name__ == "__main__":
    main()
