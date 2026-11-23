"""Gate for YouTube description files before upload.

YouTube rejects a description over 5,000 characters or containing "<" or ">", and only shows chapters when the
timestamps start at 0:00, there are at least 3 of them, and each lasts at least 10 seconds. This checks a text or
Markdown file for all of that, so the upload does not fail (or silently drop the chapters) at the last step.
  FAIL  more than --max characters (default 5000), any "<" or ">", chapters not starting at 0:00, fewer than 3
        chapters, a chapter shorter than 10 s, or timestamps out of order
  WARN  more than --warn characters (default 4500): little room left, and YouTube may count some characters differently
Optionally --comment FILE checks a pinned comment against YouTube's 10,000-character comment limit (long credits
lists fit better there than in the description).

Usage:
  python tools/check_description.py description.md [more files ...] [--max 5000] [--warn 4500] [--comment pinned.md]
Exit code 1 if any file fails.
"""
import argparse, re, sys
from pathlib import Path

STAMP = re.compile(r"^\s*(?:[-*•]\s*)?\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?\s")


def secs(stamp):
    s = 0
    for p in stamp.split(":"): s = s * 60 + int(p)
    return s


def check(path, mx, warn):
    t = Path(path).read_text("utf-8").strip(); n = len(t); prob = []
    if n > mx: prob.append(f"{n} characters (YouTube limit {mx})")
    bad = sorted(set(ch for ch in t if ch in "<>"))
    if bad: prob.append(f"contains {' and '.join(bad)} (YouTube rejects these characters)")
    chapters = [(m.group(1), secs(m.group(1))) for m in (STAMP.match(l) for l in t.splitlines()) if m]
    if chapters:
        if chapters[0][1] != 0: prob.append(f"chapters start at {chapters[0][0]}, not 0:00")
        if len(chapters) < 3: prob.append(f"only {len(chapters)} chapter(s); YouTube needs at least 3")
        for (s1, a), (s2, b) in zip(chapters, chapters[1:]):
            if b <= a: prob.append(f"timestamps out of order ({s1} then {s2})")
            elif b - a < 10: prob.append(f"chapter at {s1} lasts {b - a} s (YouTube minimum 10 s)")
    return n, len(chapters), prob


def main():
    ap = argparse.ArgumentParser(description="Check YouTube description files before upload.")
    ap.add_argument("files", nargs="+"); ap.add_argument("--max", type=int, default=5000); ap.add_argument("--warn", type=int, default=4500)
    ap.add_argument("--comment", help="pinned comment file, checked against the 10,000-character limit")
    a = ap.parse_args(); rc = 0
    for f in a.files:
        if not Path(f).is_file(): print(f"FAIL {f}: file not found"); rc = 1; continue
        n, nch, prob = check(f, a.max, a.warn)
        if prob: rc = 1; print(f"FAIL {f}: " + "; ".join(prob))
        else: print(("WARN " if n > a.warn else "OK ") + f"{f}: {n} characters, {nch} chapters")
    if a.comment:
        n = len(Path(a.comment).read_text("utf-8").strip())
        if n > 10000: rc = 1; print(f"FAIL {a.comment}: {n} characters (comment limit 10000)")
        else: print(f"OK {a.comment}: {n} characters")
    sys.exit(rc)


if __name__ == "__main__":
    main()
