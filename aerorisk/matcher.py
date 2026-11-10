"""Match a resolved flight instance to a cabin configuration with an honest confidence tier.

Tiers (never mixed in KPIs):
  exact       – registration is in the hand-verified tail map.
  likely      – operator + type known; ranked candidates with fleet-share priors.
  generic     – type known but no operator-specific configuration (not yet authored).
  unavailable – type unknown or not covered.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .fleet import OpenSkyFleet, TailOverride, load_tail_overrides, norm_reg
from .flights import FlightInstance
from .layout import list_configs


@dataclass
class MatchResult:
    tier: str
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    aircraft_icao: str | None = None
    registration: str | None = None
    uncovered_share: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _operator_matches(cfg: dict[str, Any], fi: FlightInstance) -> bool:
    op = cfg["operator"]
    icaos = {op["icao"], *cfg.get("group_operators_icao", [])}
    iatas = {op["iata"], *cfg.get("group_operators_iata", [])}
    return (fi.operator_icao in icaos) or bool(fi.operator_iata and fi.operator_iata in iatas)


def match(fi: FlightInstance, *, configs: list[dict[str, Any]] | None = None,
          tails: dict[str, TailOverride] | None = None,
          fleet: OpenSkyFleet | None = None) -> MatchResult:
    configs = configs if configs is not None else list_configs()
    tails = tails if tails is not None else load_tail_overrides()
    by_id = {c["id"]: c for c in configs}
    reg = norm_reg(fi.registration)

    # 1. exact: verified tail mapping
    if reg and reg in tails and tails[reg].config_id in by_id:
        t = tails[reg]
        return MatchResult("exact", f"Registration {reg} verified to {t.config_id} ({t.source}, {t.verified_at}).",
                           [{"config_id": t.config_id, "probability": 1.0}], by_id[t.config_id]["aircraft"]["icao"], reg)

    # 2. aircraft type: from flight provider, else from registration via OpenSky
    type_icao = fi.aircraft_icao
    type_source = "flight provider"
    if not type_icao and fleet and reg:
        rec = fleet.lookup(reg)
        if rec and rec.typecode:
            type_icao, type_source = rec.typecode, "OpenSky registry"
    if not type_icao:
        return MatchResult("unavailable", "Aircraft type not known for this flight yet; recheck closer to departure.",
                           registration=reg)

    cands = [c for c in configs if c["aircraft"]["icao"] == type_icao and _operator_matches(c, fi)]
    if not cands:
        generic = [c for c in configs if c["aircraft"]["icao"] == type_icao and c.get("generic")]
        if generic:
            return MatchResult("generic", f"No {fi.operator_name or fi.operator_iata} layout authored for {type_icao}; "
                                          "showing a generic type layout.",
                               [{"config_id": g["id"], "probability": None} for g in generic], type_icao, reg)
        return MatchResult("unavailable", f"{type_icao} for {fi.operator_name or fi.operator_iata} is not covered yet.",
                           aircraft_icao=type_icao, registration=reg)

    # fleet_share_prior is the share of this operator's fleet of this type that uses the config.
    # If the authored configs cover less than 100%, the remainder is reported, never redistributed.
    total = sum(c.get("fleet_share_prior", 1.0) for c in cands) or 1.0
    scale = 1.0 / total if total > 1.0 else 1.0
    ranked = sorted(({"config_id": c["id"], "variant": c["variant_label"],
                      "probability": round(c.get("fleet_share_prior", 1.0) * scale, 3)} for c in cands),
                    key=lambda d: -d["probability"])
    uncovered = round(max(0.0, 1.0 - sum(r["probability"] for r in ranked)), 3)
    why = (f"Type {type_icao} from {type_source}; "
           + ("single known layout for this operator/type" if len(ranked) == 1 and not uncovered
              else f"{len(ranked)} mapped variant(s), ranked by fleet share")
           + (f"; {round(uncovered * 100)}% of this fleet uses layouts not mapped yet" if uncovered else "")
           + ("" if reg else "; registration not yet assigned")
           + ". Exact only once the tail is verified.")
    return MatchResult("likely", why, ranked, type_icao, reg, uncovered)
