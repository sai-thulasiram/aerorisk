"""Render a Cabin as a standalone SVG, or an interactive HTML seat inspector."""
from __future__ import annotations

import html
import json

from .layout import Cabin, cabin_for, seat_facts

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


def _blocks(letters: list[str], aisles: list[str]) -> list[list[str]]:
    out: list[list[str]] = [[]]
    for letter in letters:
        out[-1].append(letter)
        if letter in aisles:
            out.append([])
    return [b for b in out if b]


def _seat_boxes(cabin: Cabin, letters: list[str], aisles: list[str]) -> dict[str, tuple[float, float]]:
    """x and width for each letter. Cabins with fewer seats per block (e.g. 2-2 business) get wider seats
    that fill the same block width as the main cabin."""
    base = _x_positions(cabin)
    main_blocks = _blocks(cabin.config["letters"], cabin.config["aisles_after"])
    blocks = _blocks(letters, aisles)
    if [len(b) for b in blocks] == [len(b) for b in main_blocks] or len(blocks) != len(main_blocks):
        return {l: (base.get(l, base[cabin.config["letters"][0]]), SEAT_W) for l in letters}
    out = {}
    for mb, b in zip(main_blocks, blocks):
        x0, x1 = base[mb[0]], base[mb[-1]] + SEAT_W
        w = (x1 - x0 - GAP * (len(b) - 1)) / len(b)
        for i, l in enumerate(b):
            out[l] = (x0 + i * (w + GAP), w)
    return out


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
           ".secmark line{stroke:var(--muted,#777);stroke-width:1.5}.secmark text{font:700 8px system-ui,sans-serif;fill:var(--muted,#777);letter-spacing:.08em}"
           ".wing{fill:var(--wing,#d5deec);stroke:var(--line,#9a968c);stroke-width:1}.eng{fill:var(--line,#9a968c)}"
           ".fin{fill:var(--line,#9a968c)}.cockpit{fill:var(--navy,#0b2545);opacity:.75}"
           ".seat.cab-business{fill:var(--biz,#c9d6f2)}.seat.cab-premium-economy{fill:var(--prem,#d8e6fb)}"
           ".seat.sel{fill:var(--sel,#1f5fbf)}"
           ".nosetxt{font:700 8px system-ui,sans-serif;fill:var(--muted,#777);letter-spacing:.12em}"
           "g[data-seat]:focus{outline:none}g[data-seat]:focus .seat{stroke:var(--sel,#1f5fbf);stroke-width:2.5}")


NOSE, TAIL, WING = 96, 150, 112


def _airframe(cabin: Cabin, width: float, top: float, last_y: float) -> list[str]:
    """Top-down airliner outline (nose, wings at the over-wing exits, engines, tailplane) drawn behind the cabin."""
    fx0, fx1 = SIDE - 14, width - SIDE + 14
    cx, fw = width / 2, fx1 - fx0
    y0, y1 = top - 34, top + last_y * PX_PER_IN + SEAT_H + 34
    ow = [e for e in cabin.exits if e.kind == "overwing" and e.at_row is not None]
    if ow:
        yr0 = top + min(e.y_in for e in ow) * PX_PER_IN - 2 * SEAT_H
        yr1 = top + max(e.y_in for e in ow) * PX_PER_IN + 3 * SEAT_H
    else:
        mid = top + last_y * PX_PER_IN * 0.42
        yr0, yr1 = mid - 2 * SEAT_H, mid + 3 * SEAT_H
    out = []
    for sign, root in ((-1, fx0), (1, fx1)):
        tip = root + sign * (WING - 8)
        out.append(f'<path class="wing" d="M{root:.0f},{yr0:.0f} L{tip:.0f},{yr0+92:.0f} L{tip:.0f},{yr0+124:.0f} '
                   f'L{root:.0f},{yr1:.0f} Z"/>')
        ex = root + sign * WING * 0.42
        out.append(f'<rect class="eng" x="{ex-10:.0f}" y="{yr0+8:.0f}" width="20" height="58" rx="9"/>')
        hr0 = y1 + TAIL * 0.30
        htip = root + sign * 58
        out.append(f'<path class="wing" d="M{root - sign*4:.0f},{hr0:.0f} L{htip:.0f},{hr0+46:.0f} L{htip:.0f},{hr0+64:.0f} '
                   f'L{root - sign*6:.0f},{hr0+58:.0f} Z"/>')
    out.append(f'<path class="fus" d="M{fx0:.0f},{y0:.0f} C{fx0:.0f},{y0-NOSE*.55:.0f} {cx-fw*.28:.0f},{y0-NOSE:.0f} {cx:.0f},{y0-NOSE:.0f} '
               f'C{cx+fw*.28:.0f},{y0-NOSE:.0f} {fx1:.0f},{y0-NOSE*.55:.0f} {fx1:.0f},{y0:.0f} L{fx1:.0f},{y1:.0f} '
               f'C{fx1:.0f},{y1+TAIL*.55:.0f} {cx+10:.0f},{y1+TAIL:.0f} {cx:.0f},{y1+TAIL:.0f} '
               f'C{cx-10:.0f},{y1+TAIL:.0f} {fx0:.0f},{y1+TAIL*.55:.0f} {fx0:.0f},{y1:.0f} Z"/>')
    out.append(f'<path class="cockpit" d="M{cx-26:.0f},{y0-NOSE*.52:.0f} Q{cx:.0f},{y0-NOSE*.78:.0f} {cx+26:.0f},{y0-NOSE*.52:.0f} '
               f'L{cx+20:.0f},{y0-NOSE*.42:.0f} Q{cx:.0f},{y0-NOSE*.6:.0f} {cx-20:.0f},{y0-NOSE*.42:.0f} Z"/>')
    out.append(f'<rect class="fin" x="{cx-3:.0f}" y="{y1+TAIL*.25:.0f}" width="6" height="{TAIL*.62:.0f}" rx="3"/>')
    out.append(f'<text class="nosetxt" x="{cx:.0f}" y="{y0-NOSE*.2:.0f}" text-anchor="middle">FRONT</text>')
    return out


def render_svg(cabin: Cabin, highlight: str | None = None, *, title: bool = True,
               inline_style: bool = True, sections: bool = False, shape: bool = False) -> str:
    """inline_style=False omits the <style> element (for pages with a strict CSP; use SVG_CSS in a stylesheet).
    shape=True draws the cabin inside a top-down airliner outline."""
    global TOP
    saved_top = TOP
    if shape:
        TOP = saved_top + NOSE
    try:
        return _render_svg(cabin, highlight, title=title, inline_style=inline_style, sections=sections, shape=shape)
    finally:
        TOP = saved_top


def _render_svg(cabin: Cabin, highlight: str | None, *, title: bool, inline_style: bool, sections: bool,
                shape: bool) -> str:
    xs = _x_positions(cabin)
    width = _width(cabin)
    last_y = max(s.y_in for s in cabin.seats)
    height = TOP + last_y * PX_PER_IN + (SEAT_H + 34 + TAIL + 16 if shape else 120)
    x0, vw = (-WING, width + 2 * WING) if shape else (0, width)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.0f} 0 {vw:.0f} {height:.0f}" '
           f'class="cabin" role="img" aria-label="Seat map {html.escape(cabin.id)}">']
    if inline_style:
        out.append("<style>" + SVG_CSS + "</style>")
    if title:
        out.append(f'<text x="{width/2:.0f}" y="18" text-anchor="middle" class="rown" '
                   f'style="font-size:11px;font-weight:700">{html.escape(cabin.config["aircraft"]["name"])} · '
                   f'{html.escape(cabin.config["operator"]["name"])} · {cabin.config["total_seats"]}Y</text>')
    # fuselage
    if shape:
        out.extend(_airframe(cabin, width, TOP, last_y))
    else:
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
    box_cache: dict[str, dict[str, tuple[float, float]]] = {}
    for s in cabin.seats:
        if s.cabin not in box_cache:
            cab = cabin_for(cabin.config, s.row)
            box_cache[s.cabin] = _seat_boxes(cabin, cab["letters"], cab["aisles_after"])
        sx, sw = box_cache[s.cabin][s.letter]
        y = TOP + s.y_in * PX_PER_IN
        if s.row not in seen_rows:
            seen_rows.add(s.row)
            out.append(f'<text class="rown" x="{aisle_x:.0f}" y="{y+SEAT_H/2+3:.0f}" text-anchor="middle">{s.row}</text>')
        cls = ("seat" + (" exitrow" if s.in_exit_row else "") + (" sel" if s.seat == highlight else "")
               + (f" sec-{s.section}" + (" near-exit" if s.near_exit else "") if sections else ""))
        cls += f" cab-{s.cabin.lower().replace(' ', '-')}"
        out.append(f'<g data-seat="{s.seat}" tabindex="0" role="button" aria-label="Seat {s.seat}"><rect class="{cls}" x="{sx:.0f}" y="{y:.0f}" '
                   f'width="{sw:.0f}" height="{SEAT_H}" rx="5"/>'
                   f'<text class="lbl{" sel" if s.seat == highlight else ""}" x="{sx+sw/2:.0f}" '
                   f'y="{y+SEAT_H/2+3:.0f}" text-anchor="middle">{s.seat}</text></g>')
    if sections:
        spans: dict[str, list[float]] = {}
        for st in cabin.seats:
            y0 = TOP + st.y_in * PX_PER_IN
            lo, hi = spans.get(st.section, [y0, y0])
            spans[st.section] = [min(lo, y0), max(hi, y0 + SEAT_H)]
        for name, (y0, y1) in spans.items():
            mid = (y0 + y1) / 2
            lx = (-WING + 22) if shape else 22
            out.append(f'<g class="secmark sec-{name}"><line x1="{lx}" y1="{y0:.0f}" x2="{lx}" y2="{y1:.0f}"/>'
                       f'<text x="{lx-8}" y="{mid:.0f}" transform="rotate(-90 {lx-8} {mid:.0f})" text-anchor="middle">'
                       f'{name.upper()}</text></g>')
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
