// Pure matching logic for the flight-lookup Worker. Mirrors aerorisk/matcher.py and aerorisk/flights.py.
// No I/O here so it can be unit-tested with `node --test worker/`.

export const MODEL_TO_ICAO = [
  [/737\s*MAX\s*8|737-8(-200)?\b(?!\d)|737-8200|B38M/i, "B38M"],
  [/737-800|737-8[0-9A-Z]{2}\b|\bB738\b/i, "B738"],
  [/A320\s*-?\s*NEO|A320N|A20N|A320-2\d\dN/i, "A20N"],
  [/A321\s*-?\s*NEO|A321N|A21N|A321-2\d\dNX?/i, "A21N"],
  [/A320(?!\s*-?\s*NEO)|\bA320\b/i, "A320"],
  [/A321(?!\s*-?\s*NEO)/i, "A321"],
  [/ATR\s*72/i, "AT76"],
];

const FLIGHT_RE = /^\s*([A-Z0-9]{2}|[A-Z]{3})\s*0*(\d{1,4})([A-Z]?)\s*$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function parseFlight(text) {
  const m = FLIGHT_RE.exec(String(text || "").toUpperCase());
  if (!m) return null;
  return { carrier: m[1], number: m[2] + m[3], code: m[1] + m[2] + m[3] };
}

export function validDate(text, now = new Date()) {
  if (!DATE_RE.test(text || "")) return false;
  const d = new Date(text + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return false;
  const days = (d.getTime() - Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())) / 86400000;
  return days >= -7 && days <= 330;
}

export function modelToIcao(model) {
  if (!model) return null;
  for (const [re, code] of MODEL_TO_ICAO) if (re.test(model)) return code;
  return null;
}

export const normReg = (r) => (r ? String(r).toUpperCase().replace(/\s+/g, "") : null);

// Is this carrier code one we have any configuration for? Used to avoid spending API quota.
export function knownCarrier(index, carrier) {
  const c = String(carrier || "").toUpperCase();
  return index.configs.some((x) => x.operator.iata === c || x.operator.icao === c ||
    (x.group_icao || []).includes(c) || (x.group_iata || []).includes(c));
}

export function parseAdb(payload) {
  const items = Array.isArray(payload) ? payload : (payload && payload.items) || [];
  return items.filter((it) => it && typeof it === "object").map((it) => {
    const airline = it.airline || {}, aircraft = it.aircraft || {};
    const dep = it.departure || {}, arr = it.arrival || {};
    const time = (mv) => (mv.scheduledTime && (mv.scheduledTime.local || mv.scheduledTime.utc)) || mv.scheduledTimeLocal || null;
    return {
      number: it.number || null,
      status: it.status || null,
      operator: { iata: airline.iata || null, icao: airline.icao || null, name: airline.name || null },
      origin: (dep.airport && (dep.airport.iata || dep.airport.icao)) || null,
      destination: (arr.airport && (arr.airport.iata || arr.airport.icao)) || null,
      departure_local: time(dep),
      aircraft_model: aircraft.model || null,
      aircraft_icao: modelToIcao(aircraft.model),
      registration: normReg(aircraft.reg),
      is_operator_leg: String(it.codeshareStatus || "").toLowerCase() !== "iscodeshared",
    };
  });
}

function operatorMatches(cfg, leg) {
  const icaos = [cfg.operator.icao, ...(cfg.group_icao || [])];
  const iatas = [cfg.operator.iata, ...(cfg.group_iata || [])];
  return Boolean((leg.operator.icao && icaos.includes(leg.operator.icao)) ||
                 (leg.operator.iata && iatas.includes(leg.operator.iata)));
}

export function matchLeg(leg, index) {
  const configs = index.configs, tails = index.tails || {};
  const reg = normReg(leg.registration);
  const opName = leg.operator.name || leg.operator.iata || "this airline";
  const link = (c) => ({ config_id: c.id, label: c.label, path: c.path, status: c.status });

  if (reg && tails[reg]) {
    const c = configs.find((x) => x.id === tails[reg]);
    if (c) return { tier: "exact", reason: `Registration ${reg} is verified to this cabin.`,
                    candidates: [{ ...link(c), probability: 1 }], uncovered_share: 0 };
  }
  const type = leg.aircraft_icao;
  if (!type) {
    return { tier: "unavailable", reason: "The aircraft type isn't published for this flight yet. Check again closer to departure.",
             candidates: [], uncovered_share: 0 };
  }
  const cands = configs.filter((c) => c.aircraft_icao === type && operatorMatches(c, leg));
  if (!cands.length) {
    return { tier: "unavailable", reason: `${opName} ${leg.aircraft_model || type}: we haven't mapped this cabin yet.`,
             candidates: [], uncovered_share: 0 };
  }
  const total = cands.reduce((a, c) => a + (c.share ?? 1), 0) || 1;
  const scale = total > 1 ? 1 / total : 1;
  const ranked = cands.map((c) => ({ ...link(c), probability: Math.round((c.share ?? 1) * scale * 1000) / 1000 }))
    .sort((a, b) => b.probability - a.probability);
  const uncovered = Math.max(0, Math.round((1 - ranked.reduce((a, r) => a + r.probability, 0)) * 1000) / 1000);
  let reason = ranked.length === 1 && !uncovered
    ? `${opName} flies one known cabin version of this aircraft.`
    : `${opName} flies more than one cabin version of this aircraft; ranked by share of the fleet.`;
  if (uncovered) reason += ` About ${Math.round(uncovered * 100)}% of this fleet uses versions we haven't mapped yet.`;
  reason += reg ? ` Registration ${reg} is not verified yet.` : " The registration is usually assigned close to departure.";
  return { tier: "likely", reason, candidates: ranked, uncovered_share: uncovered };
}

export function buildResult(query, legs, index) {
  const operating = legs.filter((l) => l.is_operator_leg);
  let use = operating.length ? operating : legs;
  // The provider can return neighbouring days (e.g. a late-night departure the day before). Keep the
  // departures on the requested local date; if none match, keep everything rather than show nothing.
  const sameDay = use.filter((l) => String(l.departure_local || "").startsWith(query.date));
  if (sameDay.length) use = sameDay;
  return {
    query,
    legs: use.map((leg) => ({
      origin: leg.origin, destination: leg.destination, departure_local: leg.departure_local,
      operator: leg.operator, aircraft_model: leg.aircraft_model, registration: leg.registration, status: leg.status,
      match: matchLeg(leg, index),
    })),
    codeshare_of: operating.length ? null : (legs[0] && legs[0].operator) || null,
  };
}
