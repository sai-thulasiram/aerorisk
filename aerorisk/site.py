"""Static site generator: crawlable seat-map pages + files for search engines and AI agents.

    python -m aerorisk site --out site

Everything a crawler or agent needs is in the HTML/Markdown/JSON itself; JavaScript only
adds the interactive seat inspector and analytics events.

Configuration (environment variables):
    SITE_URL            https://your-domain (required for production; canonical, sitemap, robots)
    SITE_NAME           default "MyLuckySeat"
    CONTACT_EMAIL       shown for corrections; omitted if unset
    UMAMI_WEBSITE_ID    enables Umami (custom events). Optional.
    UMAMI_SRC           default https://cloud.umami.is/script.js
    CF_BEACON_TOKEN     Cloudflare Web Analytics token, only if the domain is NOT proxied by Cloudflare
    ALLOW_AI_TRAINING   "1" (default) allows training crawlers in robots.txt; "0" disallows them
    DATA_LICENSE        default "CC BY 4.0"
    INDEXNOW_KEY        if set, writes /<key>.txt for IndexNow (Bing, Yandex, Seznam, Naver)
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import shutil
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fleet import load_tail_overrides
from .layout import Cabin, build_cabin, list_configs, section_bounds
from .research import BAND_LABEL, BANDS, BASIS, CAVEATS, EXIT_STUDY, STUDIES
from .render import SVG_CSS, render_svg

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ASTRO_JS = (Path(__file__).resolve().parent / "assets" / "astro.js").read_text(encoding="utf-8")


def _tz_options() -> str:
    named = {330: "India, Sri Lanka", 345: "Nepal", 360: "Bangladesh", 240: "UAE, Oman", 180: "Gulf: Saudi, Kuwait, Qatar",
             0: "UK, Ireland (winter)", 60: "Central Europe (winter), UK (summer)", 120: "Central Europe (summer)",
             480: "Singapore, Malaysia, China", -300: "US East (winter)", -480: "US West (winter)", 600: "Sydney (winter)"}
    mins = sorted(set(range(-720, 841, 30)) | {345})
    out = []
    for m in mins:
        sign = "+" if m >= 0 else "-"
        lab = f"UTC{sign}{abs(m) // 60}:{abs(m) % 60:02d}" + (f" {named[m]}" if m in named else "")
        out.append(f'<option value="{m}"{" selected" if m == 330 else ""}>{lab}</option>')
    return "".join(out)


SPONSORS_FILE = Path(__file__).resolve().parent.parent / "sponsors.json"


def load_sponsors(path: Path | None = None) -> list[dict[str, Any]]:
    """Enabled affiliate links from sponsors.json. Entries without an https link are ignored."""
    path = path or SPONSORS_FILE
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for it in data.get("items", []):
        if it.get("enabled") and str(it.get("url", "")).startswith("https://"):
            out.append(it)
    return out


def sponsor_html(where: str, sponsors: list[dict[str, Any]]) -> str:
    items = [it for it in sponsors if where in it.get("where", ["home", "seatmap"])][:3]
    if not items:
        return ""
    cards = "".join(
        f'<a class="sponsor-item" href="{_e(it["url"])}" target="_blank" rel="sponsored noopener" data-sponsor="{_e(it["id"])}">'
        f'<strong>{_e(it["title"])}</strong><span class="muted small">{_e(it.get("text", ""))}</span>'
        f'<span class="sponsor-cta">{_e(it.get("cta", "Open"))} →</span></a>' for it in items)
    return (f'<aside class="sponsor" aria-label="Sponsored links"><p class="sponsor-tag">Sponsored · we may earn a commission, '
            f'at no cost to you</p><div class="sponsor-items">{cards}</div></aside>')


def ad_html(s: "Settings") -> str:
    if not (s.adsense_client and s.adsense_slot):
        return ""
    return (f'<div class="adslot"><p class="sponsor-tag">Advertisement</p><ins class="adsbygoogle" '
            f'data-ad-client="{_e(s.adsense_client)}" data-ad-slot="{_e(s.adsense_slot)}" data-ad-format="auto" '
            f'data-full-width-responsive="true"></ins></div>')


def lucky_fields(prefix: str) -> str:
    """The lucky-seat inputs shared by the seat page (prefix "") and the home shortcut (prefix "lh-")."""
    i = lambda name: prefix + name
    return f"""<label>Date of birth <input type="date" id="{i('dob')}" required></label>
<label>Astrology <select id="{i('sys')}"><option value="vedic">Vedic: moon sign, nakshatra</option><option value="western">Western: sun sign</option><option value="chinese">Chinese zodiac</option></select></label>
<label>Seat type <select id="{i('pref')}"><option value="any">Any</option><option value="window">Window</option><option value="aisle">Aisle</option><option value="middle">Middle</option></select></label>
<button type="submit">Find my lucky seat</button>
<details class="more"><summary>Birth time and flight time (optional)</summary>
<div class="more-grid">
<label>Birth time <input type="time" id="{i('tob')}"></label>
<label>Time zone at birth <select id="{i('tz')}">{_tz_options()}</select></label>
<label>Flight date <input type="date" id="{i('fdate')}"></label>
<label>Departure time <input type="time" id="{i('ftime')}"></label>
</div>
<p class="small muted">Birth time pins down your moon sign and nakshatra (the moon changes sign every two to three days). Flight date and time show that day's Rahu Kalam.</p>
</details>"""
AIRCRAFT_DIR = Path(__file__).resolve().parent.parent / "aircraft"


def aircraft_info(icao: str) -> dict[str, Any] | None:
    f = AIRCRAFT_DIR / f"{icao}.json"
    return json.loads(f.read_text()) if f.exists() else None


def aircraft_html(cfg: dict[str, Any]) -> str:
    a = aircraft_info(cfg["aircraft"]["icao"])
    if not a:
        return ""
    src = a["sources"]
    cite = lambda k: (f' <a class="cite" href="{_e(src[k]["url"])}" rel="nofollow noopener" data-track="source_click" '
                      f'title="{_e(src[k]["label"])}">source</a>') if k in src else ""
    fal = ", ".join(f'{_e(x["site"])} ({_e(x["country"])}{": " + _e(x["note"]) if x.get("note") else ""})' for x in a["final_assembly"])
    rows = [("Manufacturer", f'{_e(a["manufacturer"]["name"])} · {_e(a["manufacturer"]["country"])}'),
            ("Final assembly", fal + (f'. {_e(a["final_assembly_note"])}' if a.get("final_assembly_note") else "")
             + cite(a.get("final_assembly_source")))]
    rows += [(_e(m["event"]), _e(m["when"]) + cite(m["source"])) for m in a["milestones"]]
    eng = cfg.get("engine_on_this_fleet")
    if eng:
        rows.append((f'Engines on {_e(cfg["operator"]["name"])}\'s fleet',
                     _e(eng["text"]) + f' <a class="cite" href="{_e(eng["source"]["url"])}" rel="nofollow noopener" '
                     f'data-track="source_click" title="{_e(eng["source"]["label"])}">source</a>'))
    top = "".join(f"<tr><th scope=row>{k}</th><td>{v}</td></tr>" for k, v in rows)
    engines = "".join(
        f'<li><strong>{_e(e["name"])}</strong>: ' +
        "; ".join(f'{_e(m["name"])} ({_e(m["country"])}{", " + _e(m["share"]) if m.get("share") else ""}'
                  f'{": " + _e(m["builds"]) if m.get("builds") else ""})' for m in e["makers"]) + cite(e["source"]) + "</li>"
        for e in a["engine_options"])
    if a.get("engine_options_note"):
        engines += f'<li class="muted">{_e(a["engine_options_note"])}</li>'
    comps = "".join(f'<tr><td>{_e(c["part"])}</td><td>{_e(c["maker"])}</td><td>{_e(c["country"])}{cite(c["source"])}</td></tr>'
                    for c in a["components"])
    return f"""<section class="aircraft" id="aircraft"><h2>About the {_e(a["name"])}</h2>
<table class="facts">{top}</table>
<h3>Engine options and who makes them</h3><ul>{engines}</ul>
<h3>Major parts and suppliers</h3>
<div class="tablewrap"><table class="facts suppliers"><thead><tr><th>Part</th><th>Made by</th><th>Country</th></tr></thead><tbody>{comps}</tbody></table></div>
<p class="small muted">Major components only; an airliner has thousands of suppliers, and cabin items like seats vary by airline.</p>
</section>
"""


def aircraft_md(cfg: dict[str, Any]) -> list[str]:
    a = aircraft_info(cfg["aircraft"]["icao"])
    if not a:
        return []
    L = [f"## About the {a['name']}", "",
         f"- Manufacturer: {a['manufacturer']['name']} ({a['manufacturer']['country']})",
         "- Final assembly: " + "; ".join(f"{x['site']}, {x['country']}" + (f" ({x['note']})" if x.get('note') else "") for x in a["final_assembly"])]
    L += [f"- {m['event']}: {m['when']}" for m in a["milestones"]]
    if cfg.get("engine_on_this_fleet"):
        L.append(f"- Engines on {cfg['operator']['name']}'s fleet: {cfg['engine_on_this_fleet']['text']}")
    for e in a["engine_options"]:
        L.append(f"- Engine {e['name']}: " + "; ".join(f"{m['name']} ({m['country']}{', ' + m['share'] if m.get('share') else ''})" for m in e["makers"]))
    L += ["", "| Part | Made by | Country |", "|---|---|---|"]
    L += [f"| {c['part']} | {c['maker']} | {c['country']} |" for c in a["components"]]
    L += ["", "Sources: " + "; ".join(f"{v['label']} ({v['url']})" for v in a["sources"].values()), ""]
    return L
LICENSE_URLS = {"CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
                "CC0 1.0": "https://creativecommons.org/publicdomain/zero/1.0/"}

# Search / user-triggered AI fetchers (named explicitly so intent is unambiguous).
AI_SEARCH_BOTS = ["OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot",
                  "Perplexity-User", "MistralAI-Index", "MistralAI-User", "DuckAssistBot", "meta-webindexer"]
# Training crawlers / tokens.
AI_TRAINING_BOTS = ["GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "meta-externalagent",
                    "MistralAI-Training", "CCBot"]

AI_REFERRERS = ["chatgpt.com", "chat.openai.com", "perplexity.ai", "claude.ai", "gemini.google.com",
                "copilot.microsoft.com", "chat.mistral.ai", "you.com", "phind.com", "meta.ai", "grok.com"]


@dataclass
class Settings:
    url: str = "https://example.com"
    name: str = "MyLuckySeat"
    tagline: str = "Find your lucky seat, and know the plane you're on"
    contact_email: str | None = None
    umami_website_id: str | None = None
    umami_src: str = "https://cloud.umami.is/script.js"
    cf_beacon_token: str | None = None
    allow_ai_training: bool = True
    data_license: str = "CC BY 4.0"
    indexnow_key: str | None = None
    adsense_client: str | None = None      # ca-pub-...; loads AdSense, writes ads.txt
    adsense_slot: str | None = None        # a display ad unit id; without it no manual ad units are placed
    build_date: str = field(default_factory=lambda: dt.date.today().isoformat())

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        s = cls()
        s.url = (e("SITE_URL") or s.url).rstrip("/")
        s.name = e("SITE_NAME") or s.name
        s.contact_email = e("CONTACT_EMAIL") or None
        s.umami_website_id = e("UMAMI_WEBSITE_ID") or None
        s.umami_src = e("UMAMI_SRC") or s.umami_src
        s.cf_beacon_token = e("CF_BEACON_TOKEN") or None
        s.allow_ai_training = (e("ALLOW_AI_TRAINING", "1") != "0")
        s.data_license = e("DATA_LICENSE") or s.data_license
        s.indexnow_key = e("INDEXNOW_KEY") or None
        client = (e("ADSENSE_CLIENT") or "").strip()
        if client and not re.fullmatch(r"ca-pub-\d{10,20}", client):
            raise SystemExit(f"ADSENSE_CLIENT should look like ca-pub-1234567890123456, got {client!r}")
        s.adsense_client = client or None
        slot = (e("ADSENSE_SLOT") or "").strip()
        if slot and not slot.isdigit():
            raise SystemExit(f"ADSENSE_SLOT should be the numeric ad unit id, got {slot!r}")
        s.adsense_slot = slot or None
        return s

    @property
    def host(self) -> str:
        return re.sub(r"^https?://", "", self.url).split("/")[0]

    @property
    def license_url(self) -> str | None:
        return LICENSE_URLS.get(self.data_license)


# --------------------------------------------------------------------------- facts

def slug_for(cfg: dict[str, Any]) -> str:
    if cfg.get("slug"):
        return cfg["slug"]
    raw = f'{cfg["operator"]["name"]} {cfg["aircraft"]["name"]} {cfg["total_seats"]} seats'
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def _ranges(nums: list[int]) -> str:
    nums = sorted(nums)
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}" if start == prev else f"{start}–{prev}")
            start = prev = n
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(out)


def _join(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


EXIT_KIND = {"overwing": "over-wing exit", "mid_aft_exit": "rear-cabin exit", "door": "door"}


@dataclass
class Page:
    cabin: Cabin
    slug: str
    title: str
    facts: dict[str, Any]
    variants: list[dict[str, Any]]

    @property
    def cfg(self) -> dict[str, Any]:
        return self.cabin.config

    @property
    def path(self) -> str:
        return f"/seat-maps/{self.slug}/"

    @property
    def md_path(self) -> str:
        return f"/seat-maps/{self.slug}.md"


def summarize(cabin: Cabin) -> dict[str, Any]:
    cfg = cabin.config
    letters, aisles = cfg["letters"], set(cfg["aisles_after"])
    blocks, n = [], 0
    for l in letters:
        n += 1
        if l in aisles:
            blocks.append(n); n = 0
    blocks.append(n)
    seats_by_row: dict[int, list[str]] = {}
    for s in cabin.seats:
        seats_by_row.setdefault(s.row, []).append(s.letter)

    exit_rows: dict[str, list[int]] = {}
    for e in cabin.exits:
        if e.at_row is not None:
            exit_rows.setdefault(e.kind, []).append(e.at_row)
    flags = cfg.get("seat_flags", {})
    pitch = cfg.get("pitch_in", {})
    specific = {int(k): v for k, v in pitch.get("rows", {}).items()}
    groups: dict[float, list[int]] = {}
    for r, v in specific.items():
        groups.setdefault(v, []).append(r)
    pitch_lines = [f"rows {_ranges(rs)}: {v:g} in" for v, rs in sorted(groups.items(), key=lambda kv: min(kv[1]))]
    recline = flags.get("no_recline")
    cab_list = []
    for st in cabin.seats:
        if not cab_list or cab_list[-1]["name"] != st.cabin:
            cab_list.append({"name": st.cabin, "rows": [st.row, st.row], "seats": 0, "letters": []})
        c = cab_list[-1]; c["rows"][1] = st.row; c["seats"] += 1
        if st.letter not in c["letters"]:
            c["letters"].append(st.letter)
    for c in cab_list:
        from .layout import cabin_for
        cab = cabin_for(cfg, c["rows"][0]); n = 0; parts = []
        for l in cab["letters"]:
            n += 1
            if l in cab["aisles_after"]:
                parts.append(str(n)); n = 0
        parts.append(str(n)); c["layout"] = "-".join(parts); c["letters"] = cab["letters"]
    return {
        "cabins": cab_list,
        "total_seats": cfg["total_seats"],
        "layout": "-".join(str(b) for b in blocks),
        "row_first": cfg["rows"]["first"], "row_last": cfg["rows"]["last"],
        "rows_skipped": cfg["rows"].get("skip", []),
        "row_count": len(cabin.rows),
        "exit_rows": exit_rows,
        "missing_seats": cfg.get("missing_seats", []),
        "no_window": flags.get("no_window", []),
        "misaligned_window": flags.get("misaligned_window", []),
        "window_disputed": flags.get("window_disputed", []),
        "pitch_default": pitch.get("default"),
        "pitch_default_text": (f'{pitch["default"]:g} in' if pitch.get("default") is not None else
                               (f'{pitch["default_range"][0]:g}–{pitch["default_range"][1]:g} in'
                                if pitch.get("default_range") else None)),
        "pitch_specific": pitch_lines,
        "pitch_unknown_rows": pitch.get("unknown_rows", []),
        "no_recline": "all seats" if recline == "all" else (recline or []),
        "seats_by_row": seats_by_row,
    }


def exits_sentence(f: dict[str, Any]) -> str:
    parts = []
    for kind, rows in f["exit_rows"].items():
        label = EXIT_KIND.get(kind, kind.replace("_", " "))
        parts.append(f"{label} row{'s' if len(rows) > 1 else ''} {_join([str(r) for r in sorted(rows)])}")
    text = "; ".join(parts)
    return (text[0].upper() + text[1:]) if text else "No exit rows recorded"


def lede(p: Page) -> str:
    f, cfg = p.facts, p.cfg
    if len(f["cabins"]) > 1:
        seats_txt = (f'{f["total_seats"]} seats (' + ", ".join(f'{c["seats"]} {c["name"]} in rows {c["rows"][0]}–{c["rows"][1]}, {c["layout"]}'
                                                            for c in f["cabins"]) + ')')
        s = (f'{cfg["operator"]["name"]}\'s {cfg["aircraft"]["name"]} has {seats_txt} across {f["row_count"]} rows numbered '
             f'{f["row_first"]}–{f["row_last"]}')
    else:
        s = (f'{cfg["operator"]["name"]}\'s {cfg["aircraft"]["name"]} ({cfg["variant_label"]}) has '
             f'{f["total_seats"]} seats in a {f["layout"]} layout across {f["row_count"]} rows numbered '
             f'{f["row_first"]}–{f["row_last"]}')
    s += f', with no row {_join([str(r) for r in f["rows_skipped"]])}. ' if f["rows_skipped"] else ". "
    s += exits_sentence(f) + ", plus doors at the front and rear. "
    if f["missing_seats"]:
        s += f'Seats that do not exist: {", ".join(f["missing_seats"])}. '
    if f["no_window"]:
        s += f'Seats without a window: {", ".join(f["no_window"])}.'
    return s.strip()


def variants_for(cfg: dict[str, Any], all_cfgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    same = [c for c in all_cfgs if c["operator"]["icao"] == cfg["operator"]["icao"]
            and c["aircraft"]["icao"] == cfg["aircraft"]["icao"]]
    swap_ids = {s["config_hint"] for s in cfg.get("swap_risk", [])}
    swaps = [c for c in all_cfgs if c["id"] in swap_ids and c not in same]
    tot = sum(c.get("fleet_share_prior", 1.0) for c in same) or 1
    scale = 1 / tot if tot > 1 else 1
    out = [{"id": c["id"], "slug": slug_for(c), "label": f'{c["aircraft"]["name"]} · {c["total_seats"]} seats',
            "share": round(c.get("fleet_share_prior", 1.0) * scale, 2), "relation": "same_type",
            "current": c["id"] == cfg["id"]} for c in same]
    uncovered = round(max(0.0, 1 - sum(v["share"] for v in out)), 2)
    if any(c.get("other_versions_unquantified") for c in same):
        out.append({"id": None, "slug": None, "label": "Other cabin versions (share unknown)", "share": None,
                    "relation": "unquantified", "current": False,
                    "note": cfg["provenance"].get("fleet_share_note", "")})
    if uncovered >= 0.01:
        out.append({"id": None, "slug": None, "label": "Other cabin versions (not mapped yet)",
                    "share": uncovered, "relation": "unmapped", "current": False,
                    "note": cfg["provenance"].get("fleet_share_note", "")})
    out += [{"id": c["id"], "slug": slug_for(c), "label": f'{c["aircraft"]["name"]} · {c["total_seats"]} seats',
             "share": None, "relation": "swap", "current": False} for c in swaps]
    return out


def sections_info(p: Page) -> dict[str, dict[str, Any]]:
    bounds = section_bounds(p.cfg, p.cabin.rows)
    info = {}
    for name, (lo, hi) in bounds.items():
        seats = [x for x in p.cabin.seats if x.section == name]
        info[name] = {"rows": [lo, hi], "seats": len(seats), "near_exit_seats": sum(x.near_exit for x in seats),
                      "band": BANDS[name], "band_label": BAND_LABEL[BANDS[name]]}
    return info


def safest_answer(p: Page) -> str:
    sec = sections_info(p)
    r = sec["rear"]["rows"]
    far = sorted({x.row for x in p.cabin.seats if not x.near_exit})
    ans = (f"No seat is reliably safest. In two small US studies (37 crashes, 1971–2007), passengers in the rear third "
           f"of the cabin died less often than those further forward; on this aircraft that is rows {r[0]}–{r[1]}. "
           "Results depend heavily on the type of accident, and the FAA says there is no safest seat. "
           "The most consistent finding is sitting within about five rows of an exit")
    ans += (f"; rows {_ranges(far)} are more than five rows from any exit." if far else
            "; every row on this aircraft is within five rows of an exit.")
    return ans


def faq(p: Page) -> list[tuple[str, str]]:
    f, cfg = p.facts, p.cfg
    name = f'{cfg["operator"]["name"]} {cfg["aircraft"]["name"]}'
    same = [v for v in p.variants if v["relation"] == "same_type"]
    q = []
    count = f'This configuration has {f["total_seats"]} seats in a {f["layout"]} layout.'
    unmapped = [v for v in p.variants if v["relation"] == "unmapped"]
    if len(same) > 1 or unmapped:
        parts = [f'{v["label"].split(" · ")[1]} (about {round(v["share"] * 100)}% of the fleet)' for v in same]
        parts += [f'other versions we have not mapped yet (about {round(v["share"] * 100)}%)' for v in unmapped]
        count += (" " + cfg["operator"]["name"] + " operates " + _join(parts) +
                  "; the aircraft registration tells you which one you will get.")
    q.append((f"How many seats does the {name} have?", count))
    q.append((f"Which rows are the exit rows on the {name}?",
              exits_sentence(f) + ". There are also doors at the front and rear of the cabin."))
    q.append((f"Does the {name} have a row 13?",
              "No. Row numbering skips 13." if 13 in f["rows_skipped"] else
              "Yes. Rows are numbered continuously."))
    if f["no_window"]:
        q.append((f"Which seats on the {name} have no window?",
                  f'{_join(f["no_window"])} have no window.' +
                  (f' Windows are offset at {", ".join(f["misaligned_window"])}.' if f["misaligned_window"] else "")))
    q.append((f"Which seat is safest on the {name}?", safest_answer(p)))
    return q


# --------------------------------------------------------------------------- html

def _e(x: Any) -> str:
    return html.escape(str(x), quote=True)


def head(s: Settings, *, title: str, description: str, path: str, jsonld: list[dict] | None = None,
         md_path: str | None = None, og_type: str = "website") -> str:
    canonical = s.url + path
    parts = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_e(title)}</title>",
        f'<meta name="description" content="{_e(description)}">',
        f'<link rel="canonical" href="{_e(canonical)}">',
        '<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">',
        f'<meta property="og:site_name" content="{_e(s.name)}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:title" content="{_e(title)}">',
        f'<meta property="og:description" content="{_e(description)}">',
        f'<meta property="og:url" content="{_e(canonical)}">',
        f'<meta property="og:image" content="{_e(s.url)}/assets/og.jpg">',
        '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="theme-color" content="#0b2545" media="(prefers-color-scheme: light)">',
        '<meta name="theme-color" content="#07101f" media="(prefers-color-scheme: dark)">',
        '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">',
        f'<link rel="stylesheet" href="/assets/site.css?v={ASSET_VERSION}">',
        f'<link rel="alternate" type="text/plain" title="LLM summary" href="/llms.txt">',
    ]
    if md_path:
        parts.append(f'<link rel="alternate" type="text/markdown" href="{_e(md_path)}">')
    for block in jsonld or []:
        parts.append('<script type="application/ld+json">' +
                     json.dumps(block, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    if s.umami_website_id:
        # Served through our Worker (/u/*) so ad blockers and the CSP don't stop it; counts apex and www.
        domains = s.host if s.host.startswith("www.") or s.host.endswith("workers.dev") else f"{s.host},www.{s.host}"
        parts.append(f'<script defer src="/u/s.js" data-website-id="{_e(s.umami_website_id)}" '
                     f'data-host-url="{_e(s.url)}/u" data-domains="{_e(domains)}"></script>')
    if s.cf_beacon_token:
        parts.append('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
                     f'data-cf-beacon=\'{{"token":"{_e(s.cf_beacon_token)}"}}\'></script>')
    if s.adsense_client:
        parts.append(f'<meta name="google-adsense-account" content="{_e(s.adsense_client)}">')
        parts.append(f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={_e(s.adsense_client)}" '
                     'crossorigin="anonymous"></script>')
    parts.append(f'<script defer src="/assets/app.js?v={ASSET_VERSION}"></script>')
    return "\n".join(parts)


def shell(s: Settings, head_html: str, body: str, band: str = "", band_cls: str = "band-page") -> str:
    band_html = f'<section class="band {band_cls}"><div class="wrap">{band}</div></section>' if band else ""
    contact = (f' · <a href="mailto:{_e(s.contact_email)}?subject=Seat%20map%20correction" '
               f'data-track="correction_click">Report a correction</a>' if s.contact_email else "")
    return f"""<!doctype html>
<html lang="en">
<head>
{head_html}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap">
<a class="brand" href="/"><img src="/assets/favicon.svg" alt="" width="22" height="22">{_e(s.name)}</a>
<nav><a href="/#lookup">Which plane is my flight?</a><a href="/#seat-maps">Seat maps</a><a href="/research/">Safest seats?</a></nav>
</div></header>
{band_html}
<main id="main" class="wrap">
{body}
</main>
<footer class="foot"><div class="wrap">
<p>{_e(s.name)} publishes sourced layout facts. Section colours summarise two small crash studies and are not a rating of any seat. Data: {_e(s.data_license)}{contact}.</p>
<p><a href="/research/">Where you sit and crash outcomes</a> · <a href="/privacy/">Privacy</a> · <button type="button" class="linkbtn" data-feedback-open>Send feedback</button></p>
</div></footer>
<button type="button" class="fab" data-feedback-open aria-haspopup="dialog">Feedback</button>
<dialog id="feedback" aria-labelledby="fb-h">
<form id="fbform" class="fbform">
<div class="fbhead"><h2 id="fb-h">Tell us what you think</h2><button type="button" class="fbclose" data-feedback-close aria-label="Close">×</button></div>
<fieldset class="chips"><legend>Was this page useful?</legend>
<label><input type="radio" name="rating" value="up"> <span>Yes</span></label>
<label><input type="radio" name="rating" value="down"> <span>Not really</span></label></fieldset>
<label>What's it about?
<select name="kind"><option value="idea">An idea or request</option><option value="wrong">Something is wrong on a seat map</option><option value="airline">Add my airline or aircraft</option><option value="other">Something else</option></select></label>
<label>Your message <textarea name="message" rows="4" maxlength="1500" placeholder="For a wrong seat map, tell us the flight, date and what was different."></textarea></label>
<label>Email (optional, only if you want a reply) <input name="email" type="email" maxlength="200" autocomplete="email"></label>
<label class="hp" aria-hidden="true">Leave empty <input name="website" tabindex="-1" autocomplete="off"></label>
<div class="fbactions"><button type="submit">Send</button><span class="muted small" id="fbstatus" aria-live="polite"></span></div>
</form>
</dialog>
</body>
</html>
"""


def status_badge(cfg: dict[str, Any]) -> str:
    if cfg.get("detail") == "sections_only":
        return ('<span class="badge">Section map · rows and exits only; seat-level details not mapped</span>' +
                _status_badge(cfg))
    return _status_badge(cfg)


def _status_badge(cfg: dict[str, Any]) -> str:
    st = cfg["provenance"]["status"]
    label = {"draft_unverified": "Draft · not yet checked against the airline's live seat map",
             "verified": "Verified against the airline's live seat map"}.get(st, st.replace("_", " "))
    return f'<span class="badge badge-{_e(st)}">{_e(label)}</span>'


def seat_payload(cabin: Cabin) -> dict[str, Any]:
    return {s.seat: {"p": s.position, "sd": s.side, "a": s.seats_to_aisle, "pi": s.pitch_in, "ps": s.pitch_source,
                     "x": s.in_exit_row, "f": s.flags, "z": s.section, "ne": s.near_exit, "c": s.cabin,
                     "e": [{"id": e["exit"], "k": e["kind"], "d": e["direction"], "r": e["rows_between"]}
                           for e in s.nearest_exits]}
            for s in cabin.seats}


def seatmap_page(s: Settings, p: Page, updated: str) -> str:
    f, cfg, prov = p.facts, p.cfg, p.cfg["provenance"]
    desc = lede(p)
    if len(desc) > 300:
        desc = desc[:297].rsplit(" ", 1)[0] + "…"
    title = f'{p.title} seat map: exit rows, row numbers and seat facts'

    # key facts table
    rows = [
        ("Seats", f'{f["total_seats"]} ({_e(cfg["variant_label"])})'),
        (("Cabins", "; ".join(f'{_e(c["name"])}: rows {c["rows"][0]}–{c["rows"][1]}, {c["seats"]} seats, {c["layout"]} '
                              f'({" ".join(c["letters"])})' for c in f["cabins"])) if len(f["cabins"]) > 1 else
         ("Layout", f'{f["layout"]} ({" ".join(cfg["letters"])})')),
        ("Rows", f'{f["row_first"]}–{f["row_last"]}' +
         (f' (no row {", ".join(map(str, f["rows_skipped"]))})' if f["rows_skipped"] else "")),
        ("Exit rows", _e(exits_sentence(f)) + "; doors front and rear"),
    ]
    if f["missing_seats"]:
        rows.append(("Seats that don't exist", ", ".join(f["missing_seats"])))
    if f["no_window"]:
        rows.append(("No window", ", ".join(f["no_window"])))
    if f["misaligned_window"]:
        rows.append(("Window offset", ", ".join(f["misaligned_window"])))
    if f["window_disputed"]:
        rows.append(("Window disputed by sources", ", ".join(f["window_disputed"])))
    pitch = "; ".join(f["pitch_specific"] + ([f'other rows: {f["pitch_default_text"]}'] if f["pitch_default_text"] else []))
    if f["pitch_unknown_rows"]:
        pitch += f'; row{"s" if len(f["pitch_unknown_rows"]) > 1 else ""} {_ranges(f["pitch_unknown_rows"])}: unknown'
    rows.append(('Legroom (pitch) <span class="muted small" title="Pitch is the distance from one seat to the same point on the seat in front. About 28–29 in is tight, 30–32 in is standard economy, 34 in or more is roomy.">?</span>',
                 _e(pitch) + '<br><span class="muted small">Pitch is the distance from one seat to the same point on the seat in front. About 28–29 in is tight, 30–32 in is standard economy, 34 in or more is roomy.</span>'))
    if f["no_recline"]:
        rows.append(("No recline", _e(f["no_recline"] if isinstance(f["no_recline"], str)
                                      else ", ".join(f["no_recline"]))))
    facts_html = "".join(f"<tr><th scope=row>{k}</th><td>{v}</td></tr>" for k, v in rows)

    # row-by-row table
    exit_by_row = {e.at_row: e for e in p.cabin.exits if e.at_row is not None}
    flagmap: dict[str, list[str]] = {}
    for key, label in (("no_window", "no window"), ("misaligned_window", "window offset"),
                       ("window_disputed", "window disputed")):
        for seat in (cfg.get("seat_flags", {}).get(key) or []):
            if isinstance(seat, str) and seat != "all":
                flagmap.setdefault(seat, []).append(label)
    row_html = []
    for r in p.cabin.rows:
        present = f["seats_by_row"].get(r, [])
        missing = [l for l in cfg["letters"] if l not in present]
        notes = []
        if r in exit_by_row:
            notes.append(EXIT_KIND.get(exit_by_row[r].kind, exit_by_row[r].kind) + " row")
        if missing:
            notes.append("no " + ", ".join(f"{r}{l}" for l in missing))
        for l in present:
            for lab in flagmap.get(f"{r}{l}", []):
                notes.append(f"{r}{l} {lab}")
        seat0 = p.cabin.seat(f"{r}{present[0]}") if present else None
        if seat0 is not None and seat0.pitch_source == "range":
            pv = f["pitch_default_text"]
        else:
            pv = "unknown" if seat0 is None or seat0.pitch_in is None else f"{seat0.pitch_in:g} in"
        row_html.append(f'<tr{" class=exit-row" if r in exit_by_row else ""}><th scope=row>{r}</th>'
                        f'<td>{"".join(present)}</td><td>{pv}</td><td>{_e("; ".join(notes))}</td></tr>')

    # variants
    var_html = ""
    same = [v for v in p.variants if v["relation"] == "same_type"]
    swaps = [v for v in p.variants if v["relation"] == "swap"]
    unmapped = [v for v in p.variants if v["relation"] in ("unmapped", "unquantified")]
    if len(same) > 1 or swaps or unmapped:
        items = []
        for v in same:
            share = f' — about {round(v["share"] * 100)}% of this fleet' if v["share"] is not None else ""
            if v["current"]:
                items.append(f'<li><strong>{_e(v["label"])}</strong> (this page){share}</li>')
            else:
                items.append(f'<li><a href="/seat-maps/{_e(v["slug"])}/">{_e(v["label"])}</a>{share}</li>')
        for v in unmapped:
            items.append(f'<li>{_e(v["label"])}' + (f' — about {round(v["share"] * 100)}% of this fleet' if v["share"] is not None else "")
                         + (f'. <span class="muted small">{_e(v["note"])}</span>' if v.get("note") else "") + '</li>')
        for v in swaps:
            items.append(f'<li><a href="/seat-maps/{v["slug"]}/">{_e(v["label"])}</a> — flies the same routes; '
                         f'swaps happen</li>')
        var_html = ('<section><h2>Which version will I get?</h2><p>A flight number alone does not tell you. '
                    'The aircraft registration does, and it is usually known only close to departure.</p>'
                    f'<ul>{"".join(items)}</ul></section>')

    # sources
    corr = prov.get("corroboration", {})
    corr_html = "".join(f"<li><strong>{_e(k.replace('_', ' '))}</strong>: {_e(', '.join(v))}</li>"
                        for k, v in corr.items())
    src_html = "".join(f'<li><a href="{_e(u)}" rel="nofollow noopener" data-track="source_click">'
                       f'{_e(re.sub(r"^https?://(www[.])?", "", u))}</a></li>' for u in prov["sources"])
    conf_html = "".join(f"<li>{_e(c)}</li>" for c in prov.get("conflicts", []))

    svg = render_svg(p.cabin, title=False, inline_style=False, sections=True, shape=True)
    secinfo = sections_info(p)
    sec_rows = "".join(
        f'<tr><th scope=row>{name.capitalize()}</th><td>{v["rows"][0]}–{v["rows"][1]}</td>'
        f'<td><span class="sw sw-{v["band"]}"></span>{_e(v["band_label"])}</td>'
        f'<td>{v["near_exit_seats"]} of {v["seats"]}</td></tr>' for name, v in secinfo.items())
    payload = json.dumps({"config": cfg["id"], "pitchRange": f["pitch_default_text"],
                          "sections": secinfo, "seats": seat_payload(p.cabin)},
                         separators=(",", ":")).replace("</", "<\\/")

    body = f"""
<div class="grid">
  <section class="mapcard" aria-label="Interactive seat map">
    <div class="viewbar" role="group" aria-label="Map view">
      <button type="button" class="viewbtn on" data-view="pattern" aria-pressed="true">Crash-study pattern</button>
      <button type="button" class="viewbtn" data-view="facts" aria-pressed="false">Seat facts</button>
      <button type="button" class="viewbtn" data-view="near" aria-pressed="false">Near an exit</button>
    </div>
    <div class="legend" data-for="facts" hidden><span class="sw sw-exit"></span>Exit row <span class="sw sw-door"></span>Exit</div>
    <div class="legend" data-for="pattern"><span class="sw sw-lower"></span>Lower in both studies <span class="sw sw-higher"></span>Higher
      <p class="small muted">{_e(BASIS)}. Front and middle don't differ consistently, so they share a colour. <a href="/research/">What the studies found</a></p></div>
    <div class="legend" data-for="near" hidden><span class="sw sw-near"></span>Within 5 rows of an exit
      <p class="small muted">The most consistent finding in evacuation research (University of Greenwich). <a href="/research/#exits">Source</a></p></div>
    <div class="mapwrap view-pattern" id="mapwrap">{svg}</div>
  </section>
  <aside class="panel" id="panel" aria-live="polite">
    <h2>Seat details</h2>
    <p class="muted">Select a seat on the map to see its position, pitch, exit row status and distance to the nearest exits.</p>
  </aside>
</div>
<section class="lucky" id="lucky" aria-labelledby="lucky-h">
<h2 id="lucky-h">Lucky seat <span class="badge badge-fun">Just for fun</span></h2>
<p class="muted">Picks a lucky section and seat from your birth date. Choose Vedic (moon sign and nakshatra), Western (sun sign) or Chinese zodiac: the element of your sign picks the section, numerology picks the seat. Add your flight date to see Rahu Kalam. It's entertainment: it says nothing about comfort, exits or anything else on this page. Your birth details stay in your browser.</p>
<form id="luckyform" class="inline-form">
{lucky_fields("")}
</form>
<div id="luckyout" aria-live="polite"></div>
</section>
{sponsor_html("seatmap", load_sponsors())}
<section><h2>Key facts</h2><table class="facts">{facts_html}</table></section>
<section><h2>Cabin sections and crash studies</h2>
<p>{_e(safest_answer(p))}</p>
<table class="facts sections"><thead><tr><th>Section</th><th>Rows</th><th>Crash-study pattern</th><th>Seats within 5 rows of an exit</th></tr></thead><tbody>{sec_rows}</tbody></table>
<p class="small muted">Pattern basis: {_e(BASIS)}. It describes past accidents, not your flight, and no individual seat is rated. <a href="/research/">Read the studies and their limits</a></p></section>
{var_html}
{aircraft_html(cfg)}
{ad_html(s)}
<section><h2>Row by row</h2>
<details class="rows"><summary>Show all {f["row_count"]} rows</summary>
<table class="rowtable"><thead><tr><th>Row</th><th>Seats</th><th>Legroom (pitch)</th><th>Notes</th></tr></thead>
<tbody>{"".join(row_html)}</tbody></table></details></section>
<section><h2>Sources</h2>
<p class="muted">{_e(lede(p))}</p>
<p class="muted">Built from public seat guides and checked across sources (updated {_e(updated)}). Airlines change cabins, so the seat map at check-in is final. Spotted a difference? Use Feedback.</p>
<ul>{src_html}</ul>
<h3>How each fact is corroborated</h3><ul>{corr_html}</ul>
{f"<h3>Where sources disagree</h3><ul>{conf_html}</ul>" if conf_html else ""}
</section>
<script type="application/json" id="seatdata">{payload}</script>
<script defer src="/assets/astro.js?v={ASSET_VERSION}"></script>
<script defer src="/assets/lucky.js?v={ASSET_VERSION}"></script>
"""
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": s.url + "/"},
        {"@type": "ListItem", "position": 2, "name": "Seat maps", "item": s.url + "/#seat-maps"},
        {"@type": "ListItem", "position": 3, "name": p.title, "item": s.url + p.path}]}
    dataset = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"{p.title} seat map", "description": desc, "url": s.url + p.path,
        "identifier": cfg["id"], "dateModified": updated, "isAccessibleForFree": True,
        "keywords": [cfg["operator"]["name"], cfg["aircraft"]["name"], "seat map", "exit rows", "cabin layout"],
        "creator": {"@type": "Organization", "name": s.name, "url": s.url + "/"},
        "isBasedOn": prov["sources"],
        "distribution": [
            {"@type": "DataDownload", "encodingFormat": "application/json",
             "contentUrl": f'{s.url}/api/v1/seatmaps/{cfg["id"]}.json'},
            {"@type": "DataDownload", "encodingFormat": "text/markdown", "contentUrl": s.url + p.md_path}],
    }
    if s.license_url:
        dataset["license"] = s.license_url
    chips = [f'{f["total_seats"]} seats']
    chips += ([f'{c["seats"]} {c["name"]}' for c in f["cabins"]] if len(f["cabins"]) > 1 else [f'{f["layout"]} layout'])
    ow = f["exit_rows"].get("overwing", [])
    if ow:
        chips.append("Exit rows " + " & ".join(map(str, sorted(ow))))
    other = [r for k, v in f["exit_rows"].items() if k != "overwing" for r in v]
    if other:
        chips.append("Extra exit row " + ", ".join(map(str, sorted(other))))
    if 13 in f["rows_skipped"]:
        chips.append("No row 13")
    if f["no_window"]:
        chips.append("No window: " + ", ".join(f["no_window"]))
    if cfg.get("detail") == "sections_only":
        chips.append("Rows & exits only")
    eng = (cfg.get("engine_on_this_fleet") or {}).get("text", "")
    chip_html = "".join(f'<li>{_e(c)}</li>' for c in chips)
    band = f"""<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a> › <a href="/#seat-maps">Seat maps</a> › {_e(cfg["operator"]["name"])}</nav>
<h1>{_e(p.title)} seat map</h1>
<ul class="chips-row">{chip_html}</ul>
<p class="bandcta"><a class="btn btn-yellow" href="#lucky">Find my lucky seat</a> <a class="btn btn-ghost" href="#mapwrap">Explore the seats</a></p>"""
    return shell(s, head(s, title=title, description=desc, path=p.path, jsonld=[crumbs, dataset],
                         md_path=p.md_path, og_type="article"), body, band=band)


def index_page(s: Settings, pages: list[Page]) -> str:
    cards = []
    for p in pages:
        f = p.facts
        cards.append(
            f'<li class="card" data-search="{_e((p.cfg["operator"]["name"] + " " + p.cfg["operator"]["iata"] + " " + p.cfg["aircraft"]["name"] + " " + p.cfg["aircraft"]["icao"]).lower())}">'
            f'<a href="{p.path}"><span class="op">{_e(p.cfg["operator"]["name"])}</span>'
            f'<span class="ac">{_e(p.cfg["aircraft"]["name"])}</span>'
            f'<span class="muted small">{f["total_seats"]} seats · {_e(exits_sentence(f))}</span></a></li>')
    desc = (f"{s.tagline}. Row-by-row seat maps with exit rows, missing and windowless seats, "
            f"each fact sourced and marked with how well it is verified.")
    body = f"""
<section class="lucky lift" id="luckyhome" aria-labelledby="luckyhome-h">
<h2 id="luckyhome-h">Find your lucky seat <span class="badge badge-fun">Just for fun</span></h2>
<p class="muted">Pick your aircraft and birth date. Your Vedic moon sign, Western sun sign or Chinese zodiac, plus numerology, picks a lucky section and seat. Your birth details stay in your browser.</p>
<form id="luckyhomeform" class="inline-form">
<label>Aircraft <select id="lh-ac" required>__LUCKY_OPTIONS__</select></label>
__LUCKY_FIELDS__
</form>
</section>
<section class="lookup" id="lookup" aria-labelledby="lookup-h">
<h2 id="lookup-h">Which plane is my flight?</h2>
<p class="muted">Airlines fly several cabin versions of the same aircraft and swap planes on a route, so your ticket doesn't tell you which seat map applies. Enter the flight number and date: we look up the aircraft scheduled for it and show which seat map you're likely to get.</p>
<form id="lookupform" class="inline-form">
<label>Flight number <input id="lf" required placeholder="6E 2134" autocomplete="off" maxlength="9" autocapitalize="characters"></label>
<label>Date <input id="ld" type="date" required></label>
<button type="submit">Look up</button>
</form>
<p class="muted small" id="lookupnote">Covers the airlines listed below. Flight data from AeroDataBox.</p>
<div id="lookupout" aria-live="polite">
<div class="result example" aria-label="Example result">
<p class="small muted"><strong>Example result</strong></p>
<p class="result-head"><strong>6E 1234</strong> KWI → HYD · 2026-11-09 23:45</p>
<p>IndiGo · Airbus A321neo · registration VT-NOF</p>
<p><span class="badge">Likely</span> IndiGo flies more than one cabin version of this aircraft; ranked by share of the fleet.</p>
<ul><li><a href="/seat-maps/indigo-airbus-a321neo-232-seats/">IndiGo Airbus A321neo (232 seats)</a> · <a class="luckylink" href="/seat-maps/indigo-airbus-a321neo-232-seats/#lucky">lucky seat</a> — about 64%</li>
<li>Other versions we haven't mapped yet — about 36%</li></ul>
<p class="small muted">You get: the route and time, the aircraft type and its registration (when the airline has assigned it), how likely each cabin version is, and one tap to that seat map or your lucky seat on it.</p>
</div></div>
</section>
{sponsor_html("home", load_sponsors())}
<section id="seat-maps">
<div class="searchrow"><h2>Seat maps</h2>
<label class="sr" for="q">Filter by airline or aircraft</label>
<input id="q" type="search" placeholder="Filter: IndiGo, 737, A320neo…" autocomplete="off"></div>
<ul class="cards" id="cards">{"".join(cards)}</ul>
<p class="muted small" id="noresults" hidden>No seat map matches that yet.</p>
</section>
{ad_html(s)}
<section class="three">
<div><h3>Sourced, fact by fact</h3><p>Each layout records which sources back each fact and where they disagree.</p></div>
<div><h3>Honest about gaps</h3><p>Unknown pitch is shown as unknown. Drafts are labelled as drafts until checked against the airline's own seat map.</p></div>
<div><h3>Crash studies, with their limits</h3><p>Colour the cabin by what published crash studies found, and see how small those studies are. <a href="/research/">Read more</a></p></div>
</section>

"""
    site_ld = {"@context": "https://schema.org", "@type": "WebSite", "name": s.name, "url": s.url + "/",
               "description": desc}
    org_ld = {"@context": "https://schema.org", "@type": "Organization", "name": s.name, "url": s.url + "/",
              "logo": s.url + "/assets/favicon.svg"}
    catalog = {"@context": "https://schema.org", "@type": "DataCatalog", "name": f"{s.name} seat maps",
               "url": s.url + "/", "dataset": [{"@type": "Dataset", "name": f"{p.title} seat map",
                                                "url": s.url + p.path} for p in pages]}
    opts, cur = [], None
    for p in pages:
        op = p.cfg["operator"]["name"]
        if op != cur:
            if cur is not None:
                opts.append("</optgroup>")
            opts.append(f'<optgroup label="{_e(op)}">'); cur = op
        opts.append(f'<option value="{_e(p.path)}">{_e(op)} · {_e(p.cfg["aircraft"]["name"])} · {p.cfg["total_seats"]} seats</option>')
    opts.append("</optgroup>")
    body = body.replace("__LUCKY_OPTIONS__", "".join(opts)).replace("__LUCKY_FIELDS__", lucky_fields("lh-"))
    band = """<h1>Find your <span>lucky seat</span>, and know the plane you're on</h1>
<p class="lede">Your moon sign, sun sign or zodiac animal picks a lucky seat, just for fun. Then see the real thing: exit rows, cabin sections, what crash studies found, and the aircraft behind your flight, every fact sourced.</p>
<p class="bandcta"><a class="btn btn-yellow" href="#luckyhome">Find my lucky seat</a> <a class="btn btn-ghost" href="#lookup">Which plane is my flight?</a></p>"""
    return shell(s, head(s, title=f"{s.name}: {s.tagline}", description=desc, path="/",
                         jsonld=[site_ld, org_ld, catalog]), body, band=band, band_cls="band-home")


PRIVACY_MD = """# Privacy

We measure how the site is used so we can improve it. We do not use cookies or advertising trackers, and we do not collect names, emails or flight bookings.

## What is measured
- Page views, referring site, country, device type and browser, aggregated.
- Product events: seat map viewed, seat selected (seat number only), search text typed in the seat-map filter, source link clicked, flight lookups (airline code and result type only), lucky-seat use (astrology system, sign and seat type only; never your birth date or time), and whether a visit came from an AI assistant such as ChatGPT or Perplexity.

## Flight lookup
- The flight number and date you enter are sent to our server and to AeroDataBox, our flight data provider, to find the aircraft. We keep results in a short-lived cache and do not link them to you.

## Feedback
- If you send feedback, we store your message, the page it was sent from, and your email only if you give it. We use it to fix seat maps and improve the site.

## Lucky seat
- Your birth date, birth time and flight time never leave your browser. Only the astrology system, resulting sign and seat type are counted.

## Tools
- Cloudflare Web Analytics (cookieless, aggregated).
- Umami (cookieless, aggregated), if enabled.

## Contact
Questions about this policy: see the contact link in the footer.
"""


def privacy_md(s: "Settings") -> str:
    text = PRIVACY_MD
    extra = []
    if load_sponsors():
        extra.append("## Sponsored links\n- Some links are marked Sponsored. If you book through them we may earn a commission. "
                     "The partner may set its own cookies once you are on its site.")
    if s.adsense_client:
        text = text.replace("We do not use cookies or advertising trackers, and we do not collect",
                            "Our own measurement uses no cookies. Ads are served by Google AdSense, which may use cookies; we do not collect")
        extra.append("## Advertising\n- Ads are served by Google AdSense. Google may use cookies to show and measure ads. "
                     "In the EEA, the UK and Switzerland you are asked for consent first. "
                     "See [how Google uses information from sites that use its services](https://policies.google.com/technologies/partner-sites).")
    if extra:
        text = text.replace("## Tools", "\n\n".join(extra) + "\n\n## Tools")
    return text


def flight_template(s: "Settings") -> str:
    """Shell for Worker-rendered flight pages; the Worker fills the placeholders."""
    h = head(s, title="__TITLE__", description="__DESC__", path="/flights/__SLUG__/")
    h = h.replace(f'<link rel="alternate" type="text/plain" title="LLM summary" href="/llms.txt">',
                  '<link rel="alternate" type="text/plain" title="LLM summary" href="/llms.txt">\n<!--JSONLD-->')
    return shell(s, h, "<!--BODY-->", band="<!--BAND-->")


def research_md(s: "Settings") -> str:
    lines = ["# Where you sit and crash outcomes", "",
             "No seat is reliably safest. Two small US studies found that passengers in the rear of the cabin died less "
             "often in crashes that had both survivors and fatalities, and evacuation research found that sitting within "
             "about five rows of an exit matters most. Both findings come with large caveats, listed below.", "",
             "## What the studies found", "",
             "Share of passengers who died, by section, in crashes that had both survivors and fatalities:", "",
             "| Study | Sample | Front | Middle | Rear |", "|---|---|---|---|---|"]
    for st in STUDIES:
        d = st["died_pct"]
        lines.append(f"| {st['name']} | {st['short']} | {d['front']}% | {d['middle']}% | {d['rear']}% |")
    lines += [""]
    for st in STUDIES:
        lines += [f"**{st['name']}.** {st['data']}. Sections: {st['definition']}. {st['notes']}", ""]
    lines += ["## Exits {#exits}", "",
              f"**{EXIT_STUDY['name']}.** {EXIT_STUDY['sample']}. {EXIT_STUDY['finding']} {EXIT_STUDY['caveat']}", "",
              "## How the section colours are derived", "",
              "- Each cabin is split into thirds by row: front, middle, rear.",
              f"- Basis shown next to the colours: {BASIS}.",
              "- **Lower:** the rear third, which had the lowest fatality figure in both studies.",
              "- **Higher:** the front and middle thirds. They swap order between the two studies, so they share one colour.",
              "- The colours describe past accidents in two studies. They are not a prediction for any flight, and no individual seat is rated.", "",
              "## Limits", ""]
    lines += [f"- {c}" for c in CAVEATS]
    lines += ["", "## Sources", ""]
    lines += [f"- {st['url_label']}: {st['url']}" for st in STUDIES]
    lines += [f"- {EXIT_STUDY['url_label']}: {EXIT_STUDY['url']}"]
    return "\n".join(lines) + "\n"


def md_to_html(md: str) -> str:
    """Tiny Markdown subset: headings (with optional {#id}), paragraphs, bullet lists, **bold**, `code`."""
    out, in_list, in_table = [], False, False

    def inline(t: str) -> str:
        t = _e(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        return re.sub(r"(https://[^\s<]+)", r'<a href="\1" rel="nofollow noopener" data-track="source_click">\1</a>', t)

    for block in md.strip().split("\n"):
        line = block.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r"-{3,}", c) for c in cells):
                continue
            if not in_table:
                out.append('<div class="tablewrap"><table class="facts"><thead><tr>' +
                           "".join(f"<th>{inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</tbody></table></div>"); in_table = False
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        m = re.match(r"^(#{1,3}) (.+?)(?: \{#([\w-]+)\})?$", line)
        if m:
            lvl, text, anchor = len(m.group(1)), m.group(2), m.group(3)
            out.append(f'<h{lvl}{f" id={anchor}" if anchor else ""}>{inline(text)}</h{lvl}>')
        elif line:
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</tbody></table></div>")
    return "\n".join(out)


def strip_anchors(md: str) -> str:
    return re.sub(r" \{#[\w-]+\}", "", md)


def text_page(s: Settings, md: str, path: str, title: str, description: str) -> str:
    lines = md.strip().split("\n")
    h1 = lines[0][2:] if lines and lines[0].startswith("# ") else title
    rest = "\n".join(lines[1:]) if lines and lines[0].startswith("# ") else md
    return shell(s, head(s, title=f"{title} — {s.name}", description=description, path=path,
                         md_path=path.rstrip("/") + ".md"), f'<article class="prose">{md_to_html(rest)}</article>',
                 band=f"<h1>{_e(h1)}</h1>")


# --------------------------------------------------------------------------- markdown / agents

def seatmap_md(s: Settings, p: Page, updated: str) -> str:
    f, cfg, prov = p.facts, p.cfg, p.cfg["provenance"]
    lines = [f"# {p.title} seat map", "", lede(p), "",
             f"- Status: {prov['status'].replace('_', ' ')} (first-party check: {prov.get('primary_source_check', 'not done')})",
             f"- Updated: {updated}", f"- Canonical: {s.url}{p.path}",
             f"- JSON: {s.url}/api/v1/seatmaps/{cfg['id']}.json",
             f"- Licence: {s.data_license}; cite as \"{s.name}, {p.title} seat map, {s.url}{p.path}\"", "",
             "## Key facts", "",
             f"- Seats: {f['total_seats']} ({cfg['variant_label']})",
             (f"- Cabins: " + "; ".join(f"{c['name']} rows {c['rows'][0]}–{c['rows'][1]}, {c['seats']} seats, {c['layout']}" for c in f["cabins"])
              if len(f["cabins"]) > 1 else f"- Layout: {f['layout']} ({' '.join(cfg['letters'])})"),
             f"- Rows: {f['row_first']}–{f['row_last']}" + (f" (no row {', '.join(map(str, f['rows_skipped']))})" if f['rows_skipped'] else ""),
             f"- Exits: {exits_sentence(f)}; doors front and rear"]
    if f["missing_seats"]:
        lines.append(f"- Seats that don't exist: {', '.join(f['missing_seats'])}")
    if f["no_window"]:
        lines.append(f"- No window: {', '.join(f['no_window'])}")
    if f["misaligned_window"]:
        lines.append(f"- Window offset: {', '.join(f['misaligned_window'])}")
    if f["window_disputed"]:
        lines.append(f"- Window disputed by sources: {', '.join(f['window_disputed'])}")
    pitch = "; ".join(f["pitch_specific"] + ([f"other rows: {f['pitch_default_text']}"] if f["pitch_default_text"] else []))
    if f["pitch_unknown_rows"]:
        pitch += f"; row{'s' if len(f['pitch_unknown_rows']) > 1 else ''} {_ranges(f['pitch_unknown_rows'])}: unknown"
    lines.append(f"- Legroom (seat pitch, approx., see sources): {pitch}. Pitch is the distance from one seat to the same point on the seat in front. About 28–29 in is tight, 30–32 in is standard economy, 34 in or more is roomy.")
    lines += [f"- Seat safety: {safest_answer(p)}", ""]
    lines += ["## Cabin sections", "", "| Section | Rows | Crash-study pattern | Seats within 5 rows of an exit |", "|---|---|---|---|"]
    for name, v in sections_info(p).items():
        lines.append(f"| {name.capitalize()} | {v['rows'][0]}–{v['rows'][1]} | {v['band_label']} | {v['near_exit_seats']} of {v['seats']} |")
    lines += ["", f"Pattern basis: {BASIS}. See {s.url}/research/", ""]
    same = [v for v in p.variants if v["relation"] == "same_type"]
    swaps = [v for v in p.variants if v["relation"] == "swap"]
    if len(same) > 1 or swaps or any(x["relation"] in ("unmapped", "unquantified") for x in p.variants):
        lines += ["## Other versions", ""]
        for v in same:
            if not v["current"]:
                lines.append(f"- {v['label']} (about {round(v['share'] * 100)}% of this fleet): {s.url}/seat-maps/{v['slug']}.md")
        for v in [x for x in p.variants if x["relation"] in ("unmapped", "unquantified")]:
            share = f"about {round(v['share'] * 100)}% of this fleet" if v["share"] is not None else "share unknown"
            lines.append(f"- {v['label']}: {share}. {v.get('note', '')}".rstrip())
        for v in swaps:
            lines.append(f"- {v['label']} (swapped onto the same routes): {s.url}/seat-maps/{v['slug']}.md")
        lines.append("")
    lines += aircraft_md(cfg)
    lines += ["## Row by row", "", "| Row | Seats | Exit row |", "|---|---|---|"]
    exit_rows = {e.at_row for e in p.cabin.exits if e.at_row is not None}
    for r in p.cabin.rows:
        lines.append(f"| {r} | {''.join(f['seats_by_row'].get(r, []))} | {'yes' if r in exit_rows else ''} |")
    lines += ["", "## Questions", ""]
    for q, a in faq(p):
        lines += [f"**{q}**", a, ""]
    lines += ["## Sources", ""] + [f"- {u}" for u in prov["sources"]]
    if prov.get("conflicts"):
        lines += ["", "## Where sources disagree", ""] + [f"- {c}" for c in prov["conflicts"]]
    return "\n".join(lines) + "\n"


def llms_txt(s: Settings, pages: list[Page]) -> str:
    lines = [f"# {s.name}", "",
             f"> {s.name}: a just-for-fun lucky seat finder (Vedic moon sign and nakshatra, Western sun sign or Chinese zodiac, with numerology and Rahu Kalam) on top of sourced, row-by-row airline seat maps (exit rows, seats that don't exist, windowless seats, "
             "approximate pitch), each fact with its sources and a verification status. "
             "Cabin sections are coloured by what two small crash studies found; no seat-level risk scores.", "",
             "Notes for agents:",
             "- Status matters: \"draft\" layouts are from public sources and not yet checked against the airline's live seat map. Say so when citing.",
             "- One airline + aircraft type can have several cabin versions; the aircraft registration decides which. Each page lists the versions and fleet shares.",
             "- Each page also covers the aircraft type: where it is assembled, engine options and makers, and major part suppliers with countries, each with a source.",
             f"- Licence: {s.data_license}. Please cite the canonical page URL.", "",
             "## Seat maps", ""]
    for p in pages:
        lines.append(f"- [{p.title}]({s.url}{p.md_path}): {p.facts['total_seats']} seats; {exits_sentence(p.facts)}")
    lines += ["", "## Data", "",
              f"- [Catalog (JSON)]({s.url}/api/v1/index.json): every configuration with summary facts",
              f"- [OpenAPI]({s.url}/openapi.json): JSON endpoints, including the live flight lookup",
              f"- Flight pages: {s.url}/flights/6e-1234/ (any covered flight number) shows today's aircraft, likely seat map and aircraft seen on earlier days; list in {s.url}/sitemap-flights.xml",
              f"- Live flight lookup: GET {s.url}/api/lookup?flight=6E1234&date=YYYY-MM-DD returns the scheduled aircraft, registration if assigned, and the likely seat maps with probabilities (rate-limited; covered airlines only)",
              f"- [Full text]({s.url}/llms-full.txt): all seat maps in one Markdown file", "",
              "## Docs", "",
              f"- [Where you sit and crash outcomes]({s.url}/research.md): the studies, their samples and limits, and how the section colours are derived",
              "",
              "## Optional", "",
              f"- [Privacy]({s.url}/privacy.md)"]
    return "\n".join(lines) + "\n"


def robots_txt(s: Settings) -> str:
    lines = ["# Search engines and AI assistants are welcome.", "", "User-agent: *", "Allow: /", ""]
    lines += ["# AI search and user-triggered fetchers: explicitly allowed"]
    for b in AI_SEARCH_BOTS:
        lines += [f"User-agent: {b}"]
    lines += ["Allow: /", ""]
    lines += ["# AI training crawlers"]
    for b in AI_TRAINING_BOTS:
        lines += [f"User-agent: {b}"]
    lines += ["Allow: /" if s.allow_ai_training else "Disallow: /", ""]
    train = "yes" if s.allow_ai_training else "no"
    lines += ["# Content signals (contentsignals.org)", f"Content-Signal: search=yes, ai-input=yes, ai-train={train}", "",
              f"Sitemap: {s.url}/sitemap.xml", f"Sitemap: {s.url}/sitemap-flights.xml"]
    return "\n".join(lines) + "\n"


def sitemap_xml(s: Settings, entries: list[tuple[str, str]]) -> str:
    urls = "".join(f"<url><loc>{_e(s.url + path)}</loc><lastmod>{lm}</lastmod></url>" for path, lm in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')


def api_record(s: Settings, p: Page, updated: str, *, with_seats: bool) -> dict[str, Any]:
    cfg, f = p.cfg, p.facts
    rec = {
        "id": cfg["id"], "url": s.url + p.path, "markdown": s.url + p.md_path,
        "operator": cfg["operator"], "aircraft": cfg["aircraft"], "variant_label": cfg["variant_label"],
        "status": cfg["provenance"]["status"], "updated": updated,
        "summary": lede(p),
        "facts": {k: v for k, v in f.items() if k != "seats_by_row"},
        "fleet_share_prior": cfg.get("fleet_share_prior"),
        "variants": [{k: v for k, v in x.items() if k != "current"} for x in p.variants if not x["current"]],
        "seat_safety": {"estimable": False,
                        "reason": "No validated occupant-level dataset supports a per-seat survival probability."},
        "sections": sections_info(p),
        "aircraft": aircraft_info(cfg["aircraft"]["icao"]),
        "engine_on_this_fleet": cfg.get("engine_on_this_fleet"),
        "section_pattern": {"basis": BASIS, "studies": [x["id"] for x in STUDIES], "research": s.url + "/research/",
                            "caveat": "Describes past US accidents; not a prediction for any flight or seat."},
    }
    if with_seats:
        rec["provenance"] = cfg["provenance"]
        rec["exits"] = cfg["exits"]
        rec["seats"] = [x.to_dict() for x in p.cabin.seats]
    return rec


def openapi(s: Settings) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": f"{s.name} seat map data", "version": "1",
                 "description": "Static, read-only JSON. No authentication. Layout facts only; no safety scores.",
                 "license": {"name": s.data_license, **({"url": s.license_url} if s.license_url else {})}},
        "servers": [{"url": s.url}],
        "paths": {
            "/api/lookup": {"get": {
                "operationId": "lookupFlight",
                "summary": "Which seat map a flight is likely to use",
                "description": "Looks up the aircraft scheduled for a flight number and local date and matches it to mapped cabin versions with probabilities. Covered airlines only; rate-limited; results cached.",
                "parameters": [
                    {"name": "flight", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Flight number, e.g. 6E1234 or FR 1234"},
                    {"name": "date", "in": "query", "required": True, "schema": {"type": "string", "format": "date"}, "description": "Local departure date, YYYY-MM-DD"}],
                "responses": {"200": {"description": "Legs with aircraft, registration and match (tier, reason, candidates, uncovered_share)",
                                      "content": {"application/json": {"schema": {"type": "object"}}}},
                              "400": {"description": "Bad flight number or date"}, "429": {"description": "Rate limited"},
                              "503": {"description": "Lookup not configured"}}}},
            "/api/v1/index.json": {"get": {"operationId": "listSeatMaps", "summary": "List all seat map configurations",
                                           "responses": {"200": {"description": "Catalog",
                                                                 "content": {"application/json": {"schema": {"type": "object"}}}}}}},
            "/api/v1/seatmaps/{id}.json": {"get": {
                "operationId": "getSeatMap", "summary": "One configuration with every seat",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"},
                                "description": "Configuration id, e.g. FR-B738-189Y"}],
                "responses": {"200": {"description": "Seat map", "content": {"application/json": {"schema": {"type": "object"}}}},
                              "404": {"description": "Unknown id"}}}},
        },
    }


def headers_file(s: Settings, md_paths: list[str]) -> str:
    script_src = ["'self'", "https://static.cloudflareinsights.com"]
    connect_src = ["'self'", "https://cloudflareinsights.com"]
    img_src, style_src, frame_src = ["'self'", "data:"], ["'self'"], []
    if s.adsense_client:
        # AdSense and Google's consent message load from these hosts and inject styles; this loosens the policy.
        g = ["https://pagead2.googlesyndication.com", "https://*.googlesyndication.com", "https://*.doubleclick.net",
             "https://adservice.google.com", "https://*.google.com", "https://www.googletagservices.com",
             "https://fundingchoicesmessages.google.com", "https://*.gstatic.com"]
        script_src += g
        connect_src += g
        img_src.append("https:")
        style_src.append("'unsafe-inline'")
        frame_src = g + ["https://*.adtrafficquality.google"]
        script_src.append("https://*.adtrafficquality.google")
        connect_src.append("https://*.adtrafficquality.google")
    csp = (f"default-src 'self'; script-src {' '.join(script_src)}; connect-src {' '.join(connect_src)}; "
           f"img-src {' '.join(img_src)}; style-src {' '.join(style_src)}; font-src 'self'; "
           + (f"frame-src {' '.join(frame_src)}; " if frame_src else "")
           + "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
    blocks = [
        "/*", "  X-Content-Type-Options: nosniff", "  Referrer-Policy: strict-origin-when-cross-origin",
        "  Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()",
        f"  Content-Security-Policy: {csp}", "",
        "/assets/*", "  Cache-Control: public, max-age=86400", "",
        "/api/*", "  Access-Control-Allow-Origin: *", "  Cache-Control: public, max-age=3600", "",
        "/llms.txt", "  Content-Type: text/plain; charset=utf-8", "",
        "/llms-full.txt", "  Content-Type: text/plain; charset=utf-8", "",
        "/.well-known/api-catalog", "  Content-Type: application/linkset+json", "",
        "/openapi.json", "  Access-Control-Allow-Origin: *", "",
        "/_tpl/*", "  X-Robots-Tag: noindex", "",
    ]
    for p in md_paths:
        blocks += [p, "  Content-Type: text/markdown; charset=utf-8", ""]
    return "\n".join(blocks)


APP_JS = r"""(() => {
  const track = (name, data) => {
    try { if (window.umami && typeof window.umami.track === 'function') window.umami.track(name, data || {}); } catch (_) {}
  };
  window.aeroTrack = track;

  // Ads and sponsored links (both off unless configured at build time)
  document.querySelectorAll('ins.adsbygoogle').forEach(() => { try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (_) {} });
  document.querySelectorAll('[data-sponsor]').forEach(a => a.addEventListener('click', () => track('sponsor_click', { id: a.dataset.sponsor, page: location.pathname })));

  // 0. Feedback dialog (every page)
  const dlg = document.getElementById('feedback');
  if (dlg) {
    const open = () => { try { dlg.showModal(); } catch (_) { dlg.setAttribute('open', ''); } track('feedback_open', { page: location.pathname }); };
    document.querySelectorAll('[data-feedback-open]').forEach(b => b.addEventListener('click', open));
    dlg.querySelectorAll('[data-feedback-close]').forEach(b => b.addEventListener('click', () => dlg.close()));
    dlg.addEventListener('click', ev => { if (ev.target === dlg) dlg.close(); });
    const fb = document.getElementById('fbform'), st = document.getElementById('fbstatus'), btn = fb.querySelector('button[type=submit]');
    let cfgId = null; try { cfgId = JSON.parse(document.getElementById('seatdata').textContent).config; } catch (_) {}
    fb.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(fb);
      const body = { page: location.pathname, config: cfgId, rating: fd.get('rating') || null, kind: fd.get('kind'),
                     message: String(fd.get('message') || '').trim(), email: String(fd.get('email') || '').trim(), website: fd.get('website') || '' };
      if (!body.rating && !body.message) { st.textContent = 'Choose Yes or Not really, or write a message.'; return; }
      st.textContent = 'Sending…'; btn.disabled = true;
      try {
        const r = await fetch('/api/feedback', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
        const j = await r.json().catch(() => ({}));
        if (r.ok) { st.textContent = 'Thanks, got it.'; fb.reset(); track('feedback_submit', { kind: body.kind, rating: body.rating || 'none' }); setTimeout(() => { dlg.close(); st.textContent = ''; }, 1400); }
        else st.textContent = j.message || 'Could not send. Please try again.';
      } catch (_) { st.textContent = 'Network error. Please try again.'; }
      btn.disabled = false;
    });
  }

  // 1. Visits referred by AI assistants (referrer, or utm_source such as chatgpt.com)
  try {
    const AI = __AI_REFERRERS__;
    const ref = document.referrer ? new URL(document.referrer).hostname.replace(/^www\./, '') : '';
    const utm = new URLSearchParams(location.search).get('utm_source') || '';
    const hit = AI.find(d => ref === d || ref.endsWith('.' + d) || utm.includes(d));
    let seen = false; try { seen = sessionStorage.getItem('ai_ref') === '1'; } catch (_) {}
    if (hit && !seen) {
      const send = () => track('ai_referral', { source: hit, landing: location.pathname });
      if (window.umami) send(); else window.addEventListener('load', () => setTimeout(send, 300));
      try { sessionStorage.setItem('ai_ref', '1'); } catch (_) {}
    }
  } catch (_) {}

  // 2. Outbound / tagged links
  document.addEventListener('click', (ev) => {
    const a = ev.target.closest && ev.target.closest('a[data-track]');
    if (!a) return;
    let host = ''; try { host = new URL(a.href).hostname; } catch (_) {}
    track(a.dataset.track, { host, page: location.pathname });
  });

  // 3. Seat-map filter on the home page
  const q = document.getElementById('q');
  if (q) {
    const cards = Array.from(document.querySelectorAll('#cards [data-search]'));
    const none = document.getElementById('noresults');
    let timer;
    q.addEventListener('input', () => {
      const v = q.value.trim().toLowerCase();
      let shown = 0;
      cards.forEach(c => { const ok = !v || v.split(/\s+/).every(t => c.dataset.search.includes(t)); c.hidden = !ok; if (ok) shown++; });
      none.hidden = shown !== 0;
      clearTimeout(timer);
      if (v.length >= 2) timer = setTimeout(() => track('seatmap_filter', { q: v.slice(0, 40), results: shown }), 800);
    });
  }

  // 3b. Lucky seat finder shortcut (home page): birth date travels in the URL fragment, never to the server.
  const lhf = document.getElementById('luckyhomeform');
  if (lhf) lhf.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const v = (id) => (document.getElementById('lh-' + id) || {}).value || '';
    const path = v('ac'), dob = v('dob');
    if (!path || !dob) return;
    track('lucky_shortcut', { path, system: v('sys') });
    const q = new URLSearchParams({ lucky: dob, pref: v('pref'), sys: v('sys') });
    for (const [k, id] of [['tob', 'tob'], ['tz', 'tz'], ['fd', 'fdate'], ['ft', 'ftime']]) if (v(id)) q.set(k, v(id));
    location.href = path + '#' + q.toString();
  });

  // 4. Flight lookup (home page)
  const lform = document.getElementById('lookupform');
  if (lform) {
    const lf = document.getElementById('lf'), ld = document.getElementById('ld');
    const out = document.getElementById('lookupout'), note = document.getElementById('lookupnote');
    const pad = n => String(n).padStart(2, '0');
    const t = new Date(); ld.value = t.getFullYear() + '-' + pad(t.getMonth() + 1) + '-' + pad(t.getDate());
    const disable = (msg) => { lform.querySelectorAll('input,button').forEach(el => el.disabled = true); note.textContent = msg; };
    fetch('/api/lookup?status=1').then(r => r.ok ? r.json() : { enabled: false })
      .then(j => { if (!j.enabled) disable('Flight lookup is coming soon. Browse the seat maps below meanwhile.'); })
      .catch(() => disable('Flight lookup is coming soon. Browse the seat maps below meanwhile.'));
    const esc2 = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
    const TIER = { exact: 'Exact match', likely: 'Likely', unavailable: 'Not covered yet' };
    lform.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const flight = lf.value.trim(), date = ld.value;
      const carrier = (flight.toUpperCase().match(/^([A-Z0-9]{2,3}?)\s*\d/) || [])[1] || '';
      out.innerHTML = '<p class="muted">Looking up…</p>';
      track('lookup_submit', { carrier });
      let j;
      try {
        const r = await fetch('/api/lookup?flight=' + encodeURIComponent(flight) + '&date=' + encodeURIComponent(date));
        j = await r.json();
        if (!r.ok) { out.innerHTML = '<p class="note">' + esc2(j.message || 'Something went wrong.') + '</p>'; track('lookup_result', { carrier, tier: j.error || 'error' }); return; }
      } catch (_) { out.innerHTML = '<p class="note">Network error. Try again.</p>'; return; }
      if (j.unsupported_airline) { out.innerHTML = '<p class="note">' + esc2(j.message) + '</p>'; track('lookup_result', { carrier, tier: 'unsupported_airline' }); return; }
      if (!j.legs.length) { out.innerHTML = '<p class="note">No flight found for that number and date.</p>'; track('lookup_result', { carrier, tier: 'not_found' }); return; }
      out.innerHTML = j.legs.map(l => {
        const m = l.match;
        const cands = m.candidates.map(c => '<li><a href="' + esc2(c.path) + '">' + esc2(c.label) + '</a>' +
          ' · <a class="luckylink" href="' + esc2(c.path) + '#lucky">lucky seat</a>' +
          (m.tier === 'exact' ? '' : (c.probability >= 0.995 && !m.uncovered_share ? ' — the only version we know of' : ' — about ' + Math.round(c.probability * 100) + '%')) + '</li>').join('') +
          (m.uncovered_share ? '<li>Other versions we haven\'t mapped yet — about ' + Math.round(m.uncovered_share * 100) + '%</li>' : '');
        return '<div class="result"><p class="result-head"><strong>' + esc2(j.query.flight.replace(/^([A-Z0-9]{2})(\d)/, '$1 $2')) + '</strong> ' +
          esc2(l.origin || '?') + ' → ' + esc2(l.destination || '?') + (l.departure_local ? ' · ' + esc2(l.departure_local) : '') + '</p>' +
          '<p>' + esc2((l.operator && l.operator.name) || '') + ' · ' + esc2(l.aircraft_model || 'aircraft not published yet') +
          (l.registration ? ' · ' + esc2(l.registration) : '') + '</p>' +
          '<p><span class="badge tier-' + esc2(m.tier) + '">' + esc2(TIER[m.tier] || m.tier) + '</span> ' + esc2(m.reason) + '</p>' +
          (cands ? '<ul>' + cands + '</ul>' : '') + '</div>';
      }).join('') + (j.page ? '<p><a href="' + esc2(j.page) + '">Page for this flight, with the aircraft seen on earlier days →</a></p>' : '') + '<p class="muted small">Flight data: AeroDataBox. Cabin versions can change on the day; the airline\'s seat map at check-in is final.</p>';
      track('lookup_result', { carrier, tier: j.legs[0].match.tier });
    });
  }

  // 5. Seat inspector
  const dataEl = document.getElementById('seatdata');
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const panel = document.getElementById('panel');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const LABEL = { outermost_seat_window_seat_absent: 'outermost seat in this row (no window seat beside it)',
    row_ahead_of_exit_row: 'row in front of an exit row', no_window: 'no window', misaligned_window: 'window offset',
    window_disputed: 'window disputed by sources', no_recline: 'no recline', lavatory_behind: 'lavatory behind',  first_row: 'first row', last_row: 'last row',
    mid_aft_exit: 'rear-cabin exit', overwing: 'over-wing exit', row_specific: 'row-specific', cabin_default: 'cabin default' };
  const words = s => esc(LABEL[s] || String(s).replace(/_/g, ' '));
  track('seatmap_view', { config: data.config });
  let current = null;
  const pick = (g) => {
    const seat = g.dataset.seat, f = data.seats[seat];
    if (!f) return;
    if (current) { current.querySelector('rect').classList.remove('sel'); current.querySelector('text').classList.remove('sel'); }
    g.querySelector('rect').classList.add('sel'); g.querySelector('text').classList.add('sel'); current = g;
    const side = f.sd === 'L' ? 'left' : 'right';
    const multiCabin = new Set(Object.values(data.seats).map(x => x.c)).size > 1;
    const secRaw = (data.sections || {})[f.z] || { rows: ['?', '?'], band: 'higher', band_label: '' };
    const sec = { ...secRaw, name: f.z.charAt(0).toUpperCase() + f.z.slice(1) };
    const exits = f.e.map(e => '<li>' + esc(e.id) + ' (' + words(e.k) + '): ' +
      (e.d === 'same_row' ? 'this row' : words(e.d) + ', ' + e.r + ' row' + (e.r === 1 ? '' : 's') + ' between') + '</li>').join('');
    panel.innerHTML = '<h2>Seat ' + esc(seat) + '</h2>' +
      '<dl>' + (multiCabin ? '<dt>Cabin</dt><dd>' + esc(f.c) + '</dd>' : '') + '<dt>Position</dt><dd>' + esc(f.p) + ', ' + side + ' side</dd>' +
      '<dt>Seats to aisle</dt><dd>' + f.a + '</dd>' +
      '<dt title="Distance from this seat to the same point on the seat in front">Legroom (pitch)</dt><dd>' + (f.ps === 'range' ? esc(data.pitchRange) : (f.pi === null ? 'unknown' : f.pi + ' in')) + ' <span class="muted small">(' + words(f.ps) + ')</span></dd>' +
      '<dt>Exit row</dt><dd>' + (f.x ? 'yes' : 'no') + '</dd>' +
      '<dt>Section</dt><dd>' + esc(sec.name) + ' (rows ' + sec.rows[0] + '–' + sec.rows[1] + ')</dd>' +
      '<dt>Crash studies</dt><dd><span class="sw sw-' + esc(sec.band) + '"></span>' + esc(sec.band_label) + ' <a href="/research/">why</a></dd>' +
      '<dt>Exit within 5 rows</dt><dd>' + (f.ne ? 'yes' : 'no') + '</dd>' +
      '<dt>Notes</dt><dd>' + (f.f.length ? f.f.map(words).join(', ') : '—') + '</dd></dl>' +
      '<h3>Nearest exits</h3><ul>' + exits + '</ul>' +
      '<p class="note">The section pattern comes from two small studies of past US crashes. It isn\'t a rating of this seat or a prediction for your flight.</p>';
    track('seat_click', { config: data.config, seat, position: f.p, exit_row: f.x });
  };
  const wrap = document.getElementById('mapwrap');
  document.querySelectorAll('.viewbtn').forEach(b => b.addEventListener('click', () => {
    const v = b.dataset.view;
    document.querySelectorAll('.viewbtn').forEach(x => { const on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on); });
    document.querySelectorAll('.legend').forEach(l => { l.hidden = l.dataset.for !== v; });
    if (wrap) wrap.className = 'mapwrap view-' + v;
    track('map_view', { config: data.config, view: v });
  }));
  document.querySelectorAll('g[data-seat]').forEach(g => {
    g.addEventListener('click', () => pick(g));
    g.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(g); } });
  });
})();
"""

LUCKY_JS = r"""(() => {
  // Entertainment only. An astrology sign's element picks a section; numerology picks the seat.
  // Never describes a seat in terms of hazards or outcomes; birth details never leave the browser.
  const form = document.getElementById('luckyform');
  const dataEl = document.getElementById('seatdata');
  if (!form || !dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const out = document.getElementById('luckyout');
  const track = window.aeroTrack || (() => {});
  const Astro = window.Astro;
  const val = (id) => (document.getElementById(id) || {}).value || '';
  const SIGNS = [ // [name, element, start month, start day]
    ['Capricorn', 'Earth', 12, 22], ['Aquarius', 'Air', 1, 20], ['Pisces', 'Water', 2, 19], ['Aries', 'Fire', 3, 21],
    ['Taurus', 'Earth', 4, 20], ['Gemini', 'Air', 5, 21], ['Cancer', 'Water', 6, 21], ['Leo', 'Fire', 7, 23],
    ['Virgo', 'Earth', 8, 23], ['Libra', 'Air', 9, 23], ['Scorpio', 'Water', 10, 23], ['Sagittarius', 'Fire', 11, 22]];
  const sunSign = (m, d) => {
    let sign = SIGNS[0];
    for (const s of SIGNS.slice(1)) if (m > s[2] || (m === s[2] && d >= s[3])) sign = s;
    if (m === 12 && d >= 22) sign = SIGNS[0];
    return { name: sign[0], element: sign[1] };
  };
  const SECTION_OF = { Fire: 'front', Wood: 'front', Air: 'middle', Metal: 'middle', Earth: 'rear', Water: 'rear' };
  const digits = (s) => String(s).replace(/\D/g, '').split('').reduce((a, c) => a + Number(c), 0);
  const reduce = (n) => { while (n > 9) n = digits(n); return n; };
  const hash = (s) => { let h = 2166136261; for (const c of s) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const niceDate = (iso) => new Date(iso + 'T12:00:00Z').toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short', timeZone: 'UTC' });

  // Each system returns: the sign shown, its element, which side it favours, the lucky number and notes.
  function reading(sys, dob, tob, tz) {
    const [y, m, d] = dob.split('-').map(Number);
    const lifePath = reduce(digits(dob));
    if (sys === 'vedic' && Astro) {
      const v = Astro.vedicMoon(dob, tob, tz);
      const notes = [];
      if (!v.exact && (v.rashiChanges || v.nakshatraChanges)) {
        const ch = v.rashiChanges ? 'from ' + v.rashiChanges.join(' into ') : 'from ' + v.nakshatraChanges.join(' into ') + ' nakshatra';
        notes.push('The moon moved ' + ch + ' on your birth date, so we used its noon position. Add your birth time to be sure.');
      } else if (!v.exact) notes.push('Your moon sign and nakshatra are the same all day on your birth date, so birth time doesn\'t change them.');
      return { key: v.rashi, title: 'Moon sign ' + v.rashi + ' (' + v.western + ') · ' + v.nakshatra + ' nakshatra, pada ' + v.pada,
               element: v.element, side: (v.element === 'Fire' || v.element === 'Air') ? 'L' : 'R',
               number: v.number, numberWhy: v.nakshatra + ' is ruled by ' + v.lord + ', whose number is ' + v.number,
               notes: notes.concat(['Sidereal zodiac with the Lahiri ayanamsa, the one most Indian almanacs use.']) };
    }
    if (sys === 'chinese' && Astro) {
      const z = Astro.chineseZodiac(dob);
      const notes = z.beforeNewYear ? ['You were born before Lunar New Year (' + z.lunarNewYear + '), so your sign belongs to ' + z.year + '.'] : [];
      return { key: z.element + ' ' + z.animal, title: z.element + ' ' + z.animal + ' (' + (z.yang ? 'yang' : 'yin') + ')',
               element: z.element, side: z.yang ? 'L' : 'R', number: lifePath,
               numberWhy: 'your life-path number is ' + lifePath, notes };
    }
    const sign = sunSign(m, d);
    return { key: sign.name, title: 'Sun sign ' + sign.name, element: sign.element,
             side: (sign.element === 'Fire' || sign.element === 'Air') ? 'L' : 'R', number: lifePath,
             numberWhy: 'your life-path number is ' + lifePath, notes: [] };
  }

  function rahuHtml(fdate, ftime) {
    if (!fdate || !Astro) return '';
    const r = Astro.rahuKalam(fdate, ftime);
    let line = '<p><strong>Rahu Kalam</strong> on ' + esc(niceDate(fdate)) + ': about ' + r.start + ' to ' + r.end + '.';
    if (r.inside === true) line += ' Your ' + esc(ftime) + ' departure falls inside it. Those who follow it usually step out of home before it begins.';
    else if (r.inside === false) line += ' Your ' + esc(ftime) + ' departure is outside it.';
    else line += ' Add your departure time to check it.';
    return line + '</p><p class="muted small">Standard times for a 6 am to 6 pm day. Almanacs shift them with local sunrise, often by up to an hour. Tradition only.</p>';
  }

  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const dob = val('dob'), pref = val('pref') || 'any', sys = val('sys') || 'vedic';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dob)) { out.innerHTML = '<p class="note">Enter your date of birth.</p>'; return; }
    const r = reading(sys, dob, val('tob'), Number(val('tz') || 330));
    const sideName = r.side === 'L' ? 'left' : 'right';
    const luckySection = SECTION_OF[r.element];
    const byType = Object.entries(data.seats).filter(([, f]) => pref === 'any' || f.p === pref);
    const inSection = byType.filter(([, f]) => f.z === luckySection);
    const pool = inSection.length ? inSection : byType;
    const secRows = (data.sections && data.sections[luckySection] && data.sections[luckySection].rows) || ['?', '?'];
    const scored = pool.map(([seat, f]) => {
      const row = parseInt(seat, 10), letter = seat.replace(/\d+/g, '');
      const seatNum = reduce(digits(row) + (letter.charCodeAt(0) - 64)), rowNum = reduce(row);
      let score = 0; const why = [];
      if (seatNum === r.number) { score += 3; why.push(seat + ' adds up to ' + seatNum + ': ' + r.numberWhy); }
      if (rowNum === r.number) { score += 2; why.push('row ' + row + ' reduces to ' + rowNum); }
      if (f.sd === r.side) { score += 1; why.push(r.element.toLowerCase() + ' signs pair with the ' + sideName + ' side'); }
      if (f.z === luckySection) why.unshift('in your lucky ' + luckySection + ' section');
      return { seat, score, why, tie: hash(dob + sys + data.config + seat) };
    }).sort((a, b) => b.score - a.score || a.tie - b.tie);
    if (!scored.length) { out.innerHTML = '<p class="note">No seats of that type on this aircraft.</p>'; return; }
    const [top, ...rest] = scored;
    document.querySelectorAll('.seat.lucky').forEach(el => el.classList.remove('lucky'));
    const g = document.querySelector('g[data-seat="' + top.seat + '"]');
    if (g) { g.querySelector('rect').classList.add('lucky'); g.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    out.innerHTML = '<p class="lucky-pick">Your lucky seat: ' + esc(top.seat) + '</p>' +
      '<p>' + esc(r.title) + ' · lucky number ' + r.number + '</p>' +
      '<p>Lucky section: <strong>' + cap(luckySection) + '</strong> (rows ' + secRows[0] + '–' + secRows[1] + '), the ' +
      r.element.toLowerCase() + ' section</p>' +
      '<ul>' + top.why.map(w => '<li>' + esc(w) + '</li>').join('') + '</ul>' +
      r.notes.map(n => '<p class="muted small">' + esc(n) + '</p>').join('') +
      rahuHtml(val('fdate'), val('ftime')) +
      '<p class="muted small">Also lucky for you: ' + rest.slice(0, 2).map(x => esc(x.seat)).join(', ') + '.</p>' +
      '<p class="muted small">For fun only. It isn\'t a rating of any kind; use the facts above to choose a seat.</p>';
    const pageUrl = location.origin + location.pathname;
    const msg = 'My lucky seat on the ' + document.querySelector('h1').textContent.replace(/ seat map$/, '') + ' is ' + top.seat +
      ' (' + r.key + ', ' + luckySection + ' section) on MyLuckySeat. Find yours: ' + pageUrl + '#lucky';
    out.insertAdjacentHTML('beforeend', '<div class="sharebar">' +
      (navigator.share ? '<button type="button" class="share-native">Share</button>' : '') +
      '<a class="share-wa" target="_blank" rel="noopener" href="https://wa.me/?text=' + encodeURIComponent(msg) + '">Share on WhatsApp</a></div>');
    const nb = out.querySelector('.share-native');
    if (nb) nb.addEventListener('click', () => { navigator.share({ title: 'My lucky seat', text: msg, url: pageUrl + '#lucky' }).catch(() => {}); track('lucky_share', { via: 'native' }); });
    out.querySelector('.share-wa').addEventListener('click', () => track('lucky_share', { via: 'whatsapp' }));
    track('lucky_seat', { config: data.config, system: sys, sign: r.key, pref, rahu: Boolean(val('fdate')) });
  });

  // Arriving from the home-page shortcut: #lucky=YYYY-MM-DD&pref=..&sys=..&tob=..&tz=..&fd=..&ft=..
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (location.hash.startsWith('#lucky')) {
    const sec = document.getElementById('lucky');
    if (h.get('lucky')) {
      const set = (id, v) => { const el = document.getElementById(id); if (el && v) el.value = v; };
      set('dob', h.get('lucky')); set('pref', h.get('pref')); set('sys', h.get('sys'));
      set('tob', h.get('tob')); set('tz', h.get('tz')); set('fdate', h.get('fd')); set('ftime', h.get('ft'));
      if (h.get('tob') || h.get('fd')) { const det = form.querySelector('details'); if (det) det.open = true; }
      history.replaceState(null, '', location.pathname + '#lucky');   // drop birth details from the address bar
      form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit'));
    }
    if (sec) setTimeout(() => sec.scrollIntoView({ block: 'start' }), 50);
  }
})();
"""

SITE_CSS = """
:root{--bg:#e9f0fa;--card:#fff;--ink:#0d1a2e;--muted:#4f5d73;--line:#c5d2e6;--accent:#1d4ed8;--navy:#0b2545;--navy-ink:#fff;--yellow:#ffc72c;--yellow-ink:#0b2545;--fus:#f4f7fc;--seat:#dfe9fb;--exitrow:#ffd95a;--wing:#cfdaee;--sel:#1d4ed8;--exit:#d92d20;--fac:#d7deea;--warn:#7a4f00;--warnbg:#fff3cd;--ok:#146c43;--okbg:#d9f2e4;--lucky-bg:#ffe28a;--lucky-ink:#5c4300;--band-lower:#8fdcb6;--band-higher:#ffb58c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0a1120;--card:#121b2d;--ink:#e8edf6;--muted:#9aa6bb;--line:#26324a;--accent:#7aa8ff;--navy:#07101f;--navy-ink:#fff;--yellow:#ffc72c;--yellow-ink:#0b2545;--fus:#16213a;--seat:#22355a;--exitrow:#6b5410;--wing:#1f2c47;--sel:#5b8ff0;--exit:#f2705f;--fac:#2a3650;--warn:#ffd36b;--warnbg:#33290e;--ok:#7fd8a6;--okbg:#123224;--lucky-bg:#4a3a08;--lucky-ink:#ffd866;--band-lower:#1f6b49;--band-higher:#8a4526}}
:root[data-theme="dark"]{--bg:#0a1120;--card:#121b2d;--ink:#e8edf6;--muted:#9aa6bb;--line:#26324a;--accent:#7aa8ff;--navy:#07101f;--navy-ink:#fff;--yellow:#ffc72c;--yellow-ink:#0b2545;--fus:#16213a;--seat:#22355a;--exitrow:#6b5410;--wing:#1f2c47;--sel:#5b8ff0;--exit:#f2705f;--fac:#2a3650;--warn:#ffd36b;--warnbg:#33290e;--ok:#7fd8a6;--okbg:#123224;--lucky-bg:#4a3a08;--lucky-ink:#ffd866;--band-lower:#1f6b49;--band-higher:#8a4526}
--seat:#2d2c28;--exitrow:#1f3a28;--sel:#5b8ff0;--exit:#f07062;--fac:#3a3832;--warn:#f0c46b;--warnbg:#2e2614;--ok:#7fd69e;--okbg:#16301f;--lucky-bg:#2e2140;--lucky-ink:#cfa8ff;--band-lower:#1d3d39;--band-higher:#4a3220}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}a:hover{text-decoration-thickness:2px}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px}
.skip{position:absolute;left:-999px}.skip:focus{left:16px;top:8px;background:var(--card);padding:8px;z-index:9}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.top{background:var(--navy);color:var(--navy-ink)}
.top .wrap{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:56px;flex-wrap:wrap}
.brand{display:flex;gap:8px;align-items:center;font-weight:800;color:var(--navy-ink);text-decoration:none;letter-spacing:-.01em;font-size:18px}
nav{display:flex;flex-wrap:wrap;gap:4px 18px}nav a{color:var(--navy-ink);opacity:.88;text-decoration:none;font-size:15px}nav a:hover{opacity:1;color:var(--yellow)}
main{padding:24px 16px 48px}
h1{font-size:clamp(26px,4.5vw,40px);line-height:1.15;letter-spacing:-.02em;margin:8px 0 12px}
h2{font-size:22px;letter-spacing:-.01em;margin:36px 0 12px}h3{font-size:17px;margin:20px 0 8px}
.lede{font-size:18px;max-width:68ch}.muted{color:var(--muted)}.small{font-size:14px}
.crumbs{font-size:14px;color:var(--muted);display:block}.crumbs a{color:var(--muted);margin:0}
.badge{display:inline-block;font-size:13px;font-weight:600;padding:3px 10px;border-radius:99px;background:var(--warnbg);color:var(--warn);margin-right:8px}
.badge-verified{background:var(--okbg);color:var(--ok)}
.hero{padding:28px 0 8px}.hero h1 span{background:linear-gradient(transparent 62%,var(--yellow) 62%);padding:0 2px}
.searchrow{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:12px}
input[type=search]{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);min-width:0;width:min(100%,320px)}
.cards{list-style:none;padding:0;margin:12px 0 0;display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}
.card a{display:flex;flex-direction:column;gap:2px;padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--card);color:var(--ink);text-decoration:none;height:100%}
.card a:hover{border-color:var(--accent);box-shadow:0 4px 14px rgba(15,27,45,.08)}.card a{border-left:4px solid var(--yellow)}.card .op{font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.card .ac{font-size:19px;font-weight:650}
.three{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-top:32px}
.three>div{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}.three h3{margin-top:0}
.grid{display:grid;gap:16px;grid-template-columns:minmax(0,1fr);margin-top:16px}
@media(min-width:820px){.grid{grid-template-columns:minmax(0,500px) minmax(0,1fr)}.panel{position:sticky;top:16px;align-self:start}}
.mapcard,.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px}
.mapcard{max-height:82vh;overflow:auto;background:linear-gradient(180deg,var(--bg),var(--card))}.mapcard svg{display:block;width:100%;max-width:440px;margin:0 auto}
.panel{padding:16px}.panel h2{margin-top:0}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:8px 0}dt{color:var(--muted)}dd{margin:0}
.note{font-size:14px;border-left:3px solid var(--warn);background:var(--warnbg);padding:8px 10px;border-radius:6px}
table{border-collapse:collapse;width:100%;background:var(--card);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
.facts th{width:34%;color:var(--muted);font-weight:500}
.rowtable{font-size:14px}.rowtable tr.exit-row td,.rowtable tr.exit-row th{background:var(--exitrow)}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin:8px 0}
details>summary{cursor:pointer;font-weight:600}details.rows{padding:0;overflow-x:auto}details.rows>summary{padding:12px 14px}
.prose{max-width:70ch}.prose li{margin:4px 0}code{font-size:.92em;background:var(--fus);padding:1px 5px;border-radius:5px}
.inline-form{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin:12px 0}
.inline-form label{display:flex;flex-direction:column;gap:4px;font-size:14px;color:var(--muted);min-width:0}
.inline-form input,.inline-form select{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 11px;min-width:0;max-width:100%}
.inline-form button{font:inherit;font-weight:700;color:#fff;background:var(--accent);border:0;border-radius:10px;padding:10px 16px;cursor:pointer}
.inline-form button:disabled,.inline-form input:disabled{opacity:.55;cursor:not-allowed}
.lookup,.lucky{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:24px}
.lookup h2,.lucky h2{margin-top:0}
.result.example{opacity:.85;border-top:1px dashed var(--line)}.result{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}.result p{margin:4px 0}.result-head{font-size:17px}
.badge.tier-exact{background:var(--okbg);color:var(--ok)}.badge.tier-unavailable{background:var(--fus);color:var(--muted)}
.badge-fun{background:var(--lucky-bg);color:var(--lucky-ink);vertical-align:middle}
.seat.lucky{fill:var(--lucky-bg);stroke:var(--lucky-ink);stroke-width:2}
.lucky-pick{font-size:20px;font-weight:700}
.viewbar{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px}
.viewbtn{font:inherit;font-size:14px;padding:6px 10px;border-radius:99px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
.viewbtn.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.legend{font-size:13px;color:var(--muted);margin:0 0 8px;display:block}.legend[hidden]{display:none}.legend p{margin:4px 0 0}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin:0 5px 0 8px;border:1px solid var(--line)}
.sw:first-child{margin-left:0}td .sw,dd .sw{margin-left:0}
.sw-exit{background:var(--exitrow)}.sw-door{background:var(--exit);border-color:var(--exit)}
.sw-lower{background:var(--band-lower)}.sw-higher{background:var(--band-higher)}.sw-near{background:var(--card);border:2px solid var(--accent)}
.view-pattern .seat.sec-rear{fill:var(--band-lower)}
.view-pattern .seat.sec-front,.view-pattern .seat.sec-middle{fill:var(--band-higher)}
.view-pattern .seat.exitrow{stroke:var(--line)}
.view-near .seat.exitrow{fill:var(--seat)}.view-near .seat.near-exit{stroke:var(--accent);stroke-width:2.2}
.view-pattern .seat.sel,.view-near .seat.sel{fill:var(--sel)}
.band{color:#fff;background:linear-gradient(180deg,rgba(6,21,47,.62),rgba(6,21,47,.30) 55%,rgba(6,21,47,.62)),url(/assets/band.jpg) center 40%/cover}
.band-home{background:linear-gradient(90deg,rgba(6,21,47,.78),rgba(6,21,47,.25) 75%),url(/assets/hero.jpg) center 45%/cover}
.band .wrap{padding:32px 16px 40px}.band-home .wrap{padding:64px 16px 128px}
.band h1{color:#fff;text-shadow:0 2px 18px rgba(0,0,0,.35);margin-top:6px}.band h1 span{color:var(--yellow);background:none}
.band-home h1{font-size:clamp(34px,6vw,60px);max-width:14ch}
.band .lede{color:rgba(255,255,255,.93);text-shadow:0 1px 10px rgba(0,0,0,.3)}.band .muted{color:rgba(255,255,255,.82)}
.band .crumbs,.band .crumbs a{color:rgba(255,255,255,.82)}
.band .badge{background:rgba(255,199,44,.95);color:var(--yellow-ink)}
.lift{margin-top:-96px;position:relative;z-index:2;box-shadow:0 18px 40px rgba(6,21,47,.18)}
.lookup .inline-form input{font-size:17px;padding:12px 14px}.lookup .inline-form button{font-size:17px;padding:13px 22px}
.mapcard,.panel,.lookup,.lucky,.aircraft,.three>div,.card a,table{box-shadow:0 4px 16px rgba(13,26,46,.06)}
.mapcard,.panel,.lookup,.lucky,.three>div{border-color:transparent}
.card a{border-color:transparent}
.chips-row{list-style:none;display:flex;flex-wrap:wrap;gap:8px;padding:0;margin:14px 0 4px}
.chips-row li{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.28);color:#fff;border-radius:99px;padding:5px 12px;font-size:14px;font-weight:600;backdrop-filter:blur(4px)}
.bandcta{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0 0}
.btn{display:inline-block;font-weight:700;text-decoration:none;border-radius:12px;padding:11px 18px;font-size:16px}
.btn-yellow{background:var(--yellow);color:var(--yellow-ink)}.btn-yellow:hover{filter:brightness(1.05)}
.btn-ghost{color:#fff;border:1.5px solid rgba(255,255,255,.6)}.btn-ghost:hover{background:rgba(255,255,255,.12)}
.luckylink{color:var(--lucky-ink);font-weight:600;font-size:14px}
.sharebar{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.sharebar a,.sharebar button{font:inherit;font-weight:700;font-size:14px;border-radius:10px;padding:8px 14px;border:0;cursor:pointer;text-decoration:none}
.share-wa{background:#25d366;color:#06281a}.share-native{background:var(--accent);color:#fff}
@media(max-width:700px){.mapcard{overflow-x:hidden}.mapcard svg{width:150%;max-width:none;margin-left:-25%}}
.tablewrap{overflow-x:auto}main a{overflow-wrap:anywhere}.cite{font-size:12px;margin-left:4px;color:var(--muted)}.aircraft{border-top:4px solid var(--navy);background:var(--card);border-radius:14px;padding:16px;margin-top:24px}.aircraft h2{margin-top:0}.suppliers td:first-child{font-weight:600}.sections td{white-space:normal}
.lucky .inline-form button{background:var(--yellow);color:var(--yellow-ink)}
.sponsor,.adslot{margin-top:24px;background:var(--card);border:1px dashed var(--line);border-radius:14px;padding:14px 16px}.sponsor-tag{margin:0 0 8px;font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}.sponsor-items{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}.sponsor-item{display:flex;flex-direction:column;gap:4px;padding:12px;border:1px solid var(--line);border-radius:10px;text-decoration:none;color:var(--ink)}.sponsor-item:hover{border-color:var(--accent)}.sponsor-cta{color:var(--accent);font-weight:600;font-size:14px}ins.adsbygoogle{display:block;min-height:90px}
.tablewrap{overflow-x:auto}.hist{min-width:560px}.hist a{overflow-wrap:normal}.hist td:last-child{min-width:13em}
.inline-form details.more{flex-basis:100%;width:100%}.inline-form details.more summary{cursor:pointer;color:var(--accent);font-weight:600;font-size:14px}.more-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:10px}.more-grid label{display:flex;flex-direction:column;gap:4px;font-size:14px}
.lucky{border-top:4px solid var(--yellow)}.lookup{border-top:4px solid var(--accent)}
.badge-fun{background:var(--lucky-bg);color:var(--lucky-ink)}
.linkbtn{font:inherit;color:inherit;background:none;border:0;padding:0;text-decoration:underline;cursor:pointer}
.fab{position:fixed;right:16px;bottom:16px;z-index:20;font:inherit;font-weight:700;font-size:15px;color:var(--yellow-ink);background:var(--yellow);border:0;border-radius:99px;padding:10px 16px;box-shadow:0 6px 18px rgba(11,37,69,.25);cursor:pointer}
dialog#feedback{border:0;border-radius:16px;padding:0;width:min(480px,calc(100vw - 32px));background:var(--card);color:var(--ink);box-shadow:0 20px 50px rgba(0,0,0,.3)}
dialog#feedback::backdrop{background:rgba(7,16,31,.55)}
.fbform{display:flex;flex-direction:column;gap:12px;padding:18px}
.fbhead{display:flex;justify-content:space-between;align-items:center}.fbhead h2{margin:0;font-size:20px}
.fbclose{font-size:26px;line-height:1;background:none;border:0;color:var(--muted);cursor:pointer;padding:0 4px}
.fbform label{display:flex;flex-direction:column;gap:4px;font-size:14px;color:var(--muted);min-width:0}.fbform textarea{resize:vertical}
.fbform input,.fbform select,.fbform textarea{font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:9px 11px;width:100%;min-width:0;max-width:100%}
.chips{border:0;padding:0;margin:0;display:flex;gap:8px;flex-wrap:wrap}.chips legend{font-size:14px;color:var(--muted);margin-bottom:6px;padding:0}
.chips label{flex-direction:row;align-items:center}.chips input{position:absolute;opacity:0;width:1px}
.chips span{display:inline-block;padding:7px 14px;border:1px solid var(--line);border-radius:99px;color:var(--ink);cursor:pointer}
.chips input:checked+span{background:var(--accent);border-color:var(--accent);color:#fff}.chips input:focus-visible+span{outline:2px solid var(--accent);outline-offset:2px}
.fbactions{display:flex;align-items:center;gap:12px}.fbactions button{font:inherit;font-weight:700;color:var(--yellow-ink);background:var(--yellow);border:0;border-radius:10px;padding:10px 18px;cursor:pointer}
.hp{position:absolute!important;left:-9999px}
main{padding-top:24px;padding-bottom:80px}
.foot{border-top:1px solid var(--line);padding:16px 0;font-size:14px;color:var(--muted)}.foot a{color:var(--muted)}
""" + SVG_CSS

FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#0b2545"/>'
           '<rect x="9" y="7" width="14" height="13" rx="4" fill="#ffc72c"/><rect x="7" y="21" width="18" height="4" rx="2" fill="#ffc72c"/></svg>')


# --------------------------------------------------------------------------- build

def build(out: str | Path, settings: Settings | None = None, configs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    s = settings or Settings.from_env()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    cfgs = configs if configs is not None else list_configs()
    cfgs = sorted(cfgs, key=lambda c: (c["operator"]["name"], c["aircraft"]["name"], -c["total_seats"]))

    pages = []
    for c in cfgs:
        cab = build_cabin(c)
        pages.append(Page(cab, slug_for(c), f'{c["operator"]["name"]} {c["aircraft"]["name"]} ({c["total_seats"]} seats)',
                          summarize(cab), variants_for(c, cfgs)))
    slugs = [p.slug for p in pages]
    if len(set(slugs)) != len(slugs):
        raise ValueError(f"Duplicate slugs: {slugs}")

    def write(rel: str, text: str) -> None:
        p = out / rel.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    sitemap = [("/", s.build_date)]
    md_paths = []
    for p in pages:
        updated = p.cfg["provenance"].get("updated_at") or p.cfg["provenance"].get("authored_at") or s.build_date
        write(p.path + "index.html", seatmap_page(s, p, updated))
        write(p.md_path, seatmap_md(s, p, updated))
        write(f"/api/v1/seatmaps/{p.cfg['id']}.json", json.dumps(api_record(s, p, updated, with_seats=True), indent=1))
        sitemap.append((p.path, updated)); md_paths.append(p.md_path)

    write("/index.html", index_page(s, pages))
    for path, md, title, desc in [
        ("/research/", research_md(s), "Where you sit and crash outcomes", "What crash studies found about front, middle and rear seats, how small they are, and why there is no safest seat."),
        ("/privacy/", privacy_md(s), "Privacy", "What we measure, with which cookieless tools, and what we never collect."),
    ]:
        write(path + "index.html", text_page(s, md, path, title, desc))
        write(path.rstrip("/") + ".md", strip_anchors(md))
        md_paths.append(path.rstrip("/") + ".md")
        sitemap.append((path, s.build_date))
    write("/404.html", shell(s, head(s, title=f"Not found — {s.name}", description="Page not found", path="/404.html"),
                             '<p>That page does not exist. <a href="/#seat-maps">Browse seat maps</a>.</p>', band="<h1>Not found</h1>")
          .replace('<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">',
                   '<meta name="robots" content="noindex">'))

    catalog = {"name": s.name, "generated": s.build_date, "license": s.data_license,
               "seat_maps": [api_record(s, p, p.cfg["provenance"].get("authored_at", s.build_date), with_seats=False) for p in pages]}
    write("/api/v1/index.json", json.dumps(catalog, indent=1))
    match_index = {
        "generated": s.build_date,
        "configs": [{"id": p.cfg["id"], "label": p.title, "path": p.path, "status": p.cfg["provenance"]["status"],
                     "operator": p.cfg["operator"], "group_icao": p.cfg.get("group_operators_icao", []),
                     "group_iata": p.cfg.get("group_operators_iata", []), "aircraft_icao": p.cfg["aircraft"]["icao"],
                     "share": p.cfg.get("fleet_share_prior", 1.0),
                     "unquantified": bool(p.cfg.get("other_versions_unquantified"))} for p in pages],
        "tails": {reg: t.config_id for reg, t in load_tail_overrides().items()},
    }
    write("/api/v1/match-index.json", json.dumps(match_index, indent=1))
    write("/openapi.json", json.dumps(openapi(s), indent=1))
    write("/.well-known/api-catalog", json.dumps({"linkset": [{
        "anchor": s.url + "/api/v1/",
        "service-desc": [{"href": s.url + "/openapi.json", "type": "application/openapi+json"}],
        "service-doc": [{"href": s.url + "/llms.txt", "type": "text/plain"}]}]}, indent=1))
    write("/llms.txt", llms_txt(s, pages))
    write("/llms-full.txt", "\n\n---\n\n".join(
        [llms_txt(s, pages)] + [seatmap_md(s, p, p.cfg["provenance"].get("authored_at", s.build_date)) for p in pages]
        + [strip_anchors(research_md(s))]))
    write("/robots.txt", robots_txt(s))
    write("/sitemap.xml", sitemap_xml(s, sitemap))
    write("/_headers", headers_file(s, md_paths))
    write("/assets/site.css", SITE_CSS)
    write("/assets/app.js", APP_JS.replace("__AI_REFERRERS__", json.dumps(AI_REFERRERS)))
    write("/assets/lucky.js", LUCKY_JS)
    write("/assets/astro.js", ASTRO_JS)
    write("/_tpl/flight.tpl", flight_template(s))
    if s.adsense_client:
        write("/ads.txt", f"google.com, {s.adsense_client.replace('ca-', '', 1)}, DIRECT, f08c47fec0942fa0\n")
    write("/assets/favicon.svg", FAVICON)
    if s.indexnow_key:
        write(f"/{s.indexnow_key}.txt", s.indexnow_key)
    if STATIC_DIR.exists():
        for f in STATIC_DIR.rglob("*"):
            if f.is_file():
                dest = out / "assets" / f.relative_to(STATIC_DIR)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

    warnings = []
    if "example.com" in s.url:
        warnings.append("SITE_URL is not set: canonical URLs, sitemap and robots.txt point at example.com.")
    if not s.contact_email:
        warnings.append("CONTACT_EMAIL is not set: no correction link is shown.")
    if not s.umami_website_id:
        warnings.append("UMAMI_WEBSITE_ID is not set: only Cloudflare page-view analytics will run (no custom events).")
    if not (STATIC_DIR / "og.jpg").exists():
        warnings.append("static/og.jpg missing: social previews will have no image.")
    return {"pages": len(sitemap), "seat_maps": len(pages), "out": str(out), "warnings": warnings,
            "urls": [s.url + p for p, _ in sitemap]}


def indexnow(site_dir: str | Path, settings: Settings | None = None) -> int:
    """Submit every sitemap URL to IndexNow (one request). Returns HTTP status."""
    s = settings or Settings.from_env()
    if not s.indexnow_key:
        raise RuntimeError("INDEXNOW_KEY is not set")
    sm = (Path(site_dir) / "sitemap.xml").read_text()
    urls = re.findall(r"<loc>(.*?)</loc>", sm)
    body = json.dumps({"host": s.host, "key": s.indexnow_key, "keyLocation": f"{s.url}/{s.indexnow_key}.txt",
                       "urlList": [html.unescape(u) for u in urls]}).encode()
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


# Content hash of the CSS/JS so every change gets a new URL (assets are cached for a day).
import hashlib as _hashlib
ASSET_VERSION = _hashlib.sha256((SITE_CSS + APP_JS + LUCKY_JS + ASTRO_JS).encode()).hexdigest()[:10]
