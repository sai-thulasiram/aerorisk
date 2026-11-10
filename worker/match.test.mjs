import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildResult, knownCarrier, matchLeg, modelToIcao, parseAdb, parseFlight, validDate } from "./match.js";

const INDEX_PATH = process.env.MATCH_INDEX || new URL("../site/api/v1/match-index.json", import.meta.url);
const index = JSON.parse(readFileSync(INDEX_PATH, "utf8"));

const leg = (over) => ({ operator: { iata: "6E", icao: "IGO", name: "IndiGo" }, aircraft_model: "Airbus A320 NEO",
  aircraft_icao: "A20N", registration: null, is_operator_leg: true, ...over });

test("parses flight numbers", () => {
  assert.deepEqual(parseFlight("6E 2134"), { carrier: "6E", number: "2134", code: "6E2134" });
  assert.equal(parseFlight("fr0012").code, "FR12");
  assert.equal(parseFlight("hello"), null);
});

test("validates dates", () => {
  const now = new Date("2026-11-10T12:00:00Z");
  assert.ok(validDate("2026-11-10", now));
  assert.ok(!validDate("2026-10-01", now));
  assert.ok(!validDate("2026-13-01", now));
  assert.ok(!validDate("10/11/2026", now));
});

test("maps model names like the Python matcher", () => {
  for (const [m, c] of [["Boeing 737-8AS", "B738"], ["Boeing 737-800", "B738"], ["Boeing 737 MAX 8-200", "B38M"],
                        ["Airbus A320 NEO", "A20N"], ["A320-271N", "A20N"], ["Airbus A321-271NX", "A21N"],
                        ["Airbus A320", "A320"], ["Boeing 787-9", null]]) assert.equal(modelToIcao(m), c, m);
});

test("IndiGo A320neo ranks the two mapped versions", () => {
  const m = matchLeg(leg({}), index);
  assert.equal(m.tier, "likely");
  assert.deepEqual(m.candidates.map((c) => c.config_id), ["6E-A20N-186Y", "6E-A20N-180Y"]);
  assert.equal(m.uncovered_share, 0);
});

test("IndiGo A321neo reports the unmapped share instead of claiming 100%", () => {
  const m = matchLeg(leg({ aircraft_model: "Airbus A321 NEO", aircraft_icao: "A21N" }), index);
  assert.equal(m.candidates[0].config_id, "6E-A21N-232Y");
  assert.ok(m.candidates[0].probability < 0.7);
  assert.ok(m.uncovered_share > 0.3);
});

test("group operators match (easyJet Europe)", () => {
  const m = matchLeg(leg({ operator: { iata: "EC", icao: "EJU", name: "easyJet Europe" } }), index);
  assert.equal(m.candidates[0].config_id, "U2-A20N-186Y");
});

test("unmapped types and missing types are unavailable", () => {
  assert.equal(matchLeg(leg({ aircraft_icao: null }), index).tier, "unavailable");
  assert.equal(matchLeg(leg({ aircraft_icao: "B789" }), index).tier, "unavailable");
});

test("exact only via verified tail", () => {
  const idx = { ...index, tails: { "VT-TST": "6E-A20N-180Y" } };
  const m = matchLeg(leg({ registration: "VT-TST" }), idx);
  assert.equal(m.tier, "exact");
  assert.equal(m.candidates[0].config_id, "6E-A20N-180Y");
});

test("known carriers gate API calls", () => {
  assert.ok(knownCarrier(index, "6E"));
  assert.ok(knownCarrier(index, "EJU"));
  assert.ok(!knownCarrier(index, "QR"));
  assert.ok(knownCarrier(index, "AI") && knownCarrier(index, "IX") && knownCarrier(index, "W6"));
  assert.ok(knownCarrier(index, "QP") && knownCarrier(index, "SG") && knownCarrier(index, "AKJ"));
});

test("codeshare legs are dropped in favour of the operating leg", () => {
  const payload = [
    { number: "QR 4774", codeshareStatus: "IsCodeshared", airline: { iata: "QR", icao: "QTR" }, aircraft: { model: "Airbus A320 NEO" } },
    { number: "6E 1", codeshareStatus: "IsOperator", airline: { iata: "6E", icao: "IGO", name: "IndiGo" },
      aircraft: { model: "Airbus A320 NEO", reg: "vt-abc" }, departure: { airport: { iata: "BLR" } }, arrival: { airport: { iata: "DEL" } } },
  ];
  const r = buildResult({ flight: "6E1", date: "2026-12-01" }, parseAdb(payload), index);
  assert.equal(r.legs.length, 1);
  assert.equal(r.legs[0].registration, "VT-ABC");
  assert.equal(r.legs[0].match.tier, "likely");
});

test("only departures on the requested date are kept", () => {
  const mk = (local, model) => ({ number: "6E 1234", codeshareStatus: "IsOperator", airline: { iata: "6E", icao: "IGO", name: "IndiGo" },
    aircraft: { model }, departure: { airport: { iata: "KWI" }, scheduledTime: { local } }, arrival: { airport: { iata: "HYD" } } });
  const r = buildResult({ flight: "6E1234", date: "2026-11-10" },
    parseAdb([mk("2026-11-09 23:45+03:00", "Airbus A321 NEO"), mk("2026-11-10 23:45+03:00", "Airbus A320")]), index);
  assert.equal(r.legs.length, 1);
  assert.equal(r.legs[0].departure_local.slice(0, 10), "2026-11-10");
  const none = buildResult({ flight: "6E1234", date: "2026-11-12" }, parseAdb([mk("2026-11-09 23:45+03:00", "Airbus A321 NEO")]), index);
  assert.equal(none.legs.length, 1);
});

test("Indian additions match: Akasa MAX 8, SpiceJet 737-800, Air India A321neo", () => {
  const qp = matchLeg(leg({ operator: { iata: "QP", icao: "AKJ", name: "Akasa Air" }, aircraft_model: "Boeing 737 MAX 8", aircraft_icao: "B38M" }), index);
  assert.equal(qp.candidates[0].config_id, "QP-B38M-189Y");
  assert.match(qp.reason, /share is unknown/);
  const sg = matchLeg(leg({ operator: { iata: "SG", icao: "SEJ", name: "SpiceJet" }, aircraft_model: "Boeing 737-800", aircraft_icao: "B738" }), index);
  assert.equal(sg.candidates[0].config_id, "SG-B738-189Y");
  const ai = matchLeg(leg({ operator: { iata: "AI", icao: "AIC", name: "Air India" }, aircraft_model: "Airbus A321 NEO", aircraft_icao: "A21N" }), index);
  assert.equal(ai.candidates[0].config_id, "AI-A21N-189");
});
