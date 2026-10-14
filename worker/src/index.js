// Live playlist for 5starthattukada.in.
// GET /playlist -> { tracks: [{ id, title, artist, sp }], source, updated }
// Reads the public Spotify playlist on each (cached ~60s) request. Each Spotify track gets a YouTube video from,
// in order: the site's published list (includes manual overrides), saved matches (KV), or a fresh YouTube search
// (saved to KV so it only happens once). Unmatched tracks are left out until a later request finds a match.

const PLAYLIST_ID = "28scOOA8UqlUyX7oKcmVEz";
const SITE = "https://5starthattukada.in/";
const CACHE_SECONDS = 60;
const MAX_NEW_SEARCHES = 3; // keeps each request well inside the free plan's subrequest limit
const UA = {
  "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
  "Accept-Language": "en-US,en;q=0.9",
  Cookie: "CONSENT=YES+1",
};
const BAD = /karaoke|cover|jukebox|full movie|#shorts|\bshorts?\b|instrumental|flute|violin|piano|guitar|unplugged|reprise|lyrics? only|remix|mashup|live|stage|teaser|trailer|status|ringtone|dance|whatsapp|8d|slowed|reverb|\bnon[- ]?stop\b|medley/i;
const SINGERS = new Set(["K. J. Yesudas", "K. S. Chithra", "S. Janaki", "M. G. Sreekumar", "Unni Menon", "G Venugopal", "Sujatha",
  "Vani Jairam", "Lathika", "Dinesh", "P. Jayachandran", "P. Susheela", "P. Leela"]);
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS" };

const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...extra } });

function findTrackList(o) {
  if (Array.isArray(o)) { for (const v of o) { const r = findTrackList(v); if (r) return r; } }
  else if (o && typeof o === "object") {
    if (Array.isArray(o.trackList)) return o.trackList;
    for (const v of Object.values(o)) { const r = findTrackList(v); if (r) return r; }
  }
  return null;
}

async function spotifyTracks() {
  const res = await fetch(`https://open.spotify.com/embed/playlist/${PLAYLIST_ID}`, { headers: UA });
  if (!res.ok) throw new Error(`Spotify ${res.status}`);
  const html = await res.text();
  const m = html.match(/<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/);
  if (!m) throw new Error("Spotify page format changed");
  return (findTrackList(JSON.parse(m[1])) || []).map(t => {
    const sp = (t.uri || "").split(":").pop();
    const title = t.title.replace(" - Version, 1", "").replace(/\s*\(From "[^"]+"\)/, "").trim();
    const artists = (t.subtitle || "").split(",").map(a => a.trim()).filter(Boolean);
    const singers = artists.filter(a => SINGERS.has(a));
    return { sp, title, artist: (singers.length ? singers : artists).join(", "), raw: t.title };
  }).filter(t => t.sp);
}

async function siteMatches() {
  try {
    const html = await (await fetch(SITE, { cf: { cacheTtl: 120 } })).text();
    const m = html.match(/const TRACKS = (\[[\s\S]*?\]);/);
    const map = new Map();
    for (const t of JSON.parse(m[1])) if (t.sp) map.set(t.sp, t.id);
    return map;
  } catch { return new Map(); }
}

async function youtubeMatch(t) {
  const base = t.raw.replace(/\s*-\s*(Version.*|Female Version|Male Version|Duet)$/, "").replace(/\s*\(From "[^"]+"\)/, "").trim();
  const version = t.raw.includes("Female") ? " female" : t.raw.includes("Male Version") ? " male" : "";
  const singer = t.artist.split(",")[0];
  for (const q of [`${base}${version} malayalam song ${singer}`, `${base} ${singer} Topic`]) {
    let html;
    try { html = await (await fetch("https://www.youtube.com/results?search_query=" + encodeURIComponent(q), { headers: UA })).text(); }
    catch { continue; }
    const ids = [...new Set([...html.matchAll(/"videoId":"([\w-]{11})"/g)].map(x => x[1]))].slice(0, 6);
    for (const id of ids) {
      try {
        const o = await fetch("https://www.youtube.com/oembed?format=json&url=" + encodeURIComponent("https://www.youtube.com/watch?v=" + id));
        if (!o.ok) continue; // missing or not embeddable
        const { title } = await o.json();
        if (!BAD.test(title)) return id;
      } catch { /* try next */ }
    }
  }
  return null;
}

async function buildPlaylist(env) {
  const [remote, site] = await Promise.all([spotifyTracks(), siteMatches()]);
  if (!remote.length) throw new Error("Spotify returned no tracks");
  const tracks = [];
  let searches = 0;
  for (const r of remote) {
    let id = site.get(r.sp) || (env.MATCHES && await env.MATCHES.get("sp:" + r.sp));
    if (!id && searches < MAX_NEW_SEARCHES) {
      searches++;
      id = await youtubeMatch(r);
      if (id && env.MATCHES) await env.MATCHES.put("sp:" + r.sp, id);
    }
    if (id) tracks.push({ id, title: r.title, artist: r.artist, sp: r.sp });
  }
  return { tracks, source: "spotify-live", updated: new Date().toISOString() };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    if (url.pathname !== "/playlist") return json({ error: "not found" }, 404);
    const cache = caches.default, key = new Request(url.origin + "/playlist");
    const hit = await cache.match(key);
    if (hit) return hit;
    try {
      const body = await buildPlaylist(env);
      const res = json(body, 200, { "Cache-Control": `public, max-age=${CACHE_SECONDS}` });
      ctx.waitUntil(cache.put(key, res.clone()));
      return res;
    } catch (e) {
      return json({ error: String(e.message || e) }, 502, { "Cache-Control": "no-store" });
    }
  },
};
