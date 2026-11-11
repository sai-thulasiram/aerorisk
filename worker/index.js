// Cloudflare Worker: serves /api/lookup; everything else is static assets.
// Secrets:  AERODATABOX_RAPIDAPI_KEY   (wrangler secret put AERODATABOX_RAPIDAPI_KEY)
// Dev only: ADB_MOCK=1 returns synthetic flights (never set in production).
import { DurableObject } from "cloudflare:workers";
import { buildResult, knownCarrier, parseAdb, parseFlight, validDate } from "./match.js";
import { cleanFeedback } from "./feedback.js";
import { forwardSend, UMAMI_SCRIPT } from "./umami.js";
import { parseSlug, renderFlightPage, sitemapXml, slugFor, todayFor } from "./flightpage.js";

// Feedback storage: one SQLite-backed Durable Object (free plan, no setup needed).
export class FeedbackStore extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.storage.sql.exec(`CREATE TABLE IF NOT EXISTS feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, page TEXT, config TEXT, rating TEXT,
      kind TEXT, message TEXT, email TEXT, country TEXT)`);
  }
  async add(r) {
    this.ctx.storage.sql.exec(
      "INSERT INTO feedback (ts, page, config, rating, kind, message, email, country) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      new Date().toISOString(), r.page, r.config, r.rating, r.kind, r.message, r.email, r.country);
    return true;
  }
  async list(limit) {
    return this.ctx.storage.sql.exec("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", limit).toArray();
  }
}

// Aircraft seen on each flight, from lookups. Powers the history table and the flight sitemap.
export class FlightLog extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.storage.sql.exec(`CREATE TABLE IF NOT EXISTS obs (
      flight TEXT NOT NULL, date TEXT NOT NULL, origin TEXT, destination TEXT, aircraft_model TEXT,
      registration TEXT, config_id TEXT, seen_at TEXT NOT NULL, PRIMARY KEY (flight, date, origin))`);
  }
  async record(flight, date, legs) {
    for (const l of legs) {
      if (!l.aircraft_model) continue;
      const top = l.match && l.match.candidates && l.match.candidates[0];
      this.ctx.storage.sql.exec(
        `INSERT INTO obs (flight, date, origin, destination, aircraft_model, registration, config_id, seen_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (flight, date, origin) DO UPDATE SET
         aircraft_model = excluded.aircraft_model, registration = COALESCE(excluded.registration, obs.registration),
         config_id = excluded.config_id, seen_at = excluded.seen_at`,
        flight, date, l.origin || "", l.destination, l.aircraft_model, l.registration, top ? top.config_id : null,
        new Date().toISOString());
    }
    return true;
  }
  // Daily cap on upstream calls triggered by page views (crawlers), so they can't use up the API plan.
  async spend(day, max) {
    this.ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS budget (day TEXT PRIMARY KEY, n INTEGER NOT NULL)");
    const row = this.ctx.storage.sql.exec("SELECT n FROM budget WHERE day = ?", day).toArray()[0];
    const n = row ? row.n : 0;
    if (n >= max) return false;
    this.ctx.storage.sql.exec("INSERT INTO budget (day, n) VALUES (?, 1) ON CONFLICT (day) DO UPDATE SET n = n + 1", day);
    return true;
  }
  async history(flight, limit = 30) {
    return this.ctx.storage.sql.exec("SELECT date, origin, destination, aircraft_model, registration, config_id FROM obs " +
      "WHERE flight = ? ORDER BY date DESC LIMIT ?", flight, limit).toArray();
  }
  async flights(limit = 5000) {
    const since = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);
    return this.ctx.storage.sql.exec("SELECT flight, MAX(date) AS last FROM obs WHERE date >= ? GROUP BY flight " +
      "ORDER BY last DESC LIMIT ?", since, limit).toArray();
  }
}

const flightLog = (env) => (env.FLIGHTLOG ? env.FLIGHTLOG.get(env.FLIGHTLOG.idFromName("all")) : null);

const ADB_HOST = "aerodatabox.p.rapidapi.com";
const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "x-content-type-options": "nosniff" };

const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body), { status, headers: { ...JSON_HEADERS, ...extra } });

async function loadIndex(env, request) {
  const res = await env.ASSETS.fetch(new Request(new URL("/api/v1/match-index.json", request.url)));
  if (!res.ok) throw new Error("match index missing");
  return res.json();
}

function mockFlights(flight) {
  const models = { "6E": "Airbus A320 NEO", IGO: "Airbus A320 NEO", FR: "Boeing 737-800", RYR: "Boeing 737-800",
                   U2: "Airbus A320neo", EZY: "Airbus A320neo" };
  const ops = { "6E": ["6E", "IGO", "IndiGo"], FR: ["FR", "RYR", "Ryanair"], U2: ["U2", "EZY", "easyJet"] };
  const op = ops[flight.carrier] || [flight.carrier, null, flight.carrier];
  return [{ number: `${flight.carrier} ${flight.number}`, status: "Expected", codeshareStatus: "IsOperator",
            departure: { airport: { iata: "BLR" }, scheduledTime: { local: "2026-12-01 06:10+05:30" } },
            arrival: { airport: { iata: "DEL" } },
            aircraft: { model: models[flight.carrier] || "Boeing 787-9" },
            airline: { iata: op[0], icao: op[1], name: op[2] } }];
}

async function callAdb(env, flight, date) {
  if (env.ADB_MOCK === "1") return mockFlights(flight);
  const url = `https://${ADB_HOST}/flights/number/${encodeURIComponent(flight.code)}/${date}?withAircraftImage=false&withLocation=false`;
  const res = await fetch(url, { headers: { "X-RapidAPI-Key": env.AERODATABOX_RAPIDAPI_KEY, "X-RapidAPI-Host": ADB_HOST,
                                            accept: "application/json" } });
  if (res.status === 204 || res.status === 404) return [];
  if (!res.ok) { const e = new Error(`upstream ${res.status}`); e.status = res.status; throw e; }
  return res.json();
}

// Shared by /api/lookup and the flight pages. Returns { status, body, cacheHit }.
async function lookupCore(env, ctx, request, flight, date, index, { perIp = true, budget = null } = {}) {
  const query = { flight: flight.code, date };
  const cacheKey = new Request(`https://lookup-cache.internal/v1/${flight.code}/${date}`);
  const cache = caches.default;
  const hit = await cache.match(cacheKey);
  if (hit) return { status: hit.status, body: await hit.json(), cacheHit: true };

  // Rate limits protect the API quota (only reached on cache misses).
  const ip = request.headers.get("cf-connecting-ip") || "anon";
  if (perIp && env.LOOKUP_PER_IP) {
    const { success } = await env.LOOKUP_PER_IP.limit({ key: `ip:${ip}` });
    if (!success) return { status: 429, body: { error: "rate_limited", message: "Too many lookups. Try again in a minute." } };
  }
  if (env.LOOKUP_GLOBAL) {
    const { success } = await env.LOOKUP_GLOBAL.limit({ key: "global" });
    if (!success) return { status: 429, body: { error: "busy", message: "Lookups are busy right now. Try again in a minute." } };
  }
  if (budget && !(await budget())) {
    return { status: 429, body: { error: "budget", message: "Live data for this page will be back shortly. Use the lookup on the home page meanwhile." } };
  }
  let payload;
  try {
    payload = await callAdb(env, flight, date);
  } catch (e) {
    const st = e.status || null;
    const message =
      st === 401 || st === 403 ? "The flight data key was rejected. Check that the key is subscribed to AeroDataBox on RapidAPI." :
      st === 429 ? "The flight data plan's monthly or per-second limit is used up. Try again later." :
      "The flight data service didn't answer. Try again shortly.";
    return { status: 502, body: { error: "upstream_error", message, upstream_status: st } };
  }
  const result = { ...buildResult(query, parseAdb(payload), index), source: "AeroDataBox", fetched_at: new Date().toISOString() };
  const daysAway = (Date.parse(date + "T00:00:00Z") - Date.now()) / 86400000;
  const ttl = daysAway < 2 ? 1800 : 6 * 3600;    // registrations appear close to departure
  ctx.waitUntil(cache.put(cacheKey, json(result, 200, { "cache-control": `public, max-age=${ttl}` })));
  const log = flightLog(env);
  if (log && result.legs.length) ctx.waitUntil(log.record(flight.code, date, result.legs).catch(() => {}));
  return { status: 200, body: result, cacheHit: false, ttl };
}

async function lookup(request, env, ctx) {
  const url = new URL(request.url);
  const enabled = Boolean(env.AERODATABOX_RAPIDAPI_KEY) || env.ADB_MOCK === "1";
  if (url.searchParams.has("status")) {
    return json({ enabled, key_present: Boolean(env.AERODATABOX_RAPIDAPI_KEY), mock: env.ADB_MOCK === "1" }, 200,
                { "cache-control": "no-store" });
  }
  if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
  if (!enabled) return json({ error: "lookup_not_configured", message: "Flight lookup isn't switched on yet." }, 503);

  const flight = parseFlight(url.searchParams.get("flight"));
  const date = url.searchParams.get("date") || "";
  if (!flight) return json({ error: "bad_flight", message: "Enter a flight number like 6E 2134 or FR 1234." }, 400);
  if (!validDate(date)) return json({ error: "bad_date", message: "Pick a date from the last week up to about 11 months ahead." }, 400);

  const index = await loadIndex(env, request);
  const query = { flight: flight.code, date };
  if (!knownCarrier(index, flight.carrier)) {
    return json({ query, legs: [], unsupported_airline: true,
                  message: "We don't have seat maps for this airline yet." }, 200, { "cache-control": "public, max-age=3600" });
  }
  const r = await lookupCore(env, ctx, request, flight, date, index);
  const extra = r.status === 429 ? { "retry-after": "60" } : {};
  if (r.status === 200) {
    r.body.page = `/flights/${slugFor(flight)}/`;
    return json(r.body, 200, { "cache-control": `public, max-age=${r.ttl || 1800}`, "x-cache": r.cacheHit ? "HIT" : "MISS" });
  }
  return json(r.body, r.status, extra);
}

const HTML_HEADERS = { "content-type": "text/html; charset=utf-8" };

async function flightPage(request, env, ctx) {
  const url = new URL(request.url);
  const m = /^\/flights\/([^/]+)\/?$/.exec(url.pathname);
  const flight = m ? parseSlug(decodeURIComponent(m[1])) : null;
  if (!flight) {
    const nf = await env.ASSETS.fetch(new Request(new URL("/404.html", request.url)));
    return new Response(nf.body, { status: 404, headers: { ...HTML_HEADERS, "x-robots-tag": "noindex" } });
  }
  const slug = slugFor(flight);
  const canonicalPath = `/flights/${slug}/`;
  if (url.pathname !== canonicalPath) {
    return Response.redirect(new URL(canonicalPath + url.search, request.url).toString(), 301);
  }
  const index = await loadIndex(env, request);
  const tplRes = await env.ASSETS.fetch(new Request(new URL("/_tpl/flight.tpl", request.url)));
  const tpl = await tplRes.text();
  const today = todayFor(flight.carrier);
  const reqDate = url.searchParams.get("date");
  const date = reqDate && validDate(reqDate) ? reqDate : today;
  const enabled = Boolean(env.AERODATABOX_RAPIDAPI_KEY) || env.ADB_MOCK === "1";

  let result = null, liveNote = null;
  if (!knownCarrier(index, flight.carrier)) {
    liveNote = "We don't have seat maps for this airline yet.";
  } else if (!enabled) {
    liveNote = "Live flight data isn't switched on yet.";
  } else {
    const log = flightLog(env);
    const max = Number(env.PAGE_LOOKUPS_PER_DAY || 150);
    const budget = log ? () => log.spend(new Date().toISOString().slice(0, 10), max).catch(() => false) : null;
    const r = await lookupCore(env, ctx, request, flight, date, index, { perIp: false, budget });
    if (r.status === 200) result = r.body;
    else liveNote = r.body.message || "Live flight data isn't available right now.";
  }
  const log = flightLog(env);
  let history = [];
  if (log) { try { history = await log.history(flight.code); } catch (_) { history = []; } }

  const page = renderFlightPage(tpl, { flight, date, today, result, history, index, liveNote, slug, origin: `https://${url.host}` });
  const status = page.indexable || (result && result.legs.length) || history.length ? 200 : 404;
  return new Response(page.html, { status, headers: { ...HTML_HEADERS, "cache-control": "public, max-age=1800",
    ...(page.indexable ? {} : { "x-robots-tag": "noindex" }) } });
}

async function flightSitemap(request, env) {
  const log = flightLog(env);
  let rows = [];
  if (log) { try { rows = await log.flights(); } catch (_) { rows = []; } }
  const slugs = rows.map((r) => parseSlug(r.flight)).filter(Boolean).map(slugFor);
  return new Response(sitemapXml(`https://${new URL(request.url).host}`, slugs),
    { headers: { "content-type": "application/xml; charset=utf-8", "cache-control": "public, max-age=3600" } });
}

function feedbackStub(env) {
  return env.FEEDBACK.get(env.FEEDBACK.idFromName("all"));
}

const csvCell = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;

async function feedback(request, env) {
  const url = new URL(request.url);
  if (request.method === "GET") {
    // Admin read: /api/feedback?token=...  (&format=csv). Disabled unless FEEDBACK_ADMIN_TOKEN is set.
    const token = url.searchParams.get("token") || (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
    if (!env.FEEDBACK_ADMIN_TOKEN || token !== env.FEEDBACK_ADMIN_TOKEN) return json({ error: "not_found" }, 404);
    const rows = await feedbackStub(env).list(Math.min(Number(url.searchParams.get("limit")) || 500, 5000));
    if (url.searchParams.get("format") === "csv") {
      const cols = ["id", "ts", "page", "config", "rating", "kind", "message", "email", "country"];
      const body = [cols.join(","), ...rows.map((r) => cols.map((c) => csvCell(r[c])).join(","))].join("\n");
      return new Response(body, { headers: { "content-type": "text/csv; charset=utf-8", "cache-control": "no-store" } });
    }
    return json({ count: rows.length, feedback: rows }, 200, { "cache-control": "no-store" });
  }
  if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
  if (!(request.headers.get("content-type") || "").includes("application/json")) return json({ error: "bad_request" }, 415);
  const origin = request.headers.get("origin");
  if (origin && new URL(origin).host !== url.host) return json({ error: "forbidden" }, 403);
  if (env.FEEDBACK_PER_IP) {
    const { success } = await env.FEEDBACK_PER_IP.limit({ key: `fb:${request.headers.get("cf-connecting-ip") || "anon"}` });
    if (!success) return json({ error: "rate_limited", message: "Thanks! Please wait a minute before sending more." }, 429);
  }
  let raw;
  try { raw = await request.json(); } catch (_) { return json({ error: "bad_request", message: "Could not read that." }, 400); }
  const res = cleanFeedback(raw);
  if (res.spam) return json({ ok: true });
  if (res.error) return json({ error: "bad_request", message: res.error }, 400);
  await feedbackStub(env).add({ ...res.value, country: (request.cf && request.cf.country) || null });
  return json({ ok: true });
}

export default {
  async fetch(request, env, ctx) {
    const { pathname } = new URL(request.url);
    if (pathname === "/api/feedback") {
      try { return await feedback(request, env); }
      catch (e) { return json({ error: "internal_error", message: "Could not save that. Please try again." }, 500); }
    }
    if (pathname === "/u/s.js") {
      const cache = caches.default, key = new Request("https://umami-cache.internal/script.js");
      let res = await cache.match(key);
      if (!res) {
        const up = await fetch(env.UMAMI_SCRIPT_URL || UMAMI_SCRIPT);
        if (!up.ok) return new Response("", { status: 502 });
        res = new Response(await up.text(), { headers: { "content-type": "application/javascript; charset=utf-8",
                                                         "cache-control": "public, max-age=21600" } });
        ctx.waitUntil(cache.put(key, res.clone()));
      }
      return res;
    }
    if (pathname === "/u/api/send") {
      try {
        const f = await forwardSend(request, env.UMAMI_SEND_URL || undefined);
        if (!f.request) return new Response("", { status: f.status });
        const up = await fetch(f.request);
        return new Response(up.body, { status: up.status, headers: { "content-type": up.headers.get("content-type") || "text/plain",
                                                                       "cache-control": "no-store" } });
      } catch (e) { return new Response("", { status: 502 }); }
    }
    if (pathname.startsWith("/flights/")) {
      try { return await flightPage(request, env, ctx); }
      catch (e) { return new Response("Something went wrong.", { status: 500 }); }
    }
    if (pathname === "/sitemap-flights.xml") return flightSitemap(request, env);
    if (pathname === "/api/lookup") {
      try { return await lookup(request, env, ctx); }
      catch (e) { return json({ error: "internal_error" }, 500); }
    }
    return env.ASSETS.fetch(request);
  },
};
