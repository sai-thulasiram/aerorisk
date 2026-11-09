"""Flight-number + date -> operating flight instance(s).

Provider: AeroDataBox (via RapidAPI). Requires AERODATABOX_RAPIDAPI_KEY.

Endpoint, headers and field names checked against the OpenAPI-generated
client github.com/lucasbstn/aerodatabox (generated July 2024) and the
apis.io listing of the AeroDataBox Flight API:
  GET https://aerodatabox.p.rapidapi.com/flights/number/{flightNumber}/{dateLocal}
  headers X-RapidAPI-Key, X-RapidAPI-Host
  -> list[FlightContract]: number, status, codeshareStatus (Unknown | IsOperator | IsCodeshared),
     departure/arrival.airport{icao,iata,name}, aircraft{reg, modeS, model}, airline{name, iata, icao}
`aircraft` and `airline` are optional; registration is often absent for future flights.
Not yet exercised against the live API (no key in this environment). The same API is also
sold via api.market (base https://prod.api.market/api/v1/aedbx/aerodatabox) with different auth.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any

ADB_HOST = "aerodatabox.p.rapidapi.com"

# Free-text model names from flight APIs -> ICAO type designators.
MODEL_TO_ICAO = [
    (r"737\s*MAX\s*8|737-8(-200)?\b(?!\d)|737-8200|B38M", "B38M"),
    (r"737-800|737-8[0-9A-Z]{2}\b|\bB738\b", "B738"),
    (r"A320\s*-?\s*NEO|A320N|A20N|A320-2\d\dN", "A20N"),
    (r"A321\s*-?\s*NEO|A321N|A21N|A321-2\d\dNX?", "A21N"),
    (r"A320(?!\s*-?\s*NEO)|\bA320\b", "A320"),
    (r"A321(?!\s*-?\s*NEO)", "A321"),
    (r"ATR\s*72", "AT76"),
]

FLIGHT_RE = re.compile(r"^\s*([A-Z0-9]{2}|[A-Z]{3})\s*0*(\d{1,4})([A-Z]?)\s*$", re.I)


def parse_flight_number(text: str) -> tuple[str, str]:
    """'6E 2134' -> ('6E', '2134'); 'FR1234' -> ('FR', '1234')."""
    m = FLIGHT_RE.match(text.upper())
    if not m:
        raise ValueError(f"Unrecognised flight number: {text!r}")
    return m.group(1), m.group(2) + m.group(3)


def model_to_icao(model: str | None) -> str | None:
    if not model:
        return None
    for pattern, code in MODEL_TO_ICAO:
        if re.search(pattern, model, re.I):
            return code
    return None


@dataclass
class FlightInstance:
    flight_number: str
    date: str
    operator_iata: str | None
    operator_icao: str | None
    operator_name: str | None
    origin: str | None
    destination: str | None
    aircraft_model: str | None
    aircraft_icao: str | None
    registration: str | None
    is_operator_leg: bool
    status: str | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_aerodatabox(payload: Any, flight_number: str, date: str) -> list[FlightInstance]:
    items = payload if isinstance(payload, list) else (payload or {}).get("items", [payload])
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        airline = it.get("airline") or {}
        aircraft = it.get("aircraft") or {}
        dep = (it.get("departure") or {}).get("airport") or {}
        arr = (it.get("arrival") or {}).get("airport") or {}
        model = aircraft.get("model")
        cs = (it.get("codeshareStatus") or "").lower()
        out.append(FlightInstance(
            flight_number=it.get("number") or flight_number,
            date=date,
            operator_iata=airline.get("iata"),
            operator_icao=airline.get("icao"),
            operator_name=airline.get("name"),
            origin=dep.get("iata") or dep.get("icao"),
            destination=arr.get("iata") or arr.get("icao"),
            aircraft_model=model,
            aircraft_icao=model_to_icao(model),
            registration=aircraft.get("reg"),
            is_operator_leg=cs != "iscodeshared",
            status=it.get("status"),
            source="aerodatabox",
        ))
    # operating legs first
    out.sort(key=lambda f: (not f.is_operator_leg,))
    return out


def resolve_flight(flight: str, date: str, *, api_key: str | None = None,
                   timeout: float = 15.0) -> list[FlightInstance]:
    key = api_key or os.environ.get("AERODATABOX_RAPIDAPI_KEY")
    if not key:
        raise RuntimeError("Set AERODATABOX_RAPIDAPI_KEY (RapidAPI key for AeroDataBox) or use --fixture.")
    carrier, num = parse_flight_number(flight)
    path = f"/flights/number/{urllib.parse.quote(carrier + num)}/{urllib.parse.quote(date)}"
    req = urllib.request.Request(
        f"https://{ADB_HOST}{path}?withAircraftImage=false&withLocation=false",
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": ADB_HOST, "Accept": "application/json",
                 "User-Agent": "aerorisk-explorer/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status == 204:
            return []
        payload = json.loads(resp.read().decode())
    return parse_aerodatabox(payload, carrier + num, date)


def load_fixture(path: str, flight: str, date: str) -> list[FlightInstance]:
    with open(path) as fh:
        return parse_aerodatabox(json.load(fh), flight, date)
