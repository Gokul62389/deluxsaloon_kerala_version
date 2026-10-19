#!/usr/bin/env python3
"""Sync the player's TRACKS (in index.html) with the public Spotify playlist.

- Songs removed from Spotify are removed from the player.
- New songs are matched to an embeddable YouTube upload (skipping karaoke, covers, Shorts, jukeboxes...).
- Existing songs keep their current YouTube match.
- scripts/overrides.json can pin a YouTube video for a Spotify track: {"<spotify track id>": "<youtube video id>"}.
- If Spotify can't be read, nothing is changed (never wipes the playlist).
Standard library only, so it runs as-is on GitHub Actions. Exit code 0 always; prints a summary.
"""
import json, re, sys, urllib.parse, urllib.request
from pathlib import Path

PLAYLIST_ID = "28scOOA8UqlUyX7oKcmVEz"
# The Cloudflare Worker already reconciles Spotify's lagging replicas and matches YouTube videos; prefer it.
WORKER = "https://thattukada-playlist.thattukada.workers.dev/playlist"
ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
OVERRIDES = ROOT / "scripts" / "overrides.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"}
BAD = re.compile(r"karaoke|cover|jukebox|full movie|#shorts|\bshorts?\b|instrumental|flute|violin|piano|guitar|unplugged|"
                 r"reprise|lyrics? only|remix|mashup|live|stage|teaser|trailer|status|ringtone|dance|whatsapp|8d|slowed|"
                 r"reverb|\bnon[- ]?stop\b|medley|combo|collection|compilation|\bhits\b|back ?to ?back", re.I)
SINGERS = {"K. J. Yesudas", "K. S. Chithra", "S. Janaki", "M. G. Sreekumar", "Unni Menon", "G Venugopal", "Sujatha",
           "Vani Jairam", "Lathika", "Dinesh", "P. Jayachandran", "P. Susheela", "P. Leela", "Kumar Sanu", "Alka Yagnik"}

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read().decode("utf-8", "ignore")

def spotify_tracks():
    html = get(f"https://open.spotify.com/embed/playlist/{PLAYLIST_ID}")
    data = json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S).group(1))
    def find(o):
        if isinstance(o, dict):
            if "trackList" in o: return o["trackList"]
            for v in o.values():
                r = find(v)
                if r is not None: return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r is not None: return r
    out = []
    for t in find(data) or []:
        sp = (t.get("uri") or "").split(":")[-1]
        if not sp: continue
        title = t["title"].replace(" - Version, 1", "")
        title = re.sub(r'\s*\(From "[^"]+"\)', "", title).strip()
        artists = [a.strip() for a in (t.get("subtitle") or "").split(",") if a.strip()]
        singers = [a for a in artists if a in SINGERS] or artists
        out.append({"sp": sp, "title": title, "artist": ", ".join(singers), "raw": t["title"]})
    return out

def oembed_ok(vid):
    try:
        return json.loads(get("https://www.youtube.com/oembed?format=json&url=" +
                              urllib.parse.quote("https://www.youtube.com/watch?v=" + vid)))
    except Exception:
        return None

def youtube_match(t):
    base = re.sub(r"\s*-\s*(Version.*|Female Version|Male Version|Duet)$", "", t["raw"]).strip()
    base = re.sub(r'\s*\(From "[^"]+"\)', "", base)
    version = " female" if "Female" in t["raw"] else " male" if "Male Version" in t["raw"] else ""
    singer = t["artist"].split(",")[0]
    for q in (f"{base}{version} malayalam song {singer}", f"{base} {singer} Topic"):
        try:
            html = get("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
        except Exception:
            continue
        for vid in list(dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"', html)))[:8]:
            o = oembed_ok(vid)
            if o and not BAD.search(o["title"]):
                return vid, o["title"]
    return None, None

norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())

def main():
    html = INDEX.read_text()
    m = re.search(r"const TRACKS = (\[.*?\]);", html, re.S)
    current = json.loads(m.group(1))
    # 1) Worker (stable, already matched). 2) Fall back to reading Spotify directly.
    worker = None
    try:
        data = json.loads(get(WORKER))
        worker = [t for t in data.get("tracks", []) if re.fullmatch(r"[\w-]{11}", t.get("id", "")) and t.get("sp")]
    except Exception as e:
        print(f"Worker unavailable ({e}); reading Spotify directly.")
    if worker:
        overrides = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
        new_list = [{"id": overrides.get(t["sp"], t["id"]), "title": t["title"], "artist": t["artist"], "sp": t["sp"]} for t in worker]
        return write(html, m, current, new_list, [], [])
    try:
        remote = spotify_tracks()
    except Exception as e:
        print(f"Could not read the Spotify playlist ({e}); leaving the player unchanged."); return
    if not remote:
        print("Spotify returned no tracks; leaving the player unchanged."); return
    # Reading Spotify directly has no protection against a partial response (on 2026-10-15 it briefly returned
    # 15 of 50 songs), so refuse sudden big drops here; the Worker path confirms drops over 30 minutes instead.
    if current and len(remote) < 0.7 * len(current):
        print(f"Spotify returned only {len(remote)} of {len(current)} songs; looks partial, leaving the player unchanged."); return
    overrides = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    by_sp = {t["sp"]: t for t in current if t.get("sp")}
    by_title = {norm(t["title"]): t for t in current if not t.get("sp")}  # one-time backfill for older entries
    new_list, added, unmatched = [], [], []
    for r in remote:
        if r["sp"] in overrides:
            vid = overrides[r["sp"]]
        elif r["sp"] in by_sp:
            vid = by_sp[r["sp"]]["id"]
        elif norm(r["title"]) in by_title:
            vid = by_title[norm(r["title"])]["id"]
        else:
            vid, yt_title = youtube_match(r)
            if not vid:
                unmatched.append(r["title"]); continue
            added.append(f'{r["title"]} -> {vid} ({yt_title})')
        new_list.append({"id": vid, "title": r["title"], "artist": r["artist"], "sp": r["sp"]})
    return write(html, m, current, new_list, added, unmatched)

def write(html, m, current, new_list, added, unmatched):
    kept = {t["sp"] for t in new_list}
    removed = [t["title"] for t in current if t.get("sp") and t["sp"] not in kept]
    before = {t.get("sp") for t in current}
    added = added or [f'{t["title"]} -> {t["id"]}' for t in new_list if t["sp"] not in before]
    if new_list == current:
        print(f"No changes ({len(current)} songs)." + (f" Unmatched: {unmatched}" if unmatched else "")); return
    tracks_js = "const TRACKS = " + json.dumps(new_list, ensure_ascii=False).replace("}, {", "},\n  {") + ";"
    html = html[:m.start()] + tracks_js + html[m.end():]
    # keep the structured data (search engines) in step with the playlist
    ld_m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if ld_m:
        ld = json.loads(ld_m.group(1))
        for g in ld.get("@graph", []):
            if g.get("@type") == "MusicPlaylist":
                g["numTracks"] = len(new_list)
                g["track"] = [{"@type": "MusicRecording", "name": t["title"], "byArtist": {"@type": "MusicGroup", "name": t["artist"]}} for t in new_list]
        html = html[:ld_m.start(1)] + json.dumps(ld, ensure_ascii=False) + html[ld_m.end(1):]
    # visible song list (About · Playlist panel) for visitors and search engines
    import html as _h
    items = "".join(f'<li>{_h.escape(t["title"])} <span>— {_h.escape(t["artist"])}</span></li>' for t in new_list)
    html = re.sub(r"<!--PLAYLIST-->.*?<!--/PLAYLIST-->", lambda _: "<!--PLAYLIST-->" + items + "<!--/PLAYLIST-->", html, count=1, flags=re.S)
    INDEX.write_text(html)
    print(f"Updated: {len(current)} -> {len(new_list)} songs")
    for a in added: print("  + " + a)
    for r_ in removed: print("  - " + r_)
    if unmatched: print("  ? no YouTube match yet (will retry next run): " + ", ".join(unmatched))

if __name__ == "__main__":
    main()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_pages  # keep the static song pages and sitemap in step with the playlist
    build_pages.main()
