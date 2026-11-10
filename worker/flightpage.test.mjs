import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseAdb, buildResult } from "./match.js";
import { parseSlug, renderFlightPage, sitemapXml, slugFor, todayFor } from "./flightpage.js";

const SITE = new URL("../site/", import.meta.url);
const index = JSON.parse(readFileSync(new URL("api/v1/match-index.json", SITE), "utf8"));
const tpl = readFileSync(new URL("_tpl/flight.tpl", SITE), "utf8");

const adb = [{ number: "6E 1234", codeshareStatus: "IsOperator", airline: { iata: "6E", icao: "IGO", name: "IndiGo" },
  aircraft: { model: "Airbus A321 NEO", reg: "VT-NOF" }, departure: { airport: { iata: "KWI" }, scheduledTime: { local: "2026-11-11 23:45+03:00" } },
  arrival: { airport: { iata: "HYD" } } }];

test("slugs round-trip and normalise", () => {
  assert.equal(slugFor(parseSlug("6e-1234")), "6e-1234");
  assert.equal(slugFor(parseSlug("6E1234")), "6e-1234");
  assert.equal(slugFor(parseSlug("fr-0012")), "fr-12");
  assert.equal(parseSlug("../etc"), null);
  assert.equal(parseSlug("hello-world"), null);
});

test("today uses India time for Indian airlines", () => {
  const now = new Date("2026-11-10T20:00:00Z");     // 01:30 on 11 Nov in India
  assert.equal(todayFor("6E", now), "2026-11-11");
  assert.equal(todayFor("FR", now), "2026-11-10");
});

test("flight page renders live result, history, JSON-LD and stays indexable", () => {
  const flight = parseSlug("6e-1234");
  const result = buildResult({ flight: "6E1234", date: "2026-11-11" }, parseAdb(adb), index);
  const history = [{ date: "2026-11-10", origin: "KWI", destination: "HYD", aircraft_model: "Airbus A321 NEO", registration: "VT-NOE", config_id: "6E-A21N-232Y" }];
  const p = renderFlightPage(tpl, { flight, date: "2026-11-11", today: "2026-11-11", result, history, index, slug: "6e-1234", origin: "https://x.test" });
  assert.ok(p.indexable);
  assert.equal((p.html.match(/<h1/g) || []).length, 1);
  assert.match(p.html, /<title>6E 1234 KWI → HYD seat map/);
  assert.match(p.html, /VT-NOF/);
  assert.match(p.html, /VT-NOE/);
  assert.match(p.html, /"@type":"Flight"/);
  assert.match(p.html, /href="https:\/\/x\.test\/flights\/6e-1234\/"|example\.com\/flights\/6e-1234\//);
  assert.ok(!/__TITLE__|__DESC__|__SLUG__|<!--BODY-->|<!--BAND-->|<!--JSONLD-->/.test(p.html));
  assert.ok(!/ style="/.test(p.html), "inline style breaks CSP");
  for (const m of p.html.matchAll(/<script([^>]*)>/g)) assert.ok(/src=|application\/ld\+json|application\/json/.test(m[1]), m[0]);
});

test("pages with nothing to show, other dates and unknown airlines are noindex", () => {
  const flight = parseSlug("6e-9999");
  const empty = renderFlightPage(tpl, { flight, date: "2026-11-11", today: "2026-11-11", result: { legs: [] }, history: [], index, slug: "6e-9999" });
  assert.ok(!empty.indexable);
  assert.match(empty.html, /noindex,follow/);
  const result = buildResult({ flight: "6E1234", date: "2026-11-12" }, parseAdb(adb), index);
  const other = renderFlightPage(tpl, { flight: parseSlug("6e-1234"), date: "2026-11-12", today: "2026-11-11", result, history: [], index, slug: "6e-1234" });
  assert.ok(!other.indexable);
  const qr = renderFlightPage(tpl, { flight: parseSlug("qr-1"), date: "2026-11-11", today: "2026-11-11", result: null, history: [], index, slug: "qr-1", liveNote: "We don't have seat maps for this airline yet." });
  assert.ok(!qr.indexable);
});

test("text from the provider is escaped", () => {
  const evil = [{ ...adb[0], aircraft: { model: "<script>x</script>" } }];
  const result = buildResult({ flight: "6E1234", date: "2026-11-11" }, parseAdb(evil), index);
  const p = renderFlightPage(tpl, { flight: parseSlug("6e-1234"), date: "2026-11-11", today: "2026-11-11", result, history: [], index, slug: "6e-1234" });
  assert.ok(!p.html.includes("<script>x</script>"));
});

test("flight sitemap", () => {
  const x = sitemapXml("https://x.test", ["6e-1234", "fr-12"]);
  assert.match(x, /<loc>https:\/\/x\.test\/flights\/6e-1234\/<\/loc>/);
  assert.match(x, /^<\?xml/);
});
