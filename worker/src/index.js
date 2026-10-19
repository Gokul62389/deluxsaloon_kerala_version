// Live playlist for 5starthattukada.in.
// GET /playlist -> { tracks: [{ id, title, artist, sp }], source, updated }
// Reads the public Spotify playlist on each (cached ~60s) request. Each Spotify track gets a YouTube video from,
// in order: the site's published list (includes manual overrides), saved matches (KV), or a fresh YouTube search
// (saved to KV so it only happens once). Unmatched tracks are left out until a later request finds a match.
//
// Spotify's public page is served by several replicas and some lag behind for a while after an edit, so one fetch
// can return an older version of the playlist. To stay stable we sample it a few times and track each song:
// - a song seen in any sample is real (stale copies never invent songs); we record when it was first seen
// - a song counts as removed only when it's missing from a sample that contains a song first seen *after* it
//   (proof that sample is newer than the song's addition) - stale copies can't remove recent additions
// - removed songs stay hidden for 24h even if a stale copy still lists them

const PLAYLIST_ID = "28scOOA8UqlUyX7oKcmVEz";
const SITE = "https://5starthattukada.in/";
const CACHE_SECONDS = 60;
const MAX_NEW_SEARCHES = 2; // keeps each request well inside the free plan's subrequest limit
const SAMPLES = 3;          // Spotify fetches per refresh, to catch both fresh and lagging replicas
const REMOVED_HIDE_MS = 24 * 3600 * 1000;
// A sudden big drop is far more likely a partial/glitched Spotify response than a real bulk delete
// (2026-10-15: Spotify briefly returned 15 of 50 songs). Keep serving the previous list until the drop has
// persisted this long; a genuine bulk delete still goes through, just later.
const BIG_DROP = 0.7, BIG_DROP_CONFIRM_MS = 30 * 60 * 1000;
const UA = {
  "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
  "Accept-Language": "en-US,en;q=0.9",
  Cookie: "CONSENT=YES+1",
};
const BAD = /karaoke|cover|jukebox|full movie|#shorts|\bshorts?\b|instrumental|flute|violin|piano|guitar|unplugged|reprise|lyrics? only|remix|mashup|live|stage|teaser|trailer|status|ringtone|dance|whatsapp|8d|slowed|reverb|\bnon[- ]?stop\b|medley|combo|collection|compilation|\bhits\b|back ?to ?back/i;
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

async function spotifyTracks(n = 0) {
  const res = await fetch(`https://open.spotify.com/embed/playlist/${PLAYLIST_ID}?s=${Date.now()}-${n}`, { headers: UA });
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

// Combine several samples of the playlist into the current list, using per-song history kept in KV ("state").
async function currentTracks(env) {
  const settled = await Promise.allSettled(Array.from({ length: SAMPLES }, (_, i) => spotifyTracks(i)));
  const samples = settled.filter(r => r.status === "fulfilled" && r.value.length).map(r => r.value);
  if (!samples.length) throw new Error("Spotify returned no tracks");
  const now = Date.now();
  const state = (env.MATCHES && await env.MATCHES.get("state", "json")) || { seen: {}, removed: {} };
  let changed = false;
  for (const [sp, at] of Object.entries(state.removed)) if (now - at > REMOVED_HIDE_MS) { delete state.removed[sp]; changed = true; }
  const info = {};
  for (const sample of samples) for (const t of sample) {
    info[t.sp] = info[t.sp] || t;
    if (!(t.sp in state.seen)) { state.seen[t.sp] = now; changed = true; }
  }
  const freshness = sample => Math.max(...sample.map(t => state.seen[t.sp]));
  const sets = samples.map(sm => ({ ids: new Set(sm.map(t => t.sp)), fresh: freshness(sm), list: sm }));
  for (const sp of Object.keys(info)) {
    if (sp in state.removed) continue;
    if (sets.some(s => !s.ids.has(sp) && s.fresh > state.seen[sp])) { state.removed[sp] = now; changed = true; }
  }
  // Big-drop guard (see BIG_DROP above)
  const keepCount = Object.keys(info).filter(sp => !(sp in state.removed)).length;
  if (state.lastCount && keepCount < state.lastCount * BIG_DROP) {
    if (!state.dropSince) { state.dropSince = now; changed = true; }
    if (now - state.dropSince < BIG_DROP_CONFIRM_MS && state.lastList) {
      if (changed && env.MATCHES) await env.MATCHES.put("state", JSON.stringify(state));
      return state.lastList;
    }
  } else if (state.dropSince) { delete state.dropSince; changed = true; }
  if (changed && env.MATCHES) await env.MATCHES.put("state", JSON.stringify(state));
  // Order: the freshest sample's order, then any other current songs it's missing
  const freshest = sets.reduce((a, b) => (b.fresh > a.fresh ? b : a)).list;
  const keep = t => !(t.sp in state.removed);
  const ordered = freshest.filter(keep);
  const have = new Set(ordered.map(t => t.sp));
  for (const t of Object.values(info)) if (keep(t) && !have.has(t.sp)) { ordered.push(t); have.add(t.sp); }
  if (env.MATCHES && (state.lastCount !== ordered.length || !state.lastList)) {
    state.lastCount = ordered.length;
    state.lastList = ordered;
    delete state.dropSince;
    await env.MATCHES.put("state", JSON.stringify(state));
  }
  return ordered;
}

async function buildPlaylist(env) {
  const [remote, site] = await Promise.all([currentTracks(env), siteMatches()]);
  if (!remote.length) throw new Error("Spotify returned no tracks");
  const tracks = [];
  let searches = 0;
  for (const r of remote) {
    const saved = env.MATCHES && await env.MATCHES.get("sp:" + r.sp);
    let id = saved || site.get(r.sp);
    if (id && !saved && env.MATCHES) await env.MATCHES.put("sp:" + r.sp, id);
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
