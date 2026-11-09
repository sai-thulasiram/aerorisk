"""Render a Cabin as a standalone SVG, or an interactive HTML seat inspector."""
from __future__ import annotations

import html
import json

from .layout import Cabin, seat_facts

SEAT_W, SEAT_H, GAP, AISLE_W = 26, 24, 4, 22
PX_PER_IN = 1.05          # longitudinal scale: inches of pitch -> px
TOP, SIDE = 70, 46


def _x_positions(cabin: Cabin) -> dict[str, float]:
    xs, x = {}, SIDE
    aisle_after = set(cabin.config["aisles_after"])
    for letter in cabin.config["letters"]:
        xs[letter] = x
        x += SEAT_W + GAP
        if letter in aisle_after:
            x += AISLE_W
    return xs


def _width(cabin: Cabin) -> float:
    xs = _x_positions(cabin)
    return max(xs.values()) + SEAT_W + SIDE


SVG_CSS = (".fus{fill:var(--fus,#f3f1ec);stroke:var(--line,#9a968c);stroke-width:1.5}"
           ".seat{fill:var(--seat,#fff);stroke:var(--line,#9a968c);stroke-width:1;cursor:pointer}"
           ".seat.exitrow{fill:var(--exitrow,#e3efe6)}"
           ".seat.sel{fill:var(--sel,#1f5fbf);stroke:var(--sel,#1f5fbf)}"
           ".lbl{font:600 8px system-ui,sans-serif;fill:var(--ink,#333);pointer-events:none}"
           ".lbl.sel{fill:#fff}"
           ".rown{font:500 9px system-ui,sans-serif;fill:var(--muted,#777)}"
           ".exit{fill:var(--exit,#c0392b)}.exittxt{font:700 8px system-ui,sans-serif;fill:var(--exit,#c0392b)}"
           ".fac{fill:var(--fac,#dcd8cf)}.factxt{font:500 8px system-ui,sans-serif;fill:var(--muted,#777)}"
           "g[data-seat]:focus{outline:none}g[data-seat]:focus .seat{stroke:var(--sel,#1f5fbf);stroke-width:2.5}")


def render_svg(cabin: Cabin, highlight: str | None = None, *, title: bool = True,
               inline_style: bool = True) -> str:
    """inline_style=False omits the <style> element (for pages with a strict CSP; use SVG_CSS in a stylesheet)."""
    xs = _x_positions(cabin)
    width = _width(cabin)
    last_y = max(s.y_in for s in cabin.seats)
    height = TOP + last_y * PX_PER_IN + 120
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
           f'class="cabin" role="img" aria-label="Seat map {html.escape(cabin.id)}">']
    if inline_style:
        out.append("<style>" + SVG_CSS + "</style>")
    if title:
        out.append(f'<text x="{width/2:.0f}" y="18" text-anchor="middle" class="rown" '
                   f'style="font-size:11px;font-weight:700">{html.escape(cabin.config["aircraft"]["name"])} · '
                   f'{html.escape(cabin.config["operator"]["name"])} · {cabin.config["total_seats"]}Y</text>')
    # fuselage
    out.append(f'<rect class="fus" x="{SIDE-14}" y="{TOP-34}" width="{width-2*(SIDE-14):.0f}" '
               f'height="{last_y*PX_PER_IN+SEAT_H+68:.0f}" rx="{(width-2*(SIDE-14))/2:.0f}" ry="40"/>')
    # facilities (front/rear blocks)
    for fac in cabin.config.get("facilities", []):
        fy = TOP - 28 if fac["position"] == "front" else TOP + last_y * PX_PER_IN + SEAT_H + 8
        out.append(f'<rect class="fac" x="{width/2-30:.0f}" y="{fy:.0f}" width="60" height="16" rx="3"/>'
                   f'<text class="factxt" x="{width/2:.0f}" y="{fy+11:.0f}" text-anchor="middle">Galley/Lav</text>')
    # exits
    for e in cabin.exits:
        ey = TOP + e.y_in * PX_PER_IN + (SEAT_H / 2 if e.at_row is not None else 0)
        for side in e.sides:
            ex = SIDE - 14 if side == "L" else width - SIDE + 14
            out.append(f'<rect class="exit" x="{ex-3:.0f}" y="{ey-7:.0f}" width="6" height="14" rx="1"/>')
            tx = ex + (8 if side == "L" else -8)
            anchor = "start" if side == "L" else "end"
            out.append(f'<text class="exittxt" x="{tx:.0f}" y="{ey+3:.0f}" text-anchor="{anchor}">EXIT</text>'
                       if e.at_row is None else "")
    # rows + seats
    aisle_x = xs[cabin.config["aisles_after"][0]] + SEAT_W + GAP + AISLE_W / 2 - GAP / 2
    seen_rows = set()
    for s in cabin.seats:
        y = TOP + s.y_in * PX_PER_IN
        if s.row not in seen_rows:
            seen_rows.add(s.row)
            out.append(f'<text class="rown" x="{aisle_x:.0f}" y="{y+SEAT_H/2+3:.0f}" text-anchor="middle">{s.row}</text>')
        cls = "seat" + (" exitrow" if s.in_exit_row else "") + (" sel" if s.seat == highlight else "")
        out.append(f'<g data-seat="{s.seat}" tabindex="0" role="button" aria-label="Seat {s.seat}"><rect class="{cls}" x="{xs[s.letter]:.0f}" y="{y:.0f}" '
                   f'width="{SEAT_W}" height="{SEAT_H}" rx="5"/>'
                   f'<text class="lbl{" sel" if s.seat == highlight else ""}" x="{xs[s.letter]+SEAT_W/2:.0f}" '
                   f'y="{y+SEAT_H/2+3:.0f}" text-anchor="middle">{s.seat}</text></g>')
    out.append("</svg>")
    return "".join(out)


def render_html(cabins: list[Cabin], *, match: dict | None = None) -> str:
    """Interactive inspector: pick a variant, tap a seat, see facts + data quality."""
    payload = {}
    for c in cabins:
        payload[c.id] = {
            "label": f'{c.config["operator"]["name"]} {c.config["aircraft"]["name"]} — {c.config["variant_label"]}',
            "svg": render_svg(c),
            "facts": {s.seat: seat_facts(c, s.seat) for s in c.seats},
        }
    data = json.dumps(payload).replace("</", "<\\/")
    match_json = json.dumps(match or {}).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AeroRisk Seat Inspector</title>
<style>
:root{{--bg:#faf9f6;--card:#fff;--ink:#22211f;--muted:#6f6b63;--line:#b3ada2;--fus:#f1eee7;--seat:#fff;
--exitrow:#dff0e3;--sel:#1f5fbf;--exit:#c0392b;--fac:#dcd8cf;--warn:#8a5a00;--warnbg:#fff4dc}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#161614;--card:#1f1e1c;--ink:#ecebe7;
--muted:#a39f96;--line:#5b5850;--fus:#26251f;--seat:#2e2d29;--exitrow:#203a29;--sel:#5b8ff0;--exit:#ef6b5c;
--fac:#3a3832;--warn:#f0c46b;--warnbg:#2f2714}}}}
:root[data-theme="dark"]{{--bg:#161614;--card:#1f1e1c;--ink:#ecebe7;--muted:#a39f96;--line:#5b5850;--fus:#26251f;
--seat:#2e2d29;--exitrow:#203a29;--sel:#5b8ff0;--exit:#ef6b5c;--fac:#3a3832;--warn:#f0c46b;--warnbg:#2f2714}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:16px;display:grid;gap:16px;grid-template-columns:minmax(0,1fr)}}
@media(min-width:800px){{main{{grid-template-columns:340px minmax(0,1fr)}}}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:var(--muted);font-size:13px;margin:0}}
header{{grid-column:1/-1}}select{{width:100%;padding:8px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--ink);font:inherit}}
.map{{background:var(--card);border-radius:12px;padding:8px;max-height:80vh;overflow:auto}}
.map svg{{width:100%;max-width:330px;display:block;margin:0 auto}}
.panel{{background:var(--card);border-radius:12px;padding:16px;align-self:start}}
.badge{{display:inline-block;font-size:12px;font-weight:700;padding:2px 8px;border-radius:99px;
background:var(--warnbg);color:var(--warn)}}
dl{{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;margin:12px 0}}dt{{color:var(--muted)}}dd{{margin:0}}
.na{{border-left:3px solid var(--warn);padding:8px 10px;background:var(--warnbg);border-radius:6px;font-size:13px}}
small,a{{color:var(--muted)}}a{{overflow-wrap:anywhere}}ul{{padding-left:18px;margin:6px 0}}
</style></head><body><main>
<header><h1>AeroRisk Seat Inspector</h1>
<p class="sub">Layout facts only. Hand-authored draft configurations — verify against the airline's live seat map.</p>
<div id="match" class="sub"></div></header>
<div class="map"><label class="sub" for="cfg">Configuration</label><select id="cfg"></select><div id="svg"></div></div>
<section class="panel" id="panel"><p class="sub">Tap a seat to inspect it.</p></section>
</main>
<script>
const DATA={data};const MATCH={match_json};
const sel=document.getElementById('cfg'),svgBox=document.getElementById('svg'),panel=document.getElementById('panel');
const esc=s=>String(s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
if(MATCH.tier){{document.getElementById('match').innerHTML='Map confidence: <span class="badge">'+esc(MATCH.tier)+'</span> '+esc(MATCH.reason||'');}}
for(const [id,c] of Object.entries(DATA)){{const o=document.createElement('option');o.value=id;o.textContent=c.label;sel.appendChild(o);}}
let current=null;
function show(id){{svgBox.innerHTML=DATA[id].svg;current=null;panel.innerHTML='<p class="sub">Tap a seat to inspect it.</p>';
 svgBox.querySelectorAll('g[data-seat]').forEach(g=>g.addEventListener('click',()=>pick(id,g.dataset.seat)));}}
function pick(id,seat){{
 if(current){{const r=svgBox.querySelector('g[data-seat="'+current+'"]');r.querySelector('rect').classList.remove('sel');r.querySelector('text').classList.remove('sel');}}
 const g=svgBox.querySelector('g[data-seat="'+seat+'"]');g.querySelector('rect').classList.add('sel');g.querySelector('text').classList.add('sel');current=seat;
 const f=DATA[id].facts[seat],L=f.layout;
 const ex=L.nearest_exits.map(e=>'<li>'+esc(e.exit)+' ('+esc(e.kind)+'), '+esc(e.direction.replace('_',' '))+', '+e.rows_between+' row(s) between, ~'+e.approx_distance_in+' in</li>').join('');
 panel.innerHTML='<h2 style="margin:0">'+esc(seat)+'</h2><p>'+esc(f.summary)+'</p>'+
 '<dl><dt>Position</dt><dd>'+esc(L.position)+' · '+(L.side==='L'?'left':'right')+'</dd>'+
 '<dt>Seats to aisle</dt><dd>'+L.seats_to_aisle+'</dd>'+
 '<dt>Pitch</dt><dd>'+(L.pitch_in===null?'unknown':L.pitch_in+'″')+' <small>('+esc(L.pitch_source.replace('_',' '))+')</small></dd>'+
 '<dt>Exit row</dt><dd>'+(L.in_exit_row?'yes':'no')+'</dd>'+
 '<dt>Flags</dt><dd>'+(L.flags.length?esc(L.flags.join(', ').replaceAll('_',' ')):'—')+'</dd></dl>'+
 '<p style="margin:8px 0 4px"><b>Nearest exits</b></p><ul>'+ex+'</ul>'+
 '<div class="na"><b>Historical seat risk: not estimable.</b> '+esc(f.historical_seat_risk.reason)+'</div>'+
 '<p style="margin-top:12px"><span class="badge">'+esc(f.data_quality.status.replaceAll('_',' '))+'</span></p>'+
 '<small>'+esc(f.data_quality.method)+'. First-party check: '+esc(f.data_quality.primary_source_check)+'.</small>'+
 '<details style="margin-top:8px"><summary class="sub">How each fact is corroborated</summary><ul>'+Object.entries(f.data_quality.corroboration).map(([k,v])=>'<li><b>'+esc(k.replaceAll('_',' '))+'</b>: '+esc(v.join(', '))+'</li>').join('')+'</ul></details>'+'<ul>'+f.data_quality.sources.map(s=>'<li><a href="'+esc(s)+'" target="_blank" rel="noopener">'+esc(s.replace(/^https?:\\/\\//,''))+'</a></li>').join('')+'</ul>'+
 (f.data_quality.conflicts.length?'<small>Source conflicts: '+esc(f.data_quality.conflicts.join(' '))+'</small>':'');
}}
sel.addEventListener('change',()=>show(sel.value));
const first=(MATCH.candidates&&MATCH.candidates[0]&&DATA[MATCH.candidates[0].config_id])?MATCH.candidates[0].config_id:Object.keys(DATA)[0];
sel.value=first;show(first);
</script></body></html>"""
