// Cloudflare Worker: serves /api/lookup; everything else is static assets.
// Secrets:  AERODATABOX_RAPIDAPI_KEY   (wrangler secret put AERODATABOX_RAPIDAPI_KEY)
// Dev only: ADB_MOCK=1 returns synthetic flights (never set in production).
import { DurableObject } from "cloudflare:workers";
import { buildResult, knownCarrier, parseAdb, parseFlight, validDate } from "./match.js";
import { cleanFeedback } from "./feedback.js";

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

  const cacheKey = new Request(`https://lookup-cache.internal/v1/${flight.code}/${date}`);
  const cache = caches.default;
  const hit = await cache.match(cacheKey);
  if (hit) return new Response(hit.body, { status: hit.status, headers: { ...Object.fromEntries(hit.headers), "x-cache": "HIT" } });

  // Rate limits protect the API quota (only reached on cache misses).
  const ip = request.headers.get("cf-connecting-ip") || "anon";
  if (env.LOOKUP_PER_IP) {
    const { success } = await env.LOOKUP_PER_IP.limit({ key: `ip:${ip}` });
    if (!success) return json({ error: "rate_limited", message: "Too many lookups. Try again in a minute." }, 429, { "retry-after": "60" });
  }
  if (env.LOOKUP_GLOBAL) {
    const { success } = await env.LOOKUP_GLOBAL.limit({ key: "global" });
    if (!success) return json({ error: "busy", message: "Lookups are busy right now. Try again in a minute." }, 429, { "retry-after": "60" });
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
    return json({ error: "upstream_error", message, upstream_status: st }, 502);
  }
  const result = { ...buildResult(query, parseAdb(payload), index), source: "AeroDataBox", fetched_at: new Date().toISOString() };
  const daysAway = (Date.parse(date + "T00:00:00Z") - Date.now()) / 86400000;
  const ttl = daysAway < 2 ? 1800 : 6 * 3600;    // registrations appear close to departure
  const res = json(result, 200, { "cache-control": `public, max-age=${ttl}` });
  ctx.waitUntil(cache.put(cacheKey, res.clone()));
  return new Response(res.body, { status: 200, headers: { ...Object.fromEntries(res.headers), "x-cache": "MISS" } });
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
    if (pathname === "/api/lookup") {
      try { return await lookup(request, env, ctx); }
      catch (e) { return json({ error: "internal_error" }, 500); }
    }
    return env.ASSETS.fetch(request);
  },
};
