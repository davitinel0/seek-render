"""Outlier videos of YouTube channels: each video's views divided by its channel's median views.

A video with 10x its channel's median did something the channel's usual audience alone cannot explain (topic, title,
thumbnail). Ranking by this multiple, rather than by raw views, compares small and big channels on the same scale and
is a common way to find topics and packaging that are already proven in a niche.

Uses yt-dlp only (no API key, no login), flat playlist listings, so it is fast and light. Optional extras: download
every thumbnail (for a visual swipe file) and the top N videos per channel in low resolution, with automatic
subtitles and a frame every 2 s (for studying edits).
Note: flat listings show rounded view counts on big channels (e.g. 22,000,000); the ranking is still meaningful.

Usage:
  python research/channel_outliers.py @handle1 @handle2 https://www.youtube.com/@handle3 [--tab videos|shorts|streams]
          [--limit 200] [--out outliers.csv] [--cache DIR] [--thumbs DIR] [--download 3 --dl-dir DIR] [--sub-langs en.*]
Channels can be given as @handles, bare handles, channel URLs, or one per line in --channels-file.
"""
import argparse, csv, json, statistics, subprocess, sys, urllib.request
from pathlib import Path

YTDLP = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--ignore-errors"]


def channel_url(c, tab):
    c = c.strip()
    if c.startswith("http"): base = c.rstrip("/").removesuffix("/videos").removesuffix("/shorts").removesuffix("/streams")
    else: base = f"https://www.youtube.com/@{c.lstrip('@')}"
    return f"{base}/{tab}"


def key_of(c):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in c.strip().rstrip("/").split("/")[-1].lstrip("@"))


def fetch(c, tab, limit, cache):
    out = cache / f"{key_of(c)}_{tab}.json" if cache else None
    if out and out.exists(): return json.loads(out.read_text("utf-8"))
    a = YTDLP + ["--flat-playlist", "-J"] + (["--playlist-end", str(limit)] if limit else []) + [channel_url(c, tab)]
    r = subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace")
    data = json.loads(r.stdout) if r.stdout.strip() else {"entries": []}
    vids = [{"id": e["id"], "title": e.get("title"), "views": e.get("view_count") or 0, "duration": e.get("duration"),
             "upload_date": e.get("upload_date") or e.get("release_timestamp"),
             "url": f"https://www.youtube.com/watch?v={e['id']}"}
            for e in data.get("entries") or [] if e and e.get("id")]
    if out: out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(vids, ensure_ascii=False, indent=1), "utf-8")
    return vids


def main():
    ap = argparse.ArgumentParser(description="Rank videos by views / channel median (outliers), with yt-dlp.")
    ap.add_argument("channels", nargs="*"); ap.add_argument("--channels-file")
    ap.add_argument("--tab", default="videos", choices=["videos", "shorts", "streams"])
    ap.add_argument("--limit", type=int, default=0, help="only the N most recent items per channel (0 = all)")
    ap.add_argument("--out", default="outliers.csv"); ap.add_argument("--cache", help="folder for raw listings (reruns are free)")
    ap.add_argument("--thumbs", help="download every thumbnail into this folder")
    ap.add_argument("--download", type=int, default=0, help="download the top N outliers per channel (480p)")
    ap.add_argument("--dl-dir", default="outlier_videos"); ap.add_argument("--sub-langs", default="en.*,en-orig")
    ap.add_argument("--top", type=int, default=15, help="rows printed to the console")
    a = ap.parse_args()
    chans = list(a.channels) + ([l.strip() for l in Path(a.channels_file).read_text("utf-8").splitlines()
                                 if l.strip() and not l.startswith("#")] if a.channels_file else [])
    if not chans: ap.error("give at least one channel")
    cache = Path(a.cache) if a.cache else None
    rows = []
    for c in chans:
        vids = [v for v in fetch(c, a.tab, a.limit, cache) if v["views"]]
        if not vids: print("EMPTY (no views listed)", c); continue
        med = statistics.median(v["views"] for v in vids) or 1
        for v in vids: rows.append(dict(v, channel=key_of(c), median=int(med), multiple=round(v["views"] / med, 2)))
        print(f"{key_of(c)}: {len(vids)} {a.tab}, median {med:,.0f} views")
    rows.sort(key=lambda r: -r["multiple"])
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["channel", "id", "title", "views", "median", "multiple", "duration", "upload_date", "url"])
        w.writeheader(); w.writerows(rows)
    print(f"\ntop outliers ({len(rows)} rows -> {a.out}):")
    for r in rows[: a.top]: print(f"  {r['multiple']:6.1f}x  {r['views']:>11,}  {r['channel'][:20]:20s}  {(r['title'] or '')[:70]}")
    if a.thumbs:
        for r in rows:
            p = Path(a.thumbs) / r["channel"] / f"{r['id']}.jpg"
            if p.exists(): continue
            p.parent.mkdir(parents=True, exist_ok=True)
            try: urllib.request.urlretrieve(f"https://i.ytimg.com/vi/{r['id']}/hqdefault.jpg", p)
            except Exception as ex: print("thumbnail failed", r["id"], ex)
    if a.download:
        for ch in dict.fromkeys(r["channel"] for r in rows):
            d = Path(a.dl_dir) / ch; d.mkdir(parents=True, exist_ok=True)
            for v in [r for r in rows if r["channel"] == ch][: a.download]:
                mp4 = d / f"{v['id']}.mp4"
                if not mp4.exists():
                    subprocess.run(YTDLP + ["-f", "bv*[height<=480]+ba/b[height<=480]", "--merge-output-format", "mp4",
                                            "--write-auto-subs", "--sub-langs", a.sub_langs, "--sub-format", "vtt",
                                            "-o", str(d / "%(id)s.%(ext)s"), v["url"]])
                fd = d / f"{v['id']}_frames"
                if mp4.exists() and not fd.exists():
                    fd.mkdir(parents=True)
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp4), "-vf", "fps=0.5,scale=480:-2", "-q:v", "4", str(fd / "%04d.jpg")])


if __name__ == "__main__":
    main()
