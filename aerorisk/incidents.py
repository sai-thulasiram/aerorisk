"""Collect and summarise airline incident data from open, licensed sources.

Sources (all public and openly licensed; run daily by the deploy workflow):
  * Transport Canada CADORS: daily occurrence reports for everything in Canadian airspace, including foreign
    airlines. Open Government Licence - Canada. Published as five CSV files; we use three.
  * UK AAIB reports via the GOV.UK search API. Open Government Licence v3.0.

Curated files that sit next to the generated ones (edited by hand, every number sourced):
  * india_official.json  answers given in India's Parliament
  * global_iata.json     IATA annual safety report

If a source fails, its previous snapshot is kept and status.json records the error, so a site build never
breaks because a government server is down.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import sys
import tempfile
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "incidents"
UA = "MyLuckySeat incident stats (+https://myluckyseat.in)"

CADORS_BASE = "https://opendatatc.tc.canada.ca/CADORS_{}.csv"
CADORS_PAGE = "https://open.canada.ca/data/en/dataset/a348c1d1-2392-4595-b5e2-c6a244a7e87f"
AAIB_API = ("https://www.gov.uk/api/search.json?filter_format=aaib_report&filter_aircraft_category=commercial-fixed-wing"
            "&count=40&order=-public_timestamp&fields=title,link,public_timestamp,report_type,date_of_occurrence,"
            "aircraft_type,location,description")

# CADORS event names grouped into the headline groups shown on the page.
EVENT_GROUPS: dict[str, tuple[str, ...]] = {
    "declared_emergency": ("Declared emergency/priority",),
    "landing_gear": ("Landing gear", "Blown tire/wheel failure"),
    "engine": ("Engine shut down", "Engine failure", "Engine - malfunction"),
    "smoke_fire": ("Smoke/fumes - aircraft", "Fire - aircraft", "Fire/smoke - aircraft"),
    "diversion": ("Diversion",),
    "rejected_takeoff": ("Rejected take-off",),
    "hydraulic_flight_controls": ("Hydraulic problem", "Flight control systems (ailerons, rudder, rotors, flaps, main, tail)"),
    "bird_strike": ("Bird strike",),
    "turbulence": ("Weather - clear air turbulence (CAT)/turbulence",),
    "medical": ("Medical emergency",),
    "gnss": ("Loss of Global Navigation Satellite System (GNSS) or GNSS Anomalies",),
}
GROUP_LABEL = {
    "declared_emergency": "Declared emergency or priority",
    "landing_gear": "Landing gear, tyres or wheels",
    "engine": "Engine shutdown, failure or malfunction",
    "smoke_fire": "Smoke, fumes or fire",
    "diversion": "Diversion",
    "rejected_takeoff": "Rejected take-off",
    "hydraulic_flight_controls": "Hydraulics or flight controls",
    "bird_strike": "Bird strike",
    "turbulence": "Turbulence",
    "medical": "Medical emergency on board",
    "gnss": "GPS (GNSS) loss or interference",
}
NOTABLE = ("declared_emergency", "landing_gear", "engine", "smoke_fire", "hydraulic_flight_controls")
_EVENT_TO_GROUP = {name: g for g, names in EVENT_GROUPS.items() for name in names}

# Airliners only: commercial operators flying jets or turboprops.
AIRLINER_ENGINES = {"Turbo fan", "Turbo prop", "Turbo jet"}

# Display names for operators that appear under several spellings in CADORS (keyed by Transport Canada's id).
OPERATOR_NAMES = {
    "5262": "Air Canada", "8893": "WestJet", "5002": "Jazz (Air Canada Express)", "17903": "WestJet Encore",
    "19543": "Porter Airlines", "15266": "Porter Airlines", "17978": "Air Canada Rouge", "5421": "Air France",
    "4881": "United Airlines", "14941": "Flair Airlines", "624": "Delta Air Lines", "15150": "Etihad Airways",
    "15711": "Emirates", "17640": "Qatar Airways", "11674": "Cargojet", "107": "Canadian North", "5311": "Air Transat",
    "1298": "Lufthansa", "5412": "American Airlines", "5430": "British Airways", "358": "KLM", "15405": "Turkish Airlines",
    "1743": "Air India", "12400": "Virgin Atlantic", "4352": "Cathay Pacific", "714": "Aer Lingus", "2783": "Condor",
    "19470": "ITA Airways", "8741": "SkyWest Airlines", "15690": "Republic Airways", "10377": "Endeavor Air",
    "1520": "PSA Airlines", "4343": "Horizon Air", "642": "Cargolux", "5826": "UPS", "3312": "FedEx",
    "15022": "Sunwing Airlines", "2955": "Air Inuit", "2870": "Pacific Coastal Airlines", "1861": "Perimeter Aviation",
    "2659": "PAL Airlines", "3999": "Central Mountain Air", "1487": "Calm Air", "8982": "Air Creebec",
    "11802": "Wasaya Airways", "17940": "Summit Air", "10705": "Nolinor Aviation", "17599": "AeroLogic",
    "8095": "Atlas Air", "18611": "Pascan Aviation", "11592": "Pascan Aviation",
}
SITE_AIRLINES = {"1743": "AI"}   # operators in CADORS that we also have seat maps for (operator id -> IATA)

_OP_RE = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")


# ----------------------------------------------------------------------------------------------- helpers

def _get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (fixed https URLs)
        return r.read()


def _download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as fh:   # noqa: S310
        while chunk := r.read(1 << 20):
            fh.write(chunk)
    return dest


def operator_key(raw: str) -> tuple[str | None, str]:
    """('5262', 'Air Canada') from 'AIR CANADA (5262)'. Unknown ids fall back to a tidied name."""
    raw = (raw or "").strip()
    if not raw:
        return None, ""
    m = _OP_RE.match(raw)
    oid, name = (m.group(2), m.group(1)) if m else (None, raw)
    if oid and oid in OPERATOR_NAMES:
        return oid, OPERATOR_NAMES[oid]
    name = re.sub(r",?\s+(INC|LTD|LIMITED|LTEE|L\.?L\.?C|S\.A|PLC|PJSC|CORP|CO)\.?\b.*$", "", name.strip("() "), flags=re.I)
    name = " ".join(w.capitalize() if w.isupper() else w for w in name.split())
    return oid or name.upper(), name


def _month_floor(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, 1)


def complete_through(month_counts: dict[str, int], today: dt.date) -> dt.date:
    """Last month whose data looks complete. CADORS publishes with a lag, so recent months are thin.

    A month counts as complete when it has at least 75% of the median of the 12 months before it."""
    months = sorted(m for m in month_counts if m < today.strftime("%Y-%m"))
    for i in range(len(months) - 1, 11, -1):
        prior = sorted(month_counts[m] for m in months[i - 12:i])
        median = prior[6]
        if month_counts[months[i]] >= 0.75 * median:
            y, mo = map(int, months[i].split("-"))
            return dt.date(y, mo, 1)
    return _add_months(_month_floor(today), -4)


# ----------------------------------------------------------------------------------------------- CADORS

def summarise_cadors(occurrences: Iterable[dict[str, str]], aircraft: Iterable[dict[str, str]],
                     events: Iterable[dict[str, str]], today: dt.date) -> dict[str, Any]:
    """Pure function over the three CSVs' rows. Window: the 12 complete months before the data lag."""
    occ: dict[str, dict[str, str]] = {}
    month_counts: Counter[str] = Counter()
    earliest = (today - dt.timedelta(days=800)).isoformat()
    for r in occurrences:
        d = r.get("occurrencedate") or ""
        if d >= earliest:
            occ[r["cadorsnumber"]] = r
            month_counts[d[:7]] += 1
    through = complete_through(dict(month_counts), today)
    win_end = _add_months(through, 1)                 # exclusive
    win_start = _add_months(win_end, -12)
    trend_start = _add_months(win_end, -24)

    planes: dict[tuple[str, str], dict[str, Any]] = {}
    for a in aircraft:
        o = occ.get(a.get("cadorsnumber", ""))
        if not o or a.get("operatortypedescriptione") != "Commercial" or a.get("Categorydescriptione") != "Aeroplane":
            continue
        if a.get("enginetypedescriptione") not in AIRLINER_ENGINES:
            continue
        oid, name = operator_key(a.get("operator", ""))
        if not oid:
            continue
        num = (a.get("aircraftnumber") or "").split(".")[0]
        planes[(a["cadorsnumber"], num)] = {
            "cadors": a["cadorsnumber"], "date": o["occurrencedate"], "op": oid, "name": name,
            "flight": (a.get("flightnumber") or "").replace(" ", "") or None,
            "aircraft": " ".join(x for x in (a.get("aircraft_make_name_nm"), a.get("aircraft_model_name_nm")) if x).title() or None,
            "airport": o.get("aerodromeid") or None, "phase": a.get("phasenamee") or None,
            "damage": a.get("damagedescriptione") or None,
            "fatal": _num(o.get("fatalities")), "injured": _num(o.get("Injuries")), "events": [],
        }
    for e in events:
        key = (e.get("cadorsnumber", ""), (e.get("aircraftnumber") or "").split(".")[0])
        p = planes.get(key)
        if p is not None and e.get("event_name_enm"):
            p["events"].append(e["event_name_enm"])
    for p in planes.values():
        p["groups"] = sorted({_EVENT_TO_GROUP[x] for x in p["events"] if x in _EVENT_TO_GROUP})

    in_win = [p for p in planes.values() if win_start.isoformat() <= p["date"] < win_end.isoformat()]
    by_group = Counter(g for p in in_win for g in p["groups"])
    emerg = [p for p in in_win if "declared_emergency" in p["groups"]]
    emerg_no_damage = sum(1 for p in emerg if p["damage"] == "No Damage")

    monthly: dict[str, Counter[str]] = defaultdict(Counter)
    for p in planes.values():
        if trend_start.isoformat() <= p["date"] < win_end.isoformat():
            monthly[p["date"][:7]]["reports"] += 1
            for g in p["groups"]:
                monthly[p["date"][:7]][g] += 1
    months = []
    m = trend_start
    while m < win_end:
        k = m.strftime("%Y-%m")
        months.append({"month": k, **{g: monthly[k][g] for g in ("reports", *NOTABLE)}})
        m = _add_months(m, 1)

    ops: dict[str, dict[str, Any]] = {}
    for p in in_win:
        o = ops.setdefault(p["op"], {"id": p["op"], "name": p["name"], "reports": 0, "groups": Counter()})
        o["reports"] += 1
        o["groups"].update(p["groups"])
    airlines = sorted(ops.values(), key=lambda o: -o["reports"])
    keep = [o for o in airlines[:30]] + [o for o in airlines[30:] if o["id"] in SITE_AIRLINES]
    for o in keep:
        o["groups"] = {g: o["groups"][g] for g in EVENT_GROUPS if o["groups"][g]}
        o["iata"] = SITE_AIRLINES.get(o["id"])

    def slim(p: dict[str, Any]) -> dict[str, Any]:
        return {k: p[k] for k in ("cadors", "date", "name", "flight", "aircraft", "airport", "phase", "damage", "events", "groups")}
    notable = sorted((p for p in planes.values() if any(g in NOTABLE for g in p["groups"])),
                     key=lambda p: (p["date"], p["cadors"]), reverse=True)[:40]
    site_recent = {iata: [slim(p) for p in sorted((p for p in planes.values() if p["op"] == oid),
                                                  key=lambda p: p["date"], reverse=True)[:15]]
                   for oid, iata in SITE_AIRLINES.items()}
    return {
        "source": {"label": "Transport Canada, Civil Aviation Daily Occurrence Reporting System (CADORS)",
                   "url": CADORS_PAGE, "licence": "Open Government Licence - Canada"},
        "scope": "Commercial aeroplanes with jet or turboprop engines, in Canadian airspace or at Canadian airports.",
        "window": {"from": win_start.isoformat(), "to": (win_end - dt.timedelta(days=1)).isoformat()},
        "data_through": (win_end - dt.timedelta(days=1)).isoformat(),
        "reports": len(in_win),
        "groups": {g: by_group[g] for g in EVENT_GROUPS},
        "declared_emergencies": {"count": len(emerg), "no_damage": emerg_no_damage,
                                 "fatal": sum(p["fatal"] for p in emerg), "injured": sum(p["injured"] for p in emerg)},
        "fatal_reports": sum(1 for p in in_win if p["fatal"]),
        "monthly": months,
        "airlines": keep,
        "notable": [slim(p) for p in notable],
        "site_airlines": site_recent,
    }


def _num(v: str | None) -> int:
    try:
        return int(round(float(v or 0)))
    except ValueError:
        return 0


def _csv_rows(path: Path) -> Iterable[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        yield from csv.DictReader(fh)


def collect_cadors(today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.date.today()
    with tempfile.TemporaryDirectory() as tmp:
        paths = {name: _download(CADORS_BASE.format(name), Path(tmp) / f"{name}.csv")
                 for name in ("Occurrence_Information", "Aircraft_Information", "Aircraft_Event_Information")}
        return summarise_cadors(_csv_rows(paths["Occurrence_Information"]), _csv_rows(paths["Aircraft_Information"]),
                                _csv_rows(paths["Aircraft_Event_Information"]), today)


# ----------------------------------------------------------------------------------------------- UK AAIB

TAGS = [("landing gear", r"landing gear|nose ?wheel|main gear|gear (?:collapse|retract)"),
        ("engine", r"engine|propeller"), ("smoke or fumes", r"smoke|fume|fire"),
        ("tail strike", r"tail ?strike"), ("runway", r"runway|overrun|excursion"),
        ("turbulence", r"turbulence"), ("fuel", r"fuel"), ("ground", r"ground handling|taxi|stand|pushback|collision")]


def summarise_aaib(payload: dict[str, Any]) -> dict[str, Any]:
    items = []
    for r in payload.get("results", []):
        desc = r.get("description") or ""
        text = f"{r.get('title', '')} {desc}".lower()
        items.append({
            "title": r.get("title"), "url": "https://www.gov.uk" + r.get("link", ""),
            "published": (r.get("public_timestamp") or "")[:10], "occurred": r.get("date_of_occurrence"),
            "aircraft": r.get("aircraft_type"), "location": ", ".join(r.get("location") or []),
            "summary": desc, "report_type": (r.get("report_type") or [None])[0],
            "tags": [t for t, rx in TAGS if re.search(rx, text)],
        })
    return {"source": {"label": "UK Air Accidents Investigation Branch, via GOV.UK",
                       "url": "https://www.gov.uk/aaib-reports", "licence": "Open Government Licence v3.0"},
            "scope": "Reports on commercial fixed-wing aircraft (airliners and cargo), newest first.",
            "reports": items}


def collect_aaib() -> dict[str, Any]:
    return summarise_aaib(json.loads(_get(AAIB_API)))


# ----------------------------------------------------------------------------------------------- runner

COLLECTORS = {"cadors": collect_cadors, "uk_aaib": collect_aaib}


def collect_all(out: Path = DATA_DIR, only: list[str] | None = None) -> dict[str, Any]:
    """Run each collector; keep the previous file when one fails. Returns the status record."""
    out.mkdir(parents=True, exist_ok=True)
    status_path = out / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    for name, fn in COLLECTORS.items():
        if only and name not in only:
            continue
        st = status.setdefault(name, {})
        st["last_attempt"] = now
        try:
            data = fn()
            data["collected_at"] = now
            (out / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            st.update(last_success=now, error=None)
            print(f"{name}: ok", file=sys.stderr)
        except Exception as e:   # noqa: BLE001 - one source failing must not stop the others or the deploy
            st["error"] = f"{type(e).__name__}: {e}"[:300]
            print(f"{name}: FAILED {st['error']} (keeping previous snapshot)", file=sys.stderr)
    status_path.write_text(json.dumps(status, indent=1) + "\n", encoding="utf-8")
    return status


def load(out: Path = DATA_DIR) -> dict[str, Any]:
    """Everything the site build needs; missing files become None."""
    def rd(name: str) -> Any:
        p = out / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return {"india": rd("india_official.json"), "iata": rd("global_iata.json"), "cadors": rd("cadors.json"),
            "uk_aaib": rd("uk_aaib.json"), "status": rd("status.json") or {}}
