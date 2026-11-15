import datetime as dt
import json

from aerorisk import incidents as inc


def occ(n, date, fatal="0E-18"):
    return {"cadorsnumber": n, "occurrencedate": date, "aerodromeid": "CYYZ", "fatalities": fatal, "Injuries": "0E-18"}


def ac(n, num, op="AIR CANADA (5262)", eng="Turbo fan", optype="Commercial", cat="Aeroplane", damage="No Damage", sector="705"):
    return {"operation_sector_elbl": sector, "cadorsnumber": n, "aircraftnumber": f"{num}.000000", "operator": op, "operatortypedescriptione": optype,
            "Categorydescriptione": cat, "enginetypedescriptione": eng, "flightnumber": "ACA 1",
            "aircraft_make_name_nm": "AIRBUS", "aircraft_model_name_nm": "A320", "phasenamee": "Approach",
            "damagedescriptione": damage}


def ev(n, num, name):
    return {"cadorsnumber": n, "aircraftnumber": str(num), "event_name_enm": name}


def background(today):
    # steady 100 reports a month for 26 months, then a thin (lagging) last month
    rows = []
    d = dt.date(today.year - 2, today.month, 1)
    i = 0
    while d < today.replace(day=1):
        k = 100 if d < dt.date(today.year, today.month - 1 if today.month > 1 else 12, 1) else 10
        for j in range(k):
            rows.append(occ(f"BG{i}", d.replace(day=1 + j % 27).isoformat()))
            i += 1
        d = inc._add_months(d, 1)
    return rows


def test_operator_names():
    assert inc.operator_key("AIR CANADA (5262)") == ("5262", "Air Canada")
    assert inc.operator_key("AIR INDIA LIMITED (1743)") == ("1743", "Air India")
    oid, name = inc.operator_key("SOME NEW AIRWAYS LTD. (99999)")
    assert oid == "99999" and name == "Some New Airways"
    assert inc.operator_key("") == (None, "")


def test_complete_through_skips_lagging_months():
    counts = {f"2025-{m:02d}": 100 for m in range(1, 13)}
    counts.update({"2026-01": 95, "2026-02": 30, "2026-03": 2})
    assert inc.complete_through(counts, dt.date(2026, 4, 10)) == dt.date(2026, 1, 1)


def test_summary_counts_airliners_only_and_groups_events():
    today = dt.date(2026, 11, 15)
    occs = background(today) + [occ("E1", "2026-06-03"), occ("E2", "2026-06-04"), occ("E3", "2026-06-05"),
                                occ("E4", "2026-06-06"), occ("E5", "2026-06-07"), occ("OLD", "2023-01-01")]
    acs = [ac("E1", 1), ac("E2", 2, op="AIR INDIA LIMITED (1743)", eng="Turbo fan", damage="Minor", sector="701"),
           ac("E3", 3, eng="Reciprocating"),                    # piston: not an airliner
           ac("E4", 4, optype="Private"),                       # private: excluded
           ac("E5", 5, eng="Turbo prop", sector="703")]         # air taxi: excluded
    evs = [ev("E1", 1, "Declared emergency/priority"), ev("E1", 1, "Landing gear"), ev("E2", 2, "Declared emergency/priority"),
           ev("E2", 2, "Engine shut down"), ev("E3", 3, "Engine failure")]
    s = inc.summarise_cadors(occs, acs, evs, today)
    assert s["reports"] == 2
    assert s["groups"]["declared_emergency"] == 2 and s["groups"]["landing_gear"] == 1 and s["groups"]["engine"] == 1
    assert s["declared_emergencies"] == {"count": 2, "no_damage": 1, "fatal": 0, "injured": 0}
    names = {a["name"]: a for a in s["airlines"]}
    assert names["Air India"]["iata"] == "AI" and names["Air India"]["groups"]["engine"] == 1
    assert s["site_airlines"]["AI"][0]["cadors"] == "E2"
    assert s["notable"][0]["cadors"] == "E2"
    assert len(s["monthly"]) == 24 and s["window"]["to"] < "2026-11-01"


def test_operators_with_two_ids_are_merged():
    today = dt.date(2026, 11, 15)
    occs = background(today) + [occ("P1", "2026-06-03"), occ("P2", "2026-06-04")]
    acs = [ac("P1", 1, op="PORTER AIRLINES INC. (15266)"), ac("P2", 2, op="PORTER AIRLINES (CANADA) LIMITED (19543)")]
    s = inc.summarise_cadors(occs, acs, [], today)
    assert [a["reports"] for a in s["airlines"] if a["name"] == "Porter Airlines"] == [2]


def test_aaib_summary_tags():
    payload = {"results": [{"title": "AAIB investigation to Airbus A319-131, G-EUPS", "link": "/aaib-reports/x",
                            "public_timestamp": "2026-10-08T08:59:59Z", "report_type": ["correspondence-investigation"],
                            "date_of_occurrence": "2025-10-30", "aircraft_type": "Airbus A319-131", "location": ["Edinburgh"],
                            "description": "Inertial reference drift following shock loading to nose landing gear"}]}
    payload["results"].append({"title": "AAIB investigation to Sikorsky S-92A", "link": "/x2", "aircraft_type": "Sikorsky S-92A"})
    s = inc.summarise_aaib(payload)
    assert len(s["reports"]) == 1
    r = s["reports"][0]
    assert r["url"] == "https://www.gov.uk/aaib-reports/x" and r["published"] == "2026-10-08"
    assert "landing gear" in r["tags"]


def test_failed_source_keeps_previous_snapshot(tmp_path, monkeypatch):
    (tmp_path / "uk_aaib.json").write_text('{"reports": [{"title": "old"}]}')
    def boom():
        raise OSError("server down")
    monkeypatch.setattr(inc, "COLLECTORS", {"uk_aaib": boom})
    status = inc.collect_all(tmp_path)
    assert "server down" in status["uk_aaib"]["error"]
    assert json.loads((tmp_path / "uk_aaib.json").read_text())["reports"][0]["title"] == "old"


def test_curated_india_totals_match_rows():
    d = inc.load()["india"]["fault_rates"]
    for p in ("2023", "2024"):
        assert sum(r[p]["flights"] for r in d["rows"]) == d["totals"][p]["flights"]
        assert sum(r[p]["faults"] for r in d["rows"]) == d["totals"][p]["faults"]
