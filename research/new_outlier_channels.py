"""Find NEW YouTube channels (first upload in the last N days) that already have a breakout video.

A channel that is a few weeks old and already has a video with 50k+ views found a format that works right now,
before the niche fills up. This is a cheap way to spot those formats early.

Uses yt-dlp only (no API key, no login). Steps:
  1. YouTube search filtered to "uploaded this month (or year), sorted by views" for each query -> videos with
     >= --min views.
  2. For each channel found: its /videos and /shorts listings (views), subscriber count, and the upload date of its
     OLDEST item, used as the channel's age (YouTube's join date is not exposed to yt-dlp). A channel with --ceiling or
     more items in one tab is treated as old without further requests.
  3. Keeps channels whose oldest upload is <= --days ago and whose best video has >= --min views. Ranks them by the
     median views of long videos, then Shorts, then the best video; also reports views per subscriber.
Output: <out>/candidates.csv, <out>/report.md and <out>/cache.json (a rerun with the same --out reuses everything).

Usage:
  python research/new_outlier_channels.py                                 # built-in generic queries (~70 searches)
  python research/new_outlier_channels.py --q "phones" --q "cars"          # own queries
  python research/new_outlier_channels.py --qfile queries.txt --days 30 --min 50000 --per-search 40 --out DIR
Searches go through YouTube's public results page, so results follow YouTube's locale for your IP; add language
words to the queries to target another market. yt-dlp waits 1 s between requests (--sleep-requests 1) to stay polite.
"""
import argparse, csv, datetime as dt, json, statistics as st, subprocess, sys, urllib.parse
from pathlib import Path

SP_MONTH_VIEWS = "CAMSBAgEEAE%3D"   # sort by view count + uploaded this month + type video
SP_YEAR_VIEWS = "CAMSBAgFEAE%3D"    # sort by view count + uploaded this year + type video

DEFAULT_Q = [
    # title formulas that recur in faceless outlier channels
    "everyone ignores goldmines", "stop buying these", "nobody wants", "you'll regret buying", "then vanished",
    "still made in", "dead by 2030", "worth every penny", "robbing you", "should not be this cheap", "brands to avoid",
    "never buy these", "will be bricked", "dirt cheap", "built to last", "nobody talks about", "the truth about",
    "what happened to", "forgotten", "collapse", "secretly", "they don't want you to know", "ranked worst to best",
    # broad niches
    "cars", "trucks", "phones", "laptops", "tools", "kitchen", "fishing", "camping", "watches", "shoes", "clothing brands",
    "retirement", "pensioners", "history", "ww2", "ancient", "money", "credit card", "real estate", "groceries walmart",
    "costco", "amazon finds", "guns", "boots", "knives", "lawn mower", "tires", "tv", "cameras", "gaming", "ai",
]


def ytdlp(args, timeout=300):
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--sleep-requests", "1", "--no-warnings", *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout


def flat(url, end=None):
    a = ["--flat-playlist", "-J"] + (["--playlist-end", str(end)] if end else []) + [url]
    try: return json.loads(ytdlp(a) or "{}") or {}
    except (json.JSONDecodeError, subprocess.TimeoutExpired): return {}


def upload_date(url):
    try: out = ytdlp(["--skip-download", "--print", "%(upload_date)s", url], timeout=120).strip().splitlines()
    except subprocess.TimeoutExpired: return None
    return out[-1] if out and out[-1].isdigit() else None


def main():
    ap = argparse.ArgumentParser(description="Find new YouTube channels with a breakout video (yt-dlp, no API).")
    ap.add_argument("--q", action="append", help="search query (repeatable)"); ap.add_argument("--qfile", help="one query per line, # = comment")
    ap.add_argument("--days", type=int, default=30, help="max channel age, by its first upload (default 30)")
    ap.add_argument("--min", type=int, default=50000, help="views of the breakout video (default 50000)")
    ap.add_argument("--per-search", type=int, default=30, help="results read per search (default 30)")
    ap.add_argument("--max-channels", type=int, default=400); ap.add_argument("--out", help="output folder (default new_outliers/<date>)")
    ap.add_argument("--period", default="month", choices=["month", "year"], help="search upload-date filter")
    ap.add_argument("--ceiling", type=int, default=90, help="items in one tab above which a channel counts as old")
    a = ap.parse_args()
    qs = a.q or ([l.strip() for l in Path(a.qfile).read_text("utf-8").splitlines() if l.strip() and not l.startswith("#")]
                 if a.qfile else DEFAULT_Q)
    today = dt.date.today(); limit = today - dt.timedelta(days=a.days)
    out = Path(a.out) if a.out else Path("new_outliers") / today.isoformat(); out.mkdir(parents=True, exist_ok=True)
    cache_p = out / "cache.json"; cache = json.loads(cache_p.read_text("utf-8")) if cache_p.exists() else {"search": {}, "channel": {}}
    save = lambda: cache_p.write_text(json.dumps(cache, ensure_ascii=False), "utf-8")

    # 1 - searches
    cands = {}
    for i, q in enumerate(qs):
        if q not in cache["search"]:
            sp = SP_YEAR_VIEWS if a.period == "year" else SP_MONTH_VIEWS
            d = flat(f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}&sp={sp}", a.per_search)
            cache["search"][q] = [{"id": e.get("id"), "t": e.get("title"), "v": e.get("view_count"), "c": e.get("channel_id"), "cn": e.get("channel")}
                                  for e in d.get("entries") or [] if e]
            save()
        for e in cache["search"][q]:
            if e["c"] and (e["v"] or 0) >= a.min:
                c = cands.setdefault(e["c"], {"name": e["cn"], "hits": []}); c["hits"].append((e["v"], e["t"], q))
        print(f"search {i + 1}/{len(qs)} '{q}': {len(cands)} channels with a video >= {a.min:,}", flush=True)

    # 2 - channels, most promising first (found by several searches, then biggest hit)
    order = sorted(cands, key=lambda c: (-len(cands[c]["hits"]), -max(v for v, _, _ in cands[c]["hits"])))[: a.max_channels]
    rows = []
    for j, cid in enumerate(order):
        if cid not in cache["channel"]:
            base = f"https://www.youtube.com/channel/{cid}"
            info = {"subs": None, "name": cands[cid]["name"], "long": [], "shorts": [], "oldest": None}
            old = False
            for tab in ("videos", "shorts"):
                d = flat(f"{base}/{tab}", a.ceiling)   # that many items in a tab = not a new channel: skip the date checks
                old = old or len(d.get("entries") or []) >= a.ceiling
                info["subs"] = info["subs"] or d.get("channel_follower_count"); info["name"] = info["name"] or d.get("channel")
                ents = [e for e in d.get("entries") or [] if e]
                info["long" if tab == "videos" else "shorts"] = [{"id": e.get("id"), "t": e.get("title"), "v": e.get("view_count"), "dur": e.get("duration")} for e in ents]
            dates = ["19000101"] if old else []
            for tab in (() if old else ("long", "shorts")):   # listings are newest first: the last item is the oldest
                if info[tab]:
                    dd = upload_date(f"https://www.youtube.com/watch?v={info[tab][-1]['id']}")
                    if dd: dates.append(dd)
            info["oldest"] = min(dates) if dates else None
            cache["channel"][cid] = info; save()
        info = cache["channel"][cid]
        if not info["oldest"] or info["oldest"] == "19000101":
            print(f"channel {j + 1}/{len(order)} {info['name']}: old (>= {a.ceiling} items in a tab, or no date)", flush=True); continue
        born = dt.datetime.strptime(info["oldest"], "%Y%m%d").date()
        vl = [x["v"] for x in info["long"] if x["v"]]; vs = [x["v"] for x in info["shorts"] if x["v"]]
        allv = vl + vs; best = max(allv) if allv else 0
        r = {"channel": info["name"], "url": f"https://www.youtube.com/channel/{cid}", "subscribers": info["subs"], "first_upload": born.isoformat(),
             "age_days": (today - born).days, "long_videos": len(info["long"]), "shorts": len(info["shorts"]),
             "median_long": int(st.median(vl)) if vl else 0, "mean_long": int(st.mean(vl)) if vl else 0,
             "median_shorts": int(st.median(vs)) if vs else 0, "best": best,
             "best_title": max(info["long"] + info["shorts"], key=lambda x: x["v"] or 0)["t"] if allv else "",
             "views_per_subscriber": round(sum(allv) / info["subs"], 1) if info["subs"] else None,
             "query": max(cands[cid]["hits"])[2], "new": born >= limit and best >= a.min}
        rows.append(r)
        print(f"channel {j + 1}/{len(order)} {r['channel']}: {r['age_days']} days, best {best:,}, {'NEW OUTLIER' if r['new'] else '-'}", flush=True)

    rows.sort(key=lambda r: (not r["new"], -(r["median_long"] or r["median_shorts"]), -r["best"]))
    with (out / "candidates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["channel"]); w.writeheader(); w.writerows(rows)
    new = [r for r in rows if r["new"]]
    md = [f"# New outlier channels, {today.isoformat()}", "",
          f"Criteria: first upload <= {a.days} days ago (proxy for the channel's creation) and at least one video with >= {a.min:,} views. "
          f"{len(qs)} searches (uploaded this {a.period}, by views); {len(cands)} channels with a video >= {a.min:,}; {len(rows)} measured; **{len(new)} new**.", "",
          "| Channel | Query | Days | Subscribers | Long / Shorts | Median long | Mean long | Median Shorts | Best | Views/subscriber | Best title |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    md += [f"| [{r['channel']}]({r['url']}) | {r['query']} | {r['age_days']} | {r['subscribers'] or '?'} | {r['long_videos']} / {r['shorts']} | "
           f"{r['median_long']:,} | {r['mean_long']:,} | {r['median_shorts']:,} | {r['best']:,} | {r['views_per_subscriber']} | {r['best_title']} |" for r in new]
    (out / "report.md").write_text("\n".join(md) + "\n", "utf-8")
    print(f"\n{len(new)} new outlier channels -> {out / 'report.md'}")


if __name__ == "__main__":
    sys.exit(main())
