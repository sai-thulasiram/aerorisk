"""CLI.

  python -m aerorisk configs
  python -m aerorisk seat   --config FR-B738-189Y --seat 21C
  python -m aerorisk render --config 6E-A20N-186Y --out out/indigo.svg [--seat 13A]
  python -m aerorisk lookup --flight 6E2134 --date 2026-11-20 [--fixture f.json] [--opensky aircraftDatabase.csv] --html out/flight.html
  python -m aerorisk fleet  --opensky aircraftDatabase.csv --operators RYR,IGO
  python -m aerorisk site   --out site          (reads SITE_URL, UMAMI_WEBSITE_ID, ... from env)
  python -m aerorisk indexnow --site-dir site   (after deploy; needs INDEXNOW_KEY)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .fleet import OpenSkyFleet
from .flights import load_fixture, resolve_flight
from .layout import build_cabin, list_configs, load_config, seat_facts
from .matcher import match
from .render import render_html, render_svg


def _write(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    print(f"wrote {p}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aerorisk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("configs")
    s = sub.add_parser("seat"); s.add_argument("--config", required=True); s.add_argument("--seat", required=True)
    r = sub.add_parser("render"); r.add_argument("--config", required=True); r.add_argument("--out", required=True)
    r.add_argument("--seat")
    lk = sub.add_parser("lookup"); lk.add_argument("--flight", required=True); lk.add_argument("--date", required=True)
    lk.add_argument("--fixture"); lk.add_argument("--opensky"); lk.add_argument("--html")
    fl = sub.add_parser("fleet"); fl.add_argument("--opensky", required=True); fl.add_argument("--operators", default="")
    st = sub.add_parser("site"); st.add_argument("--out", default="site")
    ix = sub.add_parser("indexnow"); ix.add_argument("--site-dir", default="site")
    a = ap.parse_args(argv)

    if a.cmd == "configs":
        for c in list_configs():
            print(f'{c["id"]:16} {c["operator"]["name"]:8} {c["aircraft"]["icao"]} {c["total_seats"]:>3}  '
                  f'{c["provenance"]["status"]}')
    elif a.cmd == "seat":
        print(json.dumps(seat_facts(build_cabin(load_config(a.config)), a.seat), indent=2))
    elif a.cmd == "render":
        cabin = build_cabin(load_config(a.config))
        _write(a.out, render_html([cabin]) if a.out.endswith(".html") else render_svg(cabin, a.seat))
    elif a.cmd == "lookup":
        flights = (load_fixture(a.fixture, a.flight, a.date) if a.fixture
                   else resolve_flight(a.flight, a.date))
        if not flights:
            print("No flight instance found."); return 1
        fleet = OpenSkyFleet.from_csv(a.opensky) if a.opensky else None
        fi = flights[0]
        m = match(fi, fleet=fleet)
        print(json.dumps({"flight": fi.to_dict(), "match": m.to_dict(),
                          "other_legs": [f.to_dict() for f in flights[1:]]}, indent=2))
        if a.html and m.candidates:
            cabins = [build_cabin(load_config(c["config_id"])) for c in m.candidates]
            _write(a.html, render_html(cabins, match=m.to_dict()))
    elif a.cmd == "fleet":
        ops = {o.strip().upper() for o in a.operators.split(",") if o.strip()} or None
        fleet = OpenSkyFleet.from_csv(a.opensky, ops)
        for (op, t), n in sorted(fleet.fleet_summary().items(), key=lambda kv: -kv[1]):
            print(f"{op or '?':5} {t or '?':5} {n}")
    elif a.cmd == "site":
        from .site import build
        res = build(a.out)
        print(f"built {res['pages']} pages ({res['seat_maps']} seat maps) into {res['out']}")
        for w in res["warnings"]:
            print("WARNING:", w)
    elif a.cmd == "indexnow":
        from .site import indexnow
        print("IndexNow HTTP", indexnow(a.site_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
