// Server-rendered flight pages (/flights/6e-1234/). Pure functions so they can be unit-tested.
// The page shell (head, nav, footer, styles) comes from /_tpl/flight.html, written by the site build.
import { parseFlight } from "./match.js";

const INDIAN = new Set(["6E", "IGO", "AI", "AIC", "IX", "AXB", "QP", "AKJ", "SG", "SEJ"]);

export const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function slugFor(flight) {
  return `${flight.carrier.toLowerCase()}-${flight.number.toLowerCase()}`;
}

export function parseSlug(slug) {
  const m = /^([a-z0-9]{2}|[a-z]{3})-?(\d{1,4}[a-z]?)$/i.exec(String(slug || ""));
  return m ? parseFlight(m[1] + m[2]) : null;
}

export const displayCode = (flight) => `${flight.carrier} ${flight.number}`;

// "Today" for a flight: India time for Indian airlines, UTC otherwise (close enough for European departures).
export function todayFor(carrier, now = new Date()) {
  const offsetMin = INDIAN.has(String(carrier).toUpperCase()) ? 330 : 0;
  return new Date(now.getTime() + offsetMin * 60000).toISOString().slice(0, 10);
}

export function airlineFor(index, carrier) {
  const c = String(carrier).toUpperCase();
  const cfg = index.configs.find((x) => x.operator.iata === c || x.operator.icao === c ||
    (x.group_iata || []).includes(c) || (x.group_icao || []).includes(c));
  return cfg ? cfg.operator : null;
}

const TIER = { exact: "Exact match", likely: "Likely", unavailable: "Not covered yet" };

function legHtml(code, leg) {
  const m = leg.match;
  const cands = m.candidates.map((c) => `<li><a href="${esc(c.path)}">${esc(c.label)}</a> · ` +
    `<a class="luckylink" href="${esc(c.path)}#lucky">lucky seat</a>` +
    (m.tier === "exact" ? "" : c.probability >= 0.995 && !m.uncovered_share ? " — the only version we know of"
      : ` — about ${Math.round(c.probability * 100)}%`) + "</li>").join("") +
    (m.uncovered_share ? `<li>Other versions we haven't mapped yet — about ${Math.round(m.uncovered_share * 100)}%</li>` : "");
  return `<div class="result"><p class="result-head"><strong>${esc(code)}</strong> ${esc(leg.origin || "?")} → ${esc(leg.destination || "?")}` +
    (leg.departure_local ? ` · ${esc(leg.departure_local)}` : "") + "</p>" +
    `<p>${esc((leg.operator && leg.operator.name) || "")} · ${esc(leg.aircraft_model || "aircraft not published yet")}` +
    (leg.registration ? ` · registration ${esc(leg.registration)}` : "") + "</p>" +
    `<p><span class="badge tier-${esc(m.tier)}">${esc(TIER[m.tier] || m.tier)}</span> ${esc(m.reason)}</p>` +
    (cands ? `<ul>${cands}</ul>` : "") + "</div>";
}

function historyHtml(history, index) {
  if (!history.length) return "";
  const label = (id) => { const c = index.configs.find((x) => x.id === id); return c ? `<a href="${esc(c.path)}">${esc(c.label)}</a>` : "—"; };
  const rows = history.map((h) => `<tr><td>${esc(h.date)}</td><td>${esc(h.origin || "?")} → ${esc(h.destination || "?")}</td>` +
    `<td>${esc(h.aircraft_model || "?")}</td><td>${esc(h.registration || "not assigned yet")}</td><td>${label(h.config_id)}</td></tr>`).join("");
  return `<section><h2>Aircraft seen on this flight</h2>
<p class="muted small">From lookups made on this site, so not every day is listed.</p>
<div class="tablewrap"><table class="facts hist"><thead><tr><th>Date</th><th>Route</th><th>Aircraft</th><th>Registration</th><th>Most likely seat map</th></tr></thead>
<tbody>${rows}</tbody></table></div></section>`;
}

// Returns { html, indexable, title }. A page is indexable only when it has real content about the flight.
export function renderFlightPage(tpl, { flight, date, today, result, history, index, liveNote, slug, origin = "" }) {
  const code = displayCode(flight);
  const airline = airlineFor(index, flight.carrier);
  const airlineName = airline ? airline.name : flight.carrier;
  const legs = (result && result.legs) || [];
  const first = legs[0] || history[0] || null;
  const route = first && first.origin ? ` ${first.origin} → ${first.destination}` : "";
  const title = `${code}${route} seat map: which plane flies it | MyLuckySeat`;
  const description = `${airlineName} flight ${code}${route}: the aircraft scheduled, which cabin version it's likely to have, ` +
    `its seat map with exit rows, and your lucky seat on it.`;

  const live = legs.length ? legs.map((l) => legHtml(code, l)).join("")
    : `<p class="note">${esc(liveNote || "No flight found for this number on this date.")}</p>`;
  const seatMaps = index.configs.filter((c) => airline && c.operator.iata === airline.iata)
    .map((c) => `<li><a href="${esc(c.path)}">${esc(c.label)}</a></li>`).join("");

  const band = `<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a> › <a href="/#lookup">Flights</a> › ${esc(code)}</nav>
<h1>${esc(code)}${esc(route)} seat map</h1>
<p class="lede">Which plane ${esc(airlineName)} flight ${esc(code)} uses, which cabin version that usually means, and the seat map to go with it.</p>`;
  const body = `<section class="lookup"><h2>${date === today ? "Today" : esc(date)}: ${esc(code)}</h2>
${live}
<form class="inline-form" method="get" action="/flights/${esc(slug)}/">
<label>Check another date <input type="date" name="date" value="${esc(date)}" required></label>
<button type="submit">Show</button></form>
<p class="muted small">Flight data: AeroDataBox. Airlines swap aircraft on the day; the seat map at check-in is final.</p></section>
${historyHtml(history, index)}
${seatMaps ? `<section><h2>${esc(airlineName)} seat maps</h2><ul>${seatMaps}</ul></section>` : ""}
<section><h2>Find your lucky seat</h2><p>Open the seat map above and use the lucky seat finder: Vedic moon sign, Western sun sign or Chinese zodiac, just for fun.</p></section>`;

  const jsonld = [{
    "@context": "https://schema.org", "@type": "BreadcrumbList", itemListElement: [
      { "@type": "ListItem", position: 1, name: "Home", item: `${origin}/` },
      { "@type": "ListItem", position: 2, name: code, item: `${origin}/flights/${slug}/` }]
  }];
  const leg = legs[0];
  if (leg) {
    jsonld.push({
      "@context": "https://schema.org", "@type": "Flight", flightNumber: flight.code,
      provider: { "@type": "Airline", name: airlineName, iataCode: airline ? airline.iata : flight.carrier },
      ...(leg.origin ? { departureAirport: { "@type": "Airport", iataCode: leg.origin } } : {}),
      ...(leg.destination ? { arrivalAirport: { "@type": "Airport", iataCode: leg.destination } } : {}),
      ...(leg.aircraft_model ? { aircraft: leg.aircraft_model } : {}),
      ...(leg.departure_local ? { departureTime: leg.departure_local.replace(" ", "T") } : {}),
    });
  }
  const indexable = Boolean(airline) && (legs.length > 0 || history.length > 0) && date === today;
  const ld = jsonld.map((b) => `<script type="application/ld+json">${JSON.stringify(b).replace(/</g, "\\u003c")}</script>`).join("\n");
  const html = tpl
    .replaceAll("__TITLE__", esc(title))
    .replaceAll("__DESC__", esc(description))
    .replaceAll("__SLUG__", esc(slug))
    .replace("index,follow,max-snippet:-1,max-image-preview:large", indexable ? "index,follow,max-snippet:-1,max-image-preview:large" : "noindex,follow")
    .replace("<!--JSONLD-->", ld)
    .replace("<!--BAND-->", band)
    .replace("<!--BODY-->", body);
  return { html, indexable, title };
}

export function sitemapXml(siteUrl, slugs) {
  const urls = slugs.map((s) => `<url><loc>${esc(siteUrl)}/flights/${esc(s)}/</loc><changefreq>daily</changefreq></url>`).join("");
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>\n`;
}
