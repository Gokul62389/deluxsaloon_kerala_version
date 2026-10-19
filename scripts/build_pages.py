#!/usr/bin/env python3
"""Build static, crawlable song pages from the player's TRACKS (in index.html):
  /old-malayalam-songs/        every song, grouped by singer
  /singers/<slug>/             one page per singer with 3+ songs
and refresh sitemap.xml. Song titles and singers only (no lyrics). Run by sync_playlist.py after each sync.
"""
import html, json, re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://5starthattukada.in"
SPOTIFY = "https://open.spotify.com/playlist/28scOOA8UqlUyX7oKcmVEz"
ALIASES = {"Yesudas": "K. J. Yesudas", "Chithra": "K. S. Chithra"}  # Spotify sometimes shortens names
MIN_SONGS = 3
ABOUT = {  # one safe line per well-known singer; others get the generic intro only
    "K. J. Yesudas": "K. J. Yesudas, fondly called Gaana Gandharvan, is one of India's most celebrated playback singers and the voice behind countless Malayalam classics.",
    "K. S. Chithra": "K. S. Chithra, loved across Kerala as Vanambadi, has sung some of the sweetest melodies in Malayalam cinema.",
    "M. G. Sreekumar": "M. G. Sreekumar's voice runs through many of the best-loved Malayalam film songs of the late 1980s and 1990s.",
    "S. Janaki": "S. Janaki is one of South India's most versatile playback singers, with a long list of Malayalam favourites.",
    "Sujatha": "Sujatha Mohan has sung many tender Malayalam film melodies, often in duets with Yesudas and Sreekumar.",
}

esc = html.escape
slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
singers_of = lambda t: [ALIASES.get(s.strip(), s.strip()) for s in t["artist"].split(",") if s.strip()]

def page(path, title, desc, h1, intro_html, sections, crumbs):
    url = f"{SITE}/{path}"
    tracks = [t for _, ts in sections for t in ts]
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "MusicPlaylist", "name": h1, "url": url, "numTracks": len(tracks),
         "track": [{"@type": "MusicRecording", "name": t["title"], "byArtist": {"@type": "MusicGroup", "name": ", ".join(singers_of(t))}} for t in tracks]},
        {"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": f"{SITE}/{p}"} for i, (n, p) in enumerate(crumbs)]}]}
    body = "".join(
        (f"<h2>{esc(head)}</h2>" if head else "") + "<ol>" + "".join(
            f'<li><a class="play" href="/?song={esc(t["sp"])}" aria-label="Play {esc(t["title"])}">▶</a> '
            f'<span class="t">{esc(t["title"])}</span> <span class="s">— {esc(", ".join(singers_of(t)))}</span></li>' for t in ts) + "</ol>"
        for head, ts in sections)
    nav = " · ".join(f'<a href="/{p}">{esc(n)}</a>' for n, p in crumbs)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="5 Star Thattukada">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="{SITE}/og.jpg">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>☕</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+Malayalam:wght@500&display=swap" rel="stylesheet">
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #f3e6cf;
    background: #1b120c linear-gradient(rgba(27,18,12,.86), rgba(27,18,12,.97)) fixed, url(/og.jpg) center / cover fixed; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 32px 16px 64px; }}
  nav {{ font-size: 13px; opacity: .75; }} nav a, a {{ color: #ffd9a0; }}
  h1 {{ font-size: clamp(26px, 5vw, 38px); line-height: 1.2; margin: 18px 0 10px; }}
  h2 {{ font-size: 19px; margin: 28px 0 8px; color: #ffd9a0; }}
  p[lang="ml"] {{ font-family: "Noto Serif Malayalam", serif; }}
  .cta {{ display: inline-block; margin: 8px 0 4px; padding: 10px 18px; border-radius: 999px; background: #f3e6cf; color: #1b120c; font-weight: 600; text-decoration: none; }}
  ol {{ padding-left: 1.6em; }} li {{ margin: 4px 0; }}
  .play {{ text-decoration: none; display: inline-grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; background: rgba(255,255,255,.12); font-size: 11px; margin-right: 4px; }}
  .play:hover, .play:focus-visible {{ background: rgba(255,255,255,.25); }}
  .s {{ opacity: .65; }}
  footer {{ margin-top: 40px; font-size: 13px; opacity: .7; }}
</style>
</head>
<body>
<main>
<nav>{nav}</nav>
<h1>{esc(h1)}</h1>
{intro_html}
<a class="cta" href="/">☕ Open the tea-shop radio</a>
{body}
<footer>Listen to the whole playlist on <a href="/">5 Star Thattukada</a> or on <a href="{SPOTIFY}" rel="noopener">Spotify</a>. Song titles and singers only; music plays from official YouTube uploads.</footer>
</main>
</body>
</html>
"""

def main():
    index = (ROOT / "index.html").read_text()
    tracks = json.loads(re.search(r"const TRACKS = (\[.*?\]);", index, re.S).group(1))
    by_singer = {}
    for t in tracks:
        for s in singers_of(t):
            by_singer.setdefault(s, []).append(t)
    ranked = sorted(by_singer.items(), key=lambda kv: -len(kv[1]))
    featured = [(s, ts) for s, ts in ranked if len(ts) >= MIN_SONGS]
    home = ("5 Star Thattukada", "")
    hub = ("Old Malayalam songs", "old-malayalam-songs/")

    # Hub: every song once, under its lead singer
    groups = {}
    for t in tracks:
        groups.setdefault(singers_of(t)[0], []).append(t)
    sections = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    singer_links = " · ".join(f'<a href="/singers/{slug(s)}/">{esc(s)} songs</a>' for s, _ in featured)
    intro = (f"<p>A hand-picked playlist of {len(tracks)} evergreen Malayalam film songs, the melodies that still play on the transistor at "
             "every roadside thattukada. Press ▶ on any song to start the 5 Star Thattukada radio from it, then let it carry on "
             "through the rest of the old Malayalam favourites.</p>"
             '<p lang="ml">തട്ടുകടയിലെ റേഡിയോയിൽ ഇന്നും കേൾക്കുന്ന പഴയ മലയാളം സിനിമാഗാനങ്ങൾ. ഏതു പാട്ടിലും ▶ അമർത്തി കേട്ടു തുടങ്ങാം.</p>'
             f"<p>By singer: {singer_links}</p>")
    out = {"old-malayalam-songs/index.html": page(
        hub[1], f"Old Malayalam Songs – {len(tracks)} Evergreen Film Songs | 5 Star Thattukada",
        f"Listen to {len(tracks)} evergreen old Malayalam film songs by Yesudas, Chithra, M. G. Sreekumar, S. Janaki and more, free on the 5 Star Thattukada radio.",
        "Old Malayalam film songs", intro, sections, [home, hub])}

    # One page per featured singer
    for s, ts in featured:
        path = f"singers/{slug(s)}/"
        intro = (f"<p>{esc(ABOUT.get(s, ''))} Here are {len(ts)} of their evergreen Malayalam film songs from the 5 Star Thattukada playlist. "
                 "Press ▶ to start the radio with any of them.</p>")
        out[path + "index.html"] = page(
            path, f"{s} Songs – {len(ts)} Evergreen Malayalam Hits | 5 Star Thattukada",
            f"Listen to {len(ts)} evergreen Malayalam film songs by {s}, free on the 5 Star Thattukada old Malayalam songs radio.",
            f"{s} songs", intro, [("", ts)], [home, hub, (f"{s} songs", path)])

    # Write pages, drop pages for singers who no longer qualify
    for rel, content in out.items():
        f = ROOT / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists() or f.read_text() != content:
            f.write_text(content)
    for old in (ROOT / "singers").glob("*/index.html"):
        if f"singers/{old.parent.name}/index.html" not in out:
            old.unlink(); old.parent.rmdir()

    urls = [""] + [rel[: -len("index.html")] for rel in out]
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "".join(f"  <url><loc>{SITE}/{u}</loc><lastmod>{date.today()}</lastmod></url>\n" for u in urls) + "</urlset>\n")
    (ROOT / "sitemap.xml").write_text(sitemap)

    # self-check: every page lists songs and every ▶ link points at a known track
    known = {t["sp"] for t in tracks}
    for rel, content in out.items():
        sps = re.findall(r'href="/\?song=([^"]+)"', content)
        assert sps and set(sps) <= known, rel
    print(f"Built {len(out)} pages: " + ", ".join("/" + r[: -len('index.html')] for r in out))

if __name__ == "__main__":
    main()
