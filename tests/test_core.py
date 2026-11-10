import json
from pathlib import Path

import pytest

from aerorisk.fleet import OpenSkyFleet, TailOverride
from aerorisk.flights import FlightInstance, load_fixture, model_to_icao, parse_flight_number
from aerorisk.layout import build_cabin, list_configs, load_config, seat_facts
from aerorisk.matcher import match
from aerorisk.preferences import ComfortRanker, rank, register
from aerorisk.render import render_html, render_svg

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("cid,n,rows", [("FR-B738-189Y", 189, 32), ("FR-B38M-197Y", 197, 34), ("6E-A20N-186Y", 186, 31),
                                        ("6E-A20N-180Y", 180, 30)])
def test_seat_counts(cid, n, rows):
    c = build_cabin(load_config(cid))
    assert len(c.seats) == n and len(c.rows) == rows


def test_ryanair_quirks():
    c = build_cabin(load_config("FR-B738-189Y"))
    labels = {s.seat for s in c.seats}
    assert "1A" in labels and "1D" not in labels          # row 1 left side only
    assert not any(s.row == 13 for s in c.seats)          # no row 13
    assert c.seat("16A").in_exit_row and c.seat("17F").in_exit_row
    assert "no_window" in c.seat("12A").flags
    assert "row_ahead_of_exit_row" in c.seat("15C").flags
    assert c.seat("14A").nearest_exits[0]["rows_between"] == 1   # row 15 between (13 skipped)


def test_positions_and_aisle_distance():
    c = build_cabin(load_config("6E-A20N-186Y"))
    assert (c.seat("10A").position, c.seat("10A").seats_to_aisle) == ("window", 2)
    assert (c.seat("10B").position, c.seat("10B").seats_to_aisle) == ("middle", 1)
    assert (c.seat("10C").position, c.seat("10D").position) == ("aisle", "aisle")
    assert c.seat("10F").side == "R"


def test_unknown_pitch_is_not_invented():
    c = build_cabin(load_config("6E-A20N-186Y"))
    assert c.seat("1A").pitch_in is None and c.seat("1A").pitch_source == "unknown"
    assert c.seat("12A").pitch_in == 35


def test_seat_facts_never_estimate_risk():
    f = seat_facts(build_cabin(load_config("FR-B738-189Y")), "21c")
    assert f["historical_seat_risk"]["estimable"] is False
    assert f["data_quality"]["status"] == "draft_unverified"
    assert "%" not in json.dumps(f)


def test_every_config_has_sources():
    for c in list_configs():
        assert c["provenance"]["sources"]


def test_flight_parsing():
    assert parse_flight_number("6E 2134") == ("6E", "2134")
    assert parse_flight_number("fr0012") == ("FR", "12")
    assert model_to_icao("Airbus A320 NEO") == "A20N"
    assert model_to_icao("Airbus A320") == "A320"
    assert model_to_icao("Boeing 737-800") == "B738"
    assert model_to_icao("Boeing 737 MAX 8") == "B38M"


def test_match_likely_ranked_by_fleet_share():
    fi = load_fixture(str(ROOT / "fixtures/adb_sample_indigo.json"), "6E9999", "2026-11-20")[0]
    m = match(fi, tails={})
    assert m.tier == "likely"
    assert [c["config_id"] for c in m.candidates] == ["6E-A20N-186Y", "6E-A20N-180Y"]
    assert abs(sum(c["probability"] for c in m.candidates) - 1) < 1e-6


def test_match_exact_only_with_verified_tail():
    fi = load_fixture(str(ROOT / "fixtures/adb_sample_ryanair.json"), "FR9999", "2026-11-20")[0]
    assert match(fi, tails={}).tier == "likely"
    tails = {"EI-TEST": TailOverride("EI-TEST", "FR-B738-189Y", "unit test", "2026-11-09")}
    assert match(fi, tails=tails).tier == "exact"


def test_match_uses_opensky_when_type_missing(tmp_path):
    csv = tmp_path / "db.csv"
    csv.write_text("icao24,registration,typecode,model,operatoricao,operator\n"
                   "800abc,VT-TST,A20N,A320-271N,IGO,IndiGo\n")
    fleet = OpenSkyFleet.from_csv(csv)
    fi = FlightInstance("6E1", "2026-11-20", "6E", "IGO", "IndiGo", "TIR", "BLR",
                        None, None, "VT-TST", True, None, "test")
    m = match(fi, tails={}, fleet=fleet)
    assert m.tier == "likely" and m.aircraft_icao == "A20N"


def test_unavailable_when_not_covered():
    fi = FlightInstance("AI1", "2026-11-20", "AI", "AIC", "Air India", None, None,
                        "Boeing 787-8", None, None, True, None, "test")
    assert match(fi, tails={}).tier == "unavailable"


def test_render_outputs():
    c = build_cabin(load_config("6E-A20N-186Y"))
    svg = render_svg(c, "13A")
    assert svg.startswith("<svg") and svg.count('data-seat="') == 186
    html = render_html([c])
    assert "Historical seat risk" in html and "</script></body>" in html


def test_ranker_cannot_make_safety_claims():
    c = build_cabin(load_config("FR-B738-189Y"))
    assert rank(c, "comfort", {"position": "window"})[0]["ranker_kind"] == "comfort"

    class BadRanker:
        name, kind = "bad", "entertainment"
        def score(self, seat, profile):
            return 1.0, "this seat is safest for Leo"
    register(BadRanker())
    with pytest.raises(ValueError):
        rank(c, "bad", {})


def test_ryanair_max_8_200():
    c = build_cabin(load_config("FR-B38M-197Y"))
    labels = {s.seat for s in c.seats}
    assert len(c.seats) == 197
    assert {"1A", "1D", "27F", "28A", "28F"}.isdisjoint(labels)
    assert {"1B", "1C", "2A", "2B", "2F", "28B", "28E", "35F"} <= labels
    assert c.seat("28B").in_exit_row and c.seat("17A").in_exit_row and not c.seat("16A").in_exit_row
    assert "outermost_seat_window_seat_absent" in c.seat("28B").flags
    assert "outermost_seat_window_seat_absent" not in c.seat("28C").flags
    assert "no_window" in c.seat("14F").flags


def test_ryanair_types_match_separately():
    base = dict(flight_number="FR1", date="2026-11-20", operator_iata="FR", operator_icao="RYR",
                operator_name="Ryanair", origin=None, destination=None, registration=None,
                is_operator_leg=True, status=None, source="test")
    m800 = match(FlightInstance(aircraft_model="Boeing 737-8AS", aircraft_icao=model_to_icao("Boeing 737-8AS"), **base), tails={})
    mmax = match(FlightInstance(aircraft_model="Boeing 737 MAX 8-200", aircraft_icao=model_to_icao("Boeing 737 MAX 8-200"), **base), tails={})
    assert m800.candidates[0]["config_id"] == "FR-B738-189Y"
    assert mmax.candidates[0]["config_id"] == "FR-B38M-197Y"


def test_every_config_records_corroboration_and_conflicts():
    for c in list_configs():
        p = c["provenance"]
        assert p.get("corroboration"), c["id"]
        assert "conflicts" in p and "primary_source_check" in p


def test_new_configs():
    a21 = build_cabin(load_config("6E-A21N-232Y"))
    assert len(a21.seats) == 232 and "28A" not in {s.seat for s in a21.seats}
    assert a21.seat("18C").in_exit_row and a21.seat("28B").in_exit_row
    assert a21.seat("10A").pitch_in is None and a21.seat("10A").pitch_source == "range"
    ezy = build_cabin(load_config("U2-A20N-186Y"))
    assert len(ezy.seats) == 186 and ezy.seat("12A").pitch_in == 35 and "no_window" in ezy.seat("31F").flags


def test_matcher_reports_unmapped_share():
    fi = FlightInstance("6E1", "2026-11-20", "6E", "IGO", "IndiGo", None, None, "Airbus A321 NEO",
                        "A21N", None, True, None, "test")
    m = match(fi, tails={})
    assert m.candidates[0]["config_id"] == "6E-A21N-232Y" and m.candidates[0]["probability"] == 0.64
    assert m.uncovered_share == 0.36 and "not mapped" in m.reason


def test_easyjet_group_iata():
    fi = FlightInstance("EC1", "2026-11-20", "EC", None, "easyJet Europe", None, None, "Airbus A320neo",
                        "A20N", None, True, None, "test")
    assert match(fi, tails={}).candidates[0]["config_id"] == "U2-A20N-186Y"
