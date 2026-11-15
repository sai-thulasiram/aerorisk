"""HTML and Markdown for /incidents/ from the data collected by aerorisk.incidents.

Rules this page follows:
  * Counts from a source with no flight numbers are never presented as a ranking.
  * Every block names its source, its licence or kind (official / press report) and its date.
  * Charts are inline SVG with classes only (the site's CSP forbids inline styles).
"""
from __future__ import annotations

import datetime as dt
from html import escape
from typing import Any

from .incidents import GROUP_LABEL

_e = lambda v: escape(str(v if v is not None else ""), quote=True)   # noqa: E731


def _fmt(n: float | int) -> str:
    return f"{n:,}" if isinstance(n, int) else f"{n:,.1f}"


def _date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        d = dt.date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{d.day} {d.strftime('%b %Y')}"


def _src(src: dict[str, Any]) -> str:
    kind = src.get("kind")
    badge = (f'<span class="srcbadge src-{_e(kind)}">{"Official" if kind == "official" else "Press report"}</span> '
             if kind else "")
    return f'<p class="small muted">{badge}Source: <a href="{_e(src["url"])}" rel="noopener">{_e(src["label"])}</a></p>'


def hbars(rows: list[tuple[str, float, str]], unit: str, cls: str = "") -> str:
    """Horizontal bar chart. rows: (label, value, value text)."""
    if not rows:
        return ""
    top = max(v for _, v, _ in rows) or 1
    lw, bw, rh = 230, 300, 26
    h = rh * len(rows) + 8
    parts = [f'<svg class="hbars {cls}" viewBox="0 0 {lw + bw + 90} {h}" role="img" aria-label="{_e(unit)}">']
    for i, (label, v, txt) in enumerate(rows):
        y = 4 + i * rh
        w = max(2, bw * v / top)
        parts.append(f'<text class="ch-label" x="{lw - 8}" y="{y + 17}" text-anchor="end">{_e(label)}</text>'
                     f'<rect class="ch-bar" x="{lw}" y="{y + 4}" width="{w:.1f}" height="{rh - 9}" rx="3"/>'
                     f'<text class="ch-val" x="{lw + w + 6:.1f}" y="{y + 17}">{_e(txt)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def trend(monthly: list[dict[str, Any]], series: list[tuple[str, str]]) -> str:
    """Small multi-line chart of monthly counts."""
    if not monthly:
        return ""
    W, H, L, B, T = 640, 200, 34, 26, 10
    n = len(monthly)
    top = max(1, max(m.get(k, 0) for m in monthly for k, _ in series))
    sx = lambda i: L + (W - L - 10) * i / max(1, n - 1)                      # noqa: E731
    sy = lambda v: T + (H - T - B) * (1 - v / top)                            # noqa: E731
    parts = [f'<svg class="trend" viewBox="0 0 {W} {H}" role="img" aria-label="Monthly reports">']
    for frac in (0, 0.5, 1):
        v = round(top * frac)
        parts.append(f'<line class="ch-grid" x1="{L}" x2="{W - 10}" y1="{sy(v):.1f}" y2="{sy(v):.1f}"/>'
                     f'<text class="ch-axis" x="{L - 6}" y="{sy(v) + 4:.1f}" text-anchor="end">{v}</text>')
    for i, m in enumerate(monthly):
        if i % 3 == 0:
            d = dt.date.fromisoformat(m["month"] + "-01")
            parts.append(f'<text class="ch-axis" x="{sx(i):.1f}" y="{H - 8}" text-anchor="middle">{d.strftime("%b %y")}</text>')
    for j, (key, _) in enumerate(series):
        pts = " ".join(f"{sx(i):.1f},{sy(m.get(key, 0)):.1f}" for i, m in enumerate(monthly))
        parts.append(f'<polyline class="ch-line ch-s{j}" points="{pts}"/>')
    parts.append("</svg>")
    legend = "".join(f'<span class="lg"><span class="lg-sw ch-s{j}"></span>{_e(label)}</span>' for j, (_, label) in enumerate(series))
    return f'{"".join(parts)}<p class="legend-row small">{legend}</p>'


def _status_line(status: dict[str, Any], key: str) -> str:
    st = status.get(key) or {}
    if not st:
        return '<p class="small muted">Waiting for the first daily update.</p>'
    txt = f"Updated {_date(st.get('last_success'))}" if st.get("last_success") else "Not collected yet"
    if st.get("error"):
        txt += f". The latest attempt ({_date(st.get('last_attempt'))}) failed, so this shows the previous data."
    return f'<p class="small muted">{_e(txt)}</p>'


def _india(india: dict[str, Any] | None, seat_maps_by_iata: dict[str, str]) -> str:
    if not india:
        return ""
    fr = india["fault_rates"]
    rows_html, bars = [], []
    for r in fr["rows"]:
        cells = []
        for p in fr["periods"]:
            d = r[p["key"]]
            rate = d["faults"] / d["flights"] * 10000
            cells.append(f'<td>{_fmt(d["faults"])}</td><td>{_fmt(d["flights"])}</td><td><strong>{rate:.1f}</strong></td>')
        name = _e(r["airline"])
        if r["iata"] in seat_maps_by_iata:
            name = f'<a href="{_e(seat_maps_by_iata[r["iata"]])}">{name}</a>'
        rows_html.append(f"<tr><th scope=\"row\">{name}</th>{''.join(cells)}</tr>")
        last = r[fr["periods"][-1]["key"]]
        if last["flights"] >= 20000:
            bars.append((r["airline"].split(" (")[0], last["faults"] / last["flights"] * 10000,
                         f'{last["faults"] / last["flights"] * 10000:.1f}'))
    tot = fr["totals"]
    tot_cells = "".join(f'<td>{_fmt(tot[p["key"]]["faults"])}</td><td>{_fmt(tot[p["key"]]["flights"])}</td>'
                        f'<td><strong>{tot[p["key"]]["faults"] / tot[p["key"]]["flights"] * 10000:.1f}</strong></td>'
                        for p in fr["periods"])
    head = "".join(f'<th colspan="3">{_e(p["label"])}</th>' for p in fr["periods"])
    sub = "<th>Faults</th><th>Flights</th><th>Per 10k</th>" * len(fr["periods"])
    bars.sort(key=lambda b: b[1])
    last_label = fr["periods"][-1]["label"]
    out = [f"""<section id="india"><h2>India: technical faults per 10,000 flights</h2>
<p>Airlines must report technical faults to India's regulator, DGCA. Counted per flight, the airline with the most faults isn't the one with the highest rate: IndiGo reports the most because it flies by far the most.</p>
<h3 class="h4">{_e(last_label)}, airlines with 20,000+ flights</h3>
{hbars(bars, "Technical faults per 10,000 flights")}
<div class="tablewrap"><table class="facts num"><thead><tr><th rowspan="2">Airline</th>{head}</tr><tr>{sub}</tr></thead>
<tbody>{''.join(rows_html)}<tr class="total"><th scope="row">All airlines</th>{tot_cells}</tr></tbody></table></div>
<p class="small muted">{_e(fr["definition"])} A higher rate can also mean stricter reporting, so it isn't a safety score.</p>
{_src(fr["source"])}"""]

    for key in ("airline_2026", "airline_2025"):
        blk = india.get(key)
        if blk:
            out.append(f'<h3 class="h4">{_e(blk["title"])}</h3>' +
                       hbars([(r["airline"], r["count"], str(r["count"])) for r in blk["rows"]], blk["title"], "short") +
                       f'<p class="small muted">{_e(blk["note"])}</p>{_src(blk["source"])}' +
                       "".join(_src(x) for x in blk.get("extra_sources", [])))
    se = india.get("serious_events_2025")
    if se:
        rows = "".join(f'<tr><th scope="row">{_e(r["airline"])}</th><td>{r["engine_shutdowns"]}</td><td>{r["mayday"]}</td></tr>'
                       for r in se["rows"])
        out.append(f'<h3 class="h4">{_e(se["title"])}</h3><div class="tablewrap"><table class="facts num"><thead><tr><th>Airline</th>'
                   f'<th>Engine shutdowns</th><th>Mayday calls</th></tr></thead><tbody>{rows}</tbody></table></div>'
                   f'<p class="small muted">{_e(se["note"])}</p>{_src(se["source"])}')
    em = india.get("emergency_landings")
    if em:
        out.append(f'<h3 class="h4">Emergency landings</h3><p>{_e(em["text"])}</p>{_src(em["source"])}')
    yd = india.get("yearly_defects")
    if yd:
        out.append(f'<h3 class="h4">{_e(yd["title"])}</h3>' +
                   hbars([(r["year"], r["count"], _fmt(r["count"]) + "") for r in yd["rows"]], "Defects per year", "short") +
                   f'<p class="small muted">{_e(yd["note"])}</p>' +
                   "".join(_src(x) for x in yd["sources"]))
    out.append("</section>")
    return "\n".join(out)


def _iata(iata: dict[str, Any] | None) -> str:
    if not iata:
        return ""
    regions = hbars([(r["region"], r["rate"], f'{r["rate"]:.2f}') for r in iata["regions"]], "Accidents per million flights")
    return f"""<section id="global"><h2>The big picture: {iata["year"]} worldwide</h2>
<div class="tiles">
<div class="tile"><span class="tile-n">1 in {_fmt(iata["one_in_flights"])}</span><span class="tile-l">flights had an accident of any kind</span></div>
<div class="tile"><span class="tile-n">{iata["accidents"]}</span><span class="tile-l">accidents in {iata["flights_million"]} million flights; {iata["fatal_accidents"]} were fatal</span></div>
<div class="tile"><span class="tile-n">{iata["accident_rate_per_million"]}</span><span class="tile-l">accidents per million flights (5-year average {iata["five_year_avg_rate"]})</span></div>
</div>
<p>The most common accidents were {", ".join(iata["common_types"][:-1])} and {iata["common_types"][-1]}. Turboprops had {iata["turboprop_rate"]} accidents per million flights, jets {iata["jet_rate"]}.</p>
<h3 class="h4">Accidents per million flights, by region</h3>
{regions}
{_src(iata["source"])}
</section>"""


def _cadors(c: dict[str, Any] | None, status: dict[str, Any], seat_maps_by_iata: dict[str, str]) -> str:
    if not c:
        return f'<section id="canada"><h2>Reported events in Canadian airspace</h2>{_status_line(status, "cadors")}</section>'
    de = c["declared_emergencies"]
    pct = round(100 * de["no_damage"] / de["count"]) if de["count"] else 0
    win = f'{_date(c["window"]["from"])} to {_date(c["window"]["to"])}'
    groups = sorted(((GROUP_LABEL[g], n, _fmt(n)) for g, n in c["groups"].items() if n), key=lambda x: -x[1])
    rows = []
    for a in c["airlines"]:
        g = a["groups"]
        name = _e(a["name"])
        if a.get("iata") and a["iata"] in seat_maps_by_iata:
            name = f'<a href="{_e(seat_maps_by_iata[a["iata"]])}">{name}</a>'
        rows.append(f'<tr{" class=hl" if a.get("iata") else ""}><th scope="row">{name}</th><td>{_fmt(a["reports"])}</td>'
                    + "".join(f"<td>{g.get(k, 0) or '–'}</td>" for k in ("declared_emergency", "landing_gear", "engine", "smoke_fire", "diversion"))
                    + "</tr>")
    notable = []
    for p in c["notable"][:15]:
        ev = "; ".join(GROUP_LABEL[g] for g in p["groups"] if g in GROUP_LABEL)
        bits = " · ".join(_e(x) for x in (p.get("flight"), p.get("aircraft"), p.get("airport"), p.get("phase")) if x)
        dmg = "" if p.get("damage") in (None, "No Damage") else f' · damage: {_e(p["damage"].lower())}'
        notable.append(f'<li><strong>{_date(p["date"])}</strong> {_e(p["name"])}: {_e(ev)}<br>'
                       f'<span class="small muted">{bits}{dmg} · CADORS {_e(p["cadors"])}</span></li>')
    return f"""<section id="canada"><h2>Emergencies usually end quietly</h2>
<p>Canada publishes every occurrence report filed in its airspace, including foreign airlines, every day. This is the most complete open record of what airliners report: declared emergencies, landing-gear problems, engine shutdowns, smoke, diversions.</p>
<div class="tiles">
<div class="tile"><span class="tile-n">{_fmt(de["count"])}</span><span class="tile-l">declared emergencies by airliners in 12 months</span></div>
<div class="tile ok"><span class="tile-n">{pct}%</span><span class="tile-l">of them ended with no damage to the aircraft</span></div>
<div class="tile"><span class="tile-n">{de["fatal"]}</span><span class="tile-l">deaths in those emergencies</span></div>
</div>
<p class="small muted">{_e(c["scope"])} Period: {win}. Canada publishes reports with a delay of a few months, so the most recent months are left out until they are complete.</p>
<h3 class="h4">What was reported, {win}</h3>
{hbars(groups, "Reports by type")}
<p class="small muted">A report can have several types. "Declared emergency" includes crews asking for priority as a precaution.</p>
<h3 class="h4">Month by month</h3>
{trend(c["monthly"], [("declared_emergency", "Declared emergency"), ("landing_gear", "Landing gear, tyres"), ("engine", "Engine")])}
<h3 class="h4">By airline</h3>
<p>Counts, not rates: Canada doesn't publish how many flights each airline operates there, so busy Canadian airlines naturally top this list. Use it to see what kind of events each airline reports, not to rank them.</p>
<div class="tablewrap"><table class="facts num"><thead><tr><th>Airline</th><th>Reports</th><th>Declared emergency</th><th>Landing gear</th><th>Engine</th><th>Smoke / fire</th><th>Diversion</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
<h3 class="h4">Latest notable reports</h3>
<ul class="evlist">{"".join(notable)}</ul>
<p class="small muted">Source: <a href="{_e(c["source"]["url"])}" rel="noopener">{_e(c["source"]["label"])}</a>, {_e(c["source"]["licence"])}. Search a CADORS number on Transport Canada's site for the full narrative.</p>
{_status_line(status, "cadors")}
</section>"""


def _aaib(u: dict[str, Any] | None, status: dict[str, Any]) -> str:
    if not u:
        return ""
    items = []
    for r in u["reports"][:12]:
        tags = "".join(f'<span class="chip-s">{_e(t)}</span>' for t in r["tags"])
        items.append(f'<li><a href="{_e(r["url"])}" rel="noopener">{_e((r["summary"] or r["title"]).strip())}</a> {tags}<br>'
                     f'<span class="small muted">{_e(r["aircraft"])} · occurred {_date(r["occurred"])} · published {_date(r["published"])}</span></li>')
    return f"""<section id="uk"><h2>Latest UK investigation reports</h2>
<p>The UK's Air Accidents Investigation Branch publishes what went wrong and why, usually months after the event.</p>
<ul class="evlist">{"".join(items)}</ul>
<p class="small muted">Source: <a href="{_e(u["source"]["url"])}" rel="noopener">{_e(u["source"]["label"])}</a>, {_e(u["source"]["licence"])}.</p>
{_status_line(status, "uk_aaib")}
</section>"""


CAVEATS = [
    "Counts depend on how strictly airlines report. A careful airline can show more reports than a careless one.",
    "Without flight numbers, more reports usually just means more flights.",
    "Most reports are precautionary: a crew declares an emergency to get priority, then lands normally.",
    "None of this predicts your flight, and none of it is linked to where you sit.",
]


def render(data: dict[str, Any], seat_maps_by_iata: dict[str, str]) -> tuple[str, str]:
    """Returns (band_html, body_html)."""
    st = data.get("status") or {}
    upd = max((v.get("last_success") or "" for v in st.values()), default="")
    chips = (f'<span class="chip">Updated {_e(_date(upd))}</span>' if upd else "") + '<span class="chip">Official and open sources</span>'
    band = f"""<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a> › Incident stats</nav>
<h1>Airline incidents: what the numbers say</h1>
<p class="lede">Emergency landings, landing-gear problems and engine shutdowns, from official and open sources, refreshed daily. Most reported events end with nothing worse than a delay.</p>
<p class="chips">{chips}</p>"""
    toc = ('<nav class="toc small" aria-label="On this page"><a href="#global">Worldwide</a> · <a href="#india">India</a> · '
           '<a href="#canada">Reported events</a> · <a href="#uk">Investigations</a> · <a href="#read">How to read this</a></nav>')
    body = "\n".join([
        toc,
        _iata(data.get("iata")),
        _india(data.get("india"), seat_maps_by_iata),
        _cadors(data.get("cadors"), st, seat_maps_by_iata),
        _aaib(data.get("uk_aaib"), st),
        '<section id="read"><h2>How to read these numbers</h2><ul>' + "".join(f"<li>{_e(c)}</li>" for c in CAVEATS) + "</ul></section>",
    ])
    return band, body


def markdown(data: dict[str, Any], url: str) -> str:
    L = ["# Airline incidents: what the numbers say", "",
         f"Canonical: {url}/incidents/ · JSON: {url}/api/v1/incidents.json", ""]
    iata = data.get("iata")
    if iata:
        L += [f"## Worldwide {iata['year']} (IATA)", "",
              f"- {iata['accidents']} accidents in {iata['flights_million']} million flights (1 in {iata['one_in_flights']:,}); {iata['fatal_accidents']} fatal",
              f"- Accident rate {iata['accident_rate_per_million']} per million flights (5-year average {iata['five_year_avg_rate']})",
              f"- Most common: {', '.join(iata['common_types'])}", f"- Source: [{iata['source']['label']}]({iata['source']['url']})", ""]
    india = data.get("india")
    if india:
        fr = india["fault_rates"]
        L += ["## India: technical faults per 10,000 flights", "", "| Airline | " + " | ".join(p["label"] for p in fr["periods"]) + " |",
              "|---|" + "---|" * len(fr["periods"])]
        for r in fr["rows"]:
            L.append(f"| {r['airline']} | " + " | ".join(
                f"{r[p['key']]['faults'] / r[p['key']]['flights'] * 10000:.1f} ({r[p['key']]['faults']} faults, {r[p['key']]['flights']:,} flights)"
                for p in fr["periods"]) + " |")
        L += ["", f"- Source ({fr['source']['kind']}): [{fr['source']['label']}]({fr['source']['url']})", ""]
    c = data.get("cadors")
    if c:
        de = c["declared_emergencies"]
        L += [f"## Reported events in Canadian airspace, {c['window']['from']} to {c['window']['to']}", "",
              f"- Declared emergencies by airliners: {de['count']}; {de['no_damage']} ended with no damage; deaths: {de['fatal']}"]
        L += [f"- {GROUP_LABEL[g]}: {n}" for g, n in c["groups"].items() if n]
        L += ["", "Counts by airline are not rates (no flight numbers published); busier airlines report more.", "",
              f"- Source: [{c['source']['label']}]({c['source']['url']}), {c['source']['licence']}", ""]
    u = data.get("uk_aaib")
    if u:
        L += ["## Latest UK AAIB reports (commercial fixed-wing)", ""]
        L += [f"- {r['published']}: [{(r['summary'] or r['title']).strip()}]({r['url']}) ({r['aircraft']})" for r in u["reports"][:12]]
        L.append("")
    L += ["## How to read these numbers", ""] + [f"- {x}" for x in CAVEATS]
    return "\n".join(L) + "\n"
