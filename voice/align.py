"""Word timestamps for a narration track with faster-whisper, grouped into script lines.

Writes a timings JSON that the other tools in this repo read (make_srt.py, tighten_pauses.py, check_hook.py,
audio/mix.py):

    {"total": 61.2,                      # duration of the audio, seconds
     "lines": [{"i": 0, "text": "Line as written in the script.",
                "start": 0.12, "end": 3.40,  # first word start, last word end
                "words": [{"w": "Line", "s": 0.12, "e": 0.31}, ...]}, ...]}

Three ways to define the lines, from most to least precise:
  --spans spans.json   You know where each line sits in the audio (e.g. you concatenated one TTS clip per line):
                       [{"text": "...", "a": 0.0, "b": 3.6}, ...] (or {"spans": [...]}). Each span is transcribed on its
                       own, so words can never leak into the neighbouring line, and memory stays low on long files.
  --script script.txt  One narration line per text line. The whole file is transcribed once and the recognised words
                       are matched back to the script (difflib), so each line gets the right words and times even when
                       Whisper mishears a few of them.
  (neither)            The whole file is transcribed and each Whisper segment becomes a line.

Line text always comes from the script when one is given, so make_srt.py can print the exact spelling.

Usage:
  python voice/align.py voice.wav timings.json [--script script.txt | --spans spans.json]
                        [--model small] [--language en] [--device cpu] [--compute-type int8] [--threads 2]
--model is a faster-whisper model name ("small", "medium", "large-v3", ...) or a path to a local model folder.
Requires ffmpeg on the PATH and `pip install faster-whisper`.
"""
import argparse, difflib, json, re, subprocess, sys, unicodedata
from pathlib import Path
import numpy as np

SR = 16000


def load_pcm(wav):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    if r.returncode: sys.exit(r.stderr.decode(errors="ignore"))
    return np.frombuffer(r.stdout, np.float32)


def norm(w):
    """Lower case, no accents, letters and digits only: for matching recognised words to script words."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFD", w.lower()).encode("ascii", "ignore").decode())


def words_of(segs, offset=0.0):
    return [{"w": w.word.strip(), "s": round(w.start + offset, 3), "e": round(w.end + offset, 3)}
            for s in segs for w in (s.words or []) if w.word.strip()]


def by_spans(model, pcm, spans, lang):
    lines = []
    for i, sp in enumerate(spans):
        a0 = max(0.0, sp["a"] - 0.15); seg = pcm[int(a0 * SR): int((sp["b"] + 0.15) * SR)]
        segs, _ = model.transcribe(seg, language=lang, word_timestamps=True, beam_size=5, condition_on_previous_text=False)
        ws = words_of(segs, a0) or [{"w": sp["text"], "s": sp["a"], "e": sp["b"]}]   # nothing recognised: the span itself
        lines.append({"i": sp.get("i", i), "text": sp["text"], "start": ws[0]["s"], "end": ws[-1]["e"], "words": ws})
    return lines


def by_script(words, script_lines):
    """Assign recognised words to script lines. Script tokens and recognised tokens are aligned with difflib; each line
    takes the recognised words from its first matched token up to the next line's first matched token."""
    stoks, owner = [], []
    for k, t in enumerate(script_lines):
        for tok in t.split():
            if norm(tok): stoks.append(norm(tok)); owner.append(k)
    rtoks = [norm(w["w"]) for w in words]
    sm = difflib.SequenceMatcher(None, stoks, rtoks, autojunk=False)
    first = [None] * len(script_lines)          # first recognised index matched inside each line
    for a, b, n in sm.get_matching_blocks():
        for j in range(n):
            k = owner[a + j]
            if first[k] is None or b + j < first[k]: first[k] = b + j
    # lines with no match: interpolate between neighbours (by token position)
    known = [(k, v) for k, v in enumerate(first) if v is not None]
    if not known: known = [(0, 0)]
    for k in range(len(first)):
        if first[k] is None:
            prev = max((x for x in known if x[0] < k), default=None, key=lambda x: x[0])
            nxt = min((x for x in known if x[0] > k), default=None, key=lambda x: x[0])
            first[k] = prev[1] + 1 if prev and (not nxt or nxt[1] > prev[1] + 1) else (prev[1] if prev else 0)
    for k in range(1, len(first)): first[k] = max(first[k], first[k - 1])
    lines = []
    for k, text in enumerate(script_lines):
        a, b = first[k], (first[k + 1] if k + 1 < len(first) else len(words))
        ws = words[a:b]
        if not ws:  # an unrecognised line: give it a zero-length slot where it should be
            t = words[a - 1]["e"] if 0 < a <= len(words) else (words[0]["s"] if words else 0.0)
            ws = [{"w": text, "s": t, "e": t}]
        lines.append({"i": k, "text": text, "start": ws[0]["s"], "end": ws[-1]["e"], "words": ws})
    return lines


def main():
    ap = argparse.ArgumentParser(description="Word timestamps of a narration (faster-whisper) grouped into script lines.")
    ap.add_argument("wav"); ap.add_argument("out_json")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--script", help="text file, one narration line per line")
    g.add_argument("--spans", help='JSON list of {"text", "a", "b"}: where each line sits in the audio (seconds)')
    ap.add_argument("--model", default="small", help="faster-whisper model name or local model folder (default: small)")
    ap.add_argument("--language", default=None, help="ISO code of the narration (en, pt, fr, ...). Default: auto-detect")
    ap.add_argument("--device", default="cpu"); ap.add_argument("--compute-type", default="int8"); ap.add_argument("--threads", type=int, default=2)
    a = ap.parse_args()
    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device=a.device, compute_type=a.compute_type, cpu_threads=a.threads)
    pcm = load_pcm(a.wav); total = round(len(pcm) / SR, 3)
    if a.spans:
        sp = json.loads(Path(a.spans).read_text("utf-8")); sp = sp["spans"] if isinstance(sp, dict) else sp
        lines = by_spans(model, pcm, sp, a.language)
    else:
        segs, info = model.transcribe(pcm, language=a.language, word_timestamps=True, beam_size=5, condition_on_previous_text=False)
        segs = list(segs)
        if a.script:
            script = [l.strip() for l in Path(a.script).read_text("utf-8").splitlines() if l.strip()]
            lines = by_script(words_of(segs), script)
        else:
            lines = [{"i": k, "text": s.text.strip(), "start": ws[0]["s"], "end": ws[-1]["e"], "words": ws}
                     for k, (s, ws) in enumerate((s, words_of([s])) for s in segs) if ws]
            for k, l in enumerate(lines): l["i"] = k
    for x, y in zip(lines, lines[1:]):   # line starts strictly increase (downstream tools index by start)
        y["start"] = max(y["start"], x["start"] + 0.2)
    Path(a.out_json).write_text(json.dumps({"total": total, "lines": lines}, ensure_ascii=False, indent=1), "utf-8")
    for l in lines:
        print(f"{l['start']:7.2f} {l['end']:7.2f} {len(l['words']):3d}w {l['text'][:60]}")
    print(f"{len(lines)} lines, {sum(len(l['words']) for l in lines)} words, {total:.1f} s -> {a.out_json}")


if __name__ == "__main__":
    main()
