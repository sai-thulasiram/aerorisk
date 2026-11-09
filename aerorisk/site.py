"""Static site generator: crawlable seat-map pages + files for search engines and AI agents.

    python -m aerorisk site --out site

Everything a crawler or agent needs is in the HTML/Markdown/JSON itself; JavaScript only
adds the interactive seat inspector and analytics events.

Configuration (environment variables):
    SITE_URL            https://your-domain (required for production; canonical, sitemap, robots)
    SITE_NAME           default "AeroRisk"
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

from .layout import Cabin, build_cabin, list_configs
from .render import SVG_CSS, render_svg

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
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
    name: str = "AeroRisk"
    tagline: str = "Aircraft seat maps where every fact has a source"
    contact_email: str | None = None
    umami_website_id: str | None = None
    umami_src: str = "https://cloud.umami.is/script.js"
    cf_beacon_token: str | None = None
    allow_ai_training: bool = True
    data_license: str = "CC BY 4.0"
    indexnow_key: str | None = None
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
    return {
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
    out = [{"id": c["id"], "slug": slug_for(c), "label": f'{c["aircraft"]["name"]} · {c["total_seats"]} seats',
            "share": round(c.get("fleet_share_prior", 1.0) / tot, 2), "relation": "same_type",
            "current": c["id"] == cfg["id"]} for c in same]
    out += [{"id": c["id"], "slug": slug_for(c), "label": f'{c["aircraft"]["name"]} · {c["total_seats"]} seats',
             "share": None, "relation": "swap", "current": False} for c in swaps]
    return out


def faq(p: Page) -> list[tuple[str, str]]:
    f, cfg = p.facts, p.cfg
    name = f'{cfg["operator"]["name"]} {cfg["aircraft"]["name"]}'
    same = [v for v in p.variants if v["relation"] == "same_type"]
    q = []
    count = f'This configuration has {f["total_seats"]} seats in a {f["layout"]} layout.'
    if len(same) > 1:
        count += " " + cfg["operator"]["name"] + " operates " + _join(
            [f'{v["label"].split(" · ")[1]} (about {round(v["share"] * 100)}% of the fleet)' for v in same]) + \
            " versions; the aircraft registration tells you which one you will get."
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
    q.append((f"Which seat is safest on the {name}?",
              "No validated dataset supports a per-seat survival probability, so we do not rank seats by safety. "
              "We show verifiable facts instead: exit rows and how many rows each seat is from the nearest exits."))
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
        f'<meta property="og:image" content="{_e(s.url)}/assets/og.png">',
        '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="theme-color" content="#f7f6f2" media="(prefers-color-scheme: light)">',
        '<meta name="theme-color" content="#141413" media="(prefers-color-scheme: dark)">',
        '<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">',
        '<link rel="stylesheet" href="/assets/site.css">',
        f'<link rel="alternate" type="text/plain" title="LLM summary" href="/llms.txt">',
    ]
    if md_path:
        parts.append(f'<link rel="alternate" type="text/markdown" href="{_e(md_path)}">')
    for block in jsonld or []:
        parts.append('<script type="application/ld+json">' +
                     json.dumps(block, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    if s.umami_website_id:
        parts.append(f'<script defer src="{_e(s.umami_src)}" data-website-id="{_e(s.umami_website_id)}" '
                     f'data-domains="{_e(s.host)}"></script>')
    if s.cf_beacon_token:
        parts.append('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
                     f'data-cf-beacon=\'{{"token":"{_e(s.cf_beacon_token)}"}}\'></script>')
    parts.append('<script defer src="/assets/app.js"></script>')
    return "\n".join(parts)


def shell(s: Settings, head_html: str, body: str) -> str:
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
<nav><a href="/#seat-maps">Seat maps</a><a href="/methodology/">Methodology</a></nav>
</div></header>
<main id="main" class="wrap">
{body}
</main>
<footer class="foot"><div class="wrap">
<p>{_e(s.name)} publishes layout facts, not safety ratings. Data: {_e(s.data_license)}{contact}.</p>
<p><a href="/methodology/">Methodology</a> · <a href="/privacy/">Privacy</a> · <a href="/api/v1/index.json">JSON API</a> · <a href="/llms.txt">llms.txt</a></p>
</div></footer>
</body>
</html>
"""


def status_badge(cfg: dict[str, Any]) -> str:
    st = cfg["provenance"]["status"]
    label = {"draft_unverified": "Draft · not yet checked against the airline's live seat map",
             "verified": "Verified against the airline's live seat map"}.get(st, st.replace("_", " "))
    return f'<span class="badge badge-{_e(st)}">{_e(label)}</span>'


def seat_payload(cabin: Cabin) -> dict[str, Any]:
    return {s.seat: {"p": s.position, "sd": s.side, "a": s.seats_to_aisle, "pi": s.pitch_in, "ps": s.pitch_source,
                     "x": s.in_exit_row, "f": s.flags,
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
        ("Layout", f'{f["layout"]} ({" ".join(cfg["letters"])})'),
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
    pitch = "; ".join(f["pitch_specific"] + ([f'other rows: {f["pitch_default"]:g} in'] if f["pitch_default"] else []))
    if f["pitch_unknown_rows"]:
        pitch += f'; row{"s" if len(f["pitch_unknown_rows"]) > 1 else ""} {_ranges(f["pitch_unknown_rows"])}: unknown'
    rows.append(("Seat pitch (approx.)", _e(pitch)))
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
        pv = "unknown" if seat0 is None or seat0.pitch_in is None else f"{seat0.pitch_in:g} in"
        row_html.append(f'<tr{" class=exit-row" if r in exit_by_row else ""}><th scope=row>{r}</th>'
                        f'<td>{"".join(present)}</td><td>{pv}</td><td>{_e("; ".join(notes))}</td></tr>')

    # variants
    var_html = ""
    same = [v for v in p.variants if v["relation"] == "same_type"]
    swaps = [v for v in p.variants if v["relation"] == "swap"]
    if len(same) > 1 or swaps:
        items = []
        for v in same:
            share = f' — about {round(v["share"] * 100)}% of this fleet' if v["share"] is not None else ""
            if v["current"]:
                items.append(f'<li><strong>{_e(v["label"])}</strong> (this page){share}</li>')
            else:
                items.append(f'<li><a href="/seat-maps/{_e(v["slug"])}/">{_e(v["label"])}</a>{share}</li>')
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
    faq_html = "".join(f"<details><summary>{_e(q)}</summary><p>{_e(a)}</p></details>" for q, a in faq(p))

    svg = render_svg(p.cabin, title=False, inline_style=False)
    payload = json.dumps({"config": cfg["id"], "seats": seat_payload(p.cabin)},
                         separators=(",", ":")).replace("</", "<\\/")

    body = f"""
<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a> › <a href="/#seat-maps">Seat maps</a> › {_e(cfg["operator"]["name"])}</nav>
<h1>{_e(p.title)} seat map</h1>
<p class="lede">{_e(lede(p))}</p>
<p>{status_badge(cfg)} <span class="muted">Updated {_e(updated)}</span></p>
<div class="grid">
  <section class="mapcard" aria-label="Interactive seat map">
    <p class="muted small">Tap a seat. Exit rows are shaded; red marks are exits.</p>
    {svg}
  </section>
  <aside class="panel" id="panel" aria-live="polite">
    <h2>Seat details</h2>
    <p class="muted">Select a seat on the map to see its position, pitch, exit row status and distance to the nearest exits.</p>
  </aside>
</div>
<section><h2>Key facts</h2><table class="facts">{facts_html}</table></section>
{var_html}
<section><h2>Row by row</h2>
<details class="rows"><summary>Show all {f["row_count"]} rows</summary>
<table class="rowtable"><thead><tr><th>Row</th><th>Seats</th><th>Pitch</th><th>Notes</th></tr></thead>
<tbody>{"".join(row_html)}</tbody></table></details></section>
<section><h2>Seat safety</h2><p>No validated occupant-level dataset supports a survival probability for individual seats, so we don't publish one. Distance to exits is shown as a layout fact, not a safety rating. <a href="/methodology/#no-scores">Why</a></p></section>
<section><h2>Questions</h2>{faq_html}</section>
<section><h2>Sources</h2>
<p class="muted">{_e(prov["method"])}. First-party check: {_e(prov.get("primary_source_check", "not done"))}.</p>
<ul>{src_html}</ul>
<h3>How each fact is corroborated</h3><ul>{corr_html}</ul>
{f"<h3>Where sources disagree</h3><ul>{conf_html}</ul>" if conf_html else ""}
<p class="small"><a href="/api/v1/seatmaps/{_e(cfg["id"])}.json">JSON</a> · <a href="{_e(p.md_path)}">Markdown</a></p>
</section>
<script type="application/json" id="seatdata">{payload}</script>
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
    faqld = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq(p)]}
    return shell(s, head(s, title=title, description=desc, path=p.path, jsonld=[crumbs, dataset, faqld],
                         md_path=p.md_path, og_type="article"), body)


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
<section class="hero">
<h1>{_e(s.tagline)}</h1>
<p class="lede">Row-by-row aircraft seat maps showing exit rows, seats that don't exist, windowless seats and pitch. Every fact lists its sources and how well it has been checked. Where we don't know, we say so.</p>
</section>
<section id="seat-maps">
<div class="searchrow"><h2>Seat maps</h2>
<label class="sr" for="q">Filter by airline or aircraft</label>
<input id="q" type="search" placeholder="Filter: IndiGo, 737, A320neo…" autocomplete="off"></div>
<ul class="cards" id="cards">{"".join(cards)}</ul>
<p class="muted small" id="noresults" hidden>No seat map matches that yet.</p>
</section>
<section class="three">
<div><h3>Sourced, fact by fact</h3><p>Each layout records which sources back each fact and where they disagree.</p></div>
<div><h3>Honest about gaps</h3><p>Unknown pitch is shown as unknown. Drafts are labelled as drafts until checked against the airline's own seat map.</p></div>
<div><h3>No invented safety scores</h3><p>We show exits and distances, not survival percentages that no dataset can support. <a href="/methodology/#no-scores">Why</a></p></div>
</section>
<section><h2>Coming next</h2><p>Look up a flight number and date to see which cabin version is likely, with the confidence stated.</p></section>
"""
    site_ld = {"@context": "https://schema.org", "@type": "WebSite", "name": s.name, "url": s.url + "/",
               "description": desc}
    org_ld = {"@context": "https://schema.org", "@type": "Organization", "name": s.name, "url": s.url + "/",
              "logo": s.url + "/assets/favicon.svg"}
    catalog = {"@context": "https://schema.org", "@type": "DataCatalog", "name": f"{s.name} seat maps",
               "url": s.url + "/", "dataset": [{"@type": "Dataset", "name": f"{p.title} seat map",
                                                "url": s.url + p.path} for p in pages]}
    return shell(s, head(s, title=f"{s.name} — {s.tagline}", description=desc, path="/",
                         jsonld=[site_ld, org_ld, catalog]), body)


METHODOLOGY_MD = """# Methodology

## What we publish
Cabin layout facts for specific airline and aircraft configurations: seat count, row numbering, which seats exist, exit rows, windowless seats, approximate seat pitch, and how many rows each seat is from the nearest exits.

## Where the data comes from
Each configuration is hand-authored from public sources such as airline fee and fleet pages and seat-map reference sites. It is not licensed airline layout (LOPA) data. Every configuration lists its sources, which sources back each fact, and where sources disagree.

## Verification status
- **Draft**: built from public sources, not yet checked against the airline's live seat map.
- **Verified**: checked against the airline's live seat map or the aircraft's safety card for at least one aircraft of that configuration.

## Which version will I get?
Airlines often fly more than one cabin version of the same aircraft type, and swap aircraft on a route. A flight number gives the aircraft type; only the registration identifies the exact cabin. We state this as a confidence level:
- **Exact**: registration matched to a verified configuration.
- **Likely**: airline and type known; versions ranked by their share of the fleet.
- **Unavailable**: type unknown or not covered yet.

## Why there are no seat safety scores {#no-scores}
Seat-level survival outcomes are recorded for only a small number of survivable accidents, and outcomes depend mostly on accident type, fire, smoke and evacuation conditions rather than seat number. That data cannot support a reliable survival probability for an individual seat, so we don't publish one. Exit distance is shown as a layout fact only.

## Corrections
If a seat map is wrong for your flight, tell us the airline, flight date, aircraft registration (shown on many airline apps and flight trackers) and what differs.

## API and licence {#api}
- Catalog: `/api/v1/index.json`
- One configuration with every seat: `/api/v1/seatmaps/{id}.json`
- OpenAPI description: `/openapi.json`
"""

PRIVACY_MD = """# Privacy

We measure how the site is used so we can improve it. We do not use cookies or advertising trackers, and we do not collect names, emails or flight bookings.

## What is measured
- Page views, referring site, country, device type and browser, aggregated.
- Product events: seat map viewed, seat selected (seat number only), search text typed in the seat-map filter, source link clicked, and whether a visit came from an AI assistant such as ChatGPT or Perplexity.

## Tools
- Cloudflare Web Analytics (cookieless, aggregated).
- Umami (cookieless, aggregated), if enabled.

## Contact
Questions about this policy: see the contact link in the footer.
"""


def md_to_html(md: str) -> str:
    """Tiny Markdown subset: headings (with optional {#id}), paragraphs, bullet lists, **bold**, `code`."""
    out, in_list = [], False

    def inline(t: str) -> str:
        t = _e(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        return re.sub(r"`(.+?)`", r"<code>\1</code>", t)

    for block in md.strip().split("\n"):
        line = block.rstrip()
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
    return "\n".join(out)


def strip_anchors(md: str) -> str:
    return re.sub(r" \{#[\w-]+\}", "", md)


def text_page(s: Settings, md: str, path: str, title: str, description: str) -> str:
    return shell(s, head(s, title=f"{title} — {s.name}", description=description, path=path,
                         md_path=path.rstrip("/") + ".md"), f'<article class="prose">{md_to_html(md)}</article>')


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
             f"- Layout: {f['layout']} ({' '.join(cfg['letters'])})",
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
    pitch = "; ".join(f["pitch_specific"] + ([f"other rows: {f['pitch_default']:g} in"] if f["pitch_default"] else []))
    if f["pitch_unknown_rows"]:
        pitch += f"; row{'s' if len(f['pitch_unknown_rows']) > 1 else ''} {_ranges(f['pitch_unknown_rows'])}: unknown"
    lines.append(f"- Seat pitch (approx., see sources): {pitch}")
    lines += ["- Seat safety: not estimated. No validated per-seat survival data exists; exit distance is a layout fact only.", ""]
    same = [v for v in p.variants if v["relation"] == "same_type"]
    swaps = [v for v in p.variants if v["relation"] == "swap"]
    if len(same) > 1 or swaps:
        lines += ["## Other versions", ""]
        for v in same:
            if not v["current"]:
                lines.append(f"- {v['label']} (about {round(v['share'] * 100)}% of this fleet): {s.url}/seat-maps/{v['slug']}.md")
        for v in swaps:
            lines.append(f"- {v['label']} (swapped onto the same routes): {s.url}/seat-maps/{v['slug']}.md")
        lines.append("")
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
             f"> {s.tagline}. Row-by-row airline seat maps (exit rows, seats that don't exist, windowless seats, "
             "approximate pitch), each fact with its sources and a verification status. "
             "We do not publish seat safety scores.", "",
             "Notes for agents:",
             "- Status matters: \"draft\" layouts are from public sources and not yet checked against the airline's live seat map. Say so when citing.",
             "- One airline + aircraft type can have several cabin versions; the aircraft registration decides which. Each page lists the versions and fleet shares.",
             f"- Licence: {s.data_license}. Please cite the canonical page URL.", "",
             "## Seat maps", ""]
    for p in pages:
        lines.append(f"- [{p.title}]({s.url}{p.md_path}): {p.facts['total_seats']} seats; {exits_sentence(p.facts)}")
    lines += ["", "## Data", "",
              f"- [Catalog (JSON)]({s.url}/api/v1/index.json): every configuration with summary facts",
              f"- [OpenAPI]({s.url}/openapi.json): static JSON endpoints",
              f"- [Full text]({s.url}/llms-full.txt): all seat maps in one Markdown file", "",
              "## Docs", "",
              f"- [Methodology]({s.url}/methodology.md): sources, verification status, confidence levels, why there are no safety scores", "",
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
              f"Sitemap: {s.url}/sitemap.xml"]
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
    if s.umami_website_id:
        origin = re.match(r"^(https?://[^/]+)", s.umami_src).group(1)
        script_src.append(origin)
        connect_src += [origin, "https://api-gateway.umami.dev"]
    csp = (f"default-src 'self'; script-src {' '.join(script_src)}; connect-src {' '.join(connect_src)}; "
           "img-src 'self' data:; style-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
           "form-action 'self'; object-src 'none'")
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
    ]
    for p in md_paths:
        blocks += [p, "  Content-Type: text/markdown; charset=utf-8", ""]
    return "\n".join(blocks)


APP_JS = r"""(() => {
  const track = (name, data) => {
    try { if (window.umami && typeof window.umami.track === 'function') window.umami.track(name, data || {}); } catch (_) {}
  };
  window.aeroTrack = track;

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

  // 4. Seat inspector
  const dataEl = document.getElementById('seatdata');
  if (!dataEl) return;
  const data = JSON.parse(dataEl.textContent);
  const panel = document.getElementById('panel');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const LABEL = { outermost_seat_window_seat_absent: 'outermost seat in this row (no window seat beside it)',
    row_ahead_of_exit_row: 'row in front of an exit row', no_window: 'no window', misaligned_window: 'window offset',
    window_disputed: 'window disputed by sources', no_recline: 'no recline', first_row: 'first row', last_row: 'last row',
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
    const exits = f.e.map(e => '<li>' + esc(e.id) + ' (' + words(e.k) + '): ' +
      (e.d === 'same_row' ? 'this row' : words(e.d) + ', ' + e.r + ' row' + (e.r === 1 ? '' : 's') + ' between') + '</li>').join('');
    panel.innerHTML = '<h2>Seat ' + esc(seat) + '</h2>' +
      '<dl><dt>Position</dt><dd>' + esc(f.p) + ', ' + side + ' side</dd>' +
      '<dt>Seats to aisle</dt><dd>' + f.a + '</dd>' +
      '<dt>Pitch</dt><dd>' + (f.pi === null ? 'unknown' : f.pi + ' in') + ' <span class="muted small">(' + words(f.ps) + ')</span></dd>' +
      '<dt>Exit row</dt><dd>' + (f.x ? 'yes' : 'no') + '</dd>' +
      '<dt>Notes</dt><dd>' + (f.f.length ? f.f.map(words).join(', ') : '—') + '</dd></dl>' +
      '<h3>Nearest exits</h3><ul>' + exits + '</ul>' +
      '<p class="note">Layout facts only. We don\'t rate seat safety: no validated per-seat survival data exists.</p>';
    track('seat_click', { config: data.config, seat, position: f.p, exit_row: f.x });
  };
  document.querySelectorAll('g[data-seat]').forEach(g => {
    g.addEventListener('click', () => pick(g));
    g.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(g); } });
  });
})();
"""

SITE_CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d1c1a;--muted:#6b675f;--line:#c9c4b8;--accent:#1f5fbf;--fus:#efece5;--seat:#fff;
--exitrow:#dcefe1;--sel:#1f5fbf;--exit:#c0392b;--fac:#dcd8cf;--warn:#7a4f00;--warnbg:#fff1d6;--ok:#1d6b3a;--okbg:#ddf3e4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141413;--card:#1e1d1b;--ink:#ecebe6;--muted:#a19d94;
--line:#4d4a43;--accent:#7aa7ff;--fus:#24231f;--seat:#2d2c28;--exitrow:#1f3a28;--sel:#5b8ff0;--exit:#f07062;--fac:#3a3832;
--warn:#f0c46b;--warnbg:#2e2614;--ok:#7fd69e;--okbg:#16301f}}
:root[data-theme="dark"]{--bg:#141413;--card:#1e1d1b;--ink:#ecebe6;--muted:#a19d94;--line:#4d4a43;--accent:#7aa7ff;--fus:#24231f;
--seat:#2d2c28;--exitrow:#1f3a28;--sel:#5b8ff0;--exit:#f07062;--fac:#3a3832;--warn:#f0c46b;--warnbg:#2e2614;--ok:#7fd69e;--okbg:#16301f}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}a:hover{text-decoration-thickness:2px}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px}
.skip{position:absolute;left:-999px}.skip:focus{left:16px;top:8px;background:var(--card);padding:8px;z-index:9}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.top{border-bottom:1px solid var(--line);background:var(--card)}
.top .wrap{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:56px;flex-wrap:wrap}
.brand{display:flex;gap:8px;align-items:center;font-weight:700;color:var(--ink);text-decoration:none;letter-spacing:-.01em}
nav a{margin-left:16px;color:var(--ink);text-decoration:none;font-size:15px}nav a:first-child{margin-left:0}
main{padding:24px 16px 48px}
h1{font-size:clamp(26px,4.5vw,40px);line-height:1.15;letter-spacing:-.02em;margin:8px 0 12px}
h2{font-size:22px;letter-spacing:-.01em;margin:36px 0 12px}h3{font-size:17px;margin:20px 0 8px}
.lede{font-size:18px;max-width:68ch}.muted{color:var(--muted)}.small{font-size:14px}
.crumbs{font-size:14px;color:var(--muted)}.crumbs a{color:var(--muted);margin:0}
.badge{display:inline-block;font-size:13px;font-weight:600;padding:3px 10px;border-radius:99px;background:var(--warnbg);color:var(--warn);margin-right:8px}
.badge-verified{background:var(--okbg);color:var(--ok)}
.hero{padding:24px 0 8px}
.searchrow{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:12px}
input[type=search]{font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);min-width:0;width:min(100%,320px)}
.cards{list-style:none;padding:0;margin:12px 0 0;display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}
.card a{display:flex;flex-direction:column;gap:2px;padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--card);color:var(--ink);text-decoration:none;height:100%}
.card a:hover{border-color:var(--accent)}.card .op{font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.card .ac{font-size:19px;font-weight:650}
.three{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-top:32px}
.three>div{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}.three h3{margin-top:0}
.grid{display:grid;gap:16px;grid-template-columns:minmax(0,1fr);margin-top:16px}
@media(min-width:820px){.grid{grid-template-columns:minmax(0,380px) minmax(0,1fr)}.panel{position:sticky;top:16px;align-self:start}}
.mapcard,.panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px}
.mapcard{max-height:78vh;overflow:auto}.mapcard svg{display:block;width:100%;max-width:340px;margin:0 auto}
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
.foot{border-top:1px solid var(--line);padding:16px 0;font-size:14px;color:var(--muted)}.foot a{color:var(--muted)}
""" + SVG_CSS

FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#1f5fbf"/>'
           '<rect x="9" y="7" width="14" height="13" rx="4" fill="#fff"/><rect x="7" y="21" width="18" height="4" rx="2" fill="#fff"/></svg>')


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
        ("/methodology/", METHODOLOGY_MD, "Methodology", "How seat maps are sourced and verified, confidence levels, and why we publish no seat safety scores."),
        ("/privacy/", PRIVACY_MD, "Privacy", "What we measure, with which cookieless tools, and what we never collect."),
    ]:
        write(path + "index.html", text_page(s, md, path, title, desc))
        write(path.rstrip("/") + ".md", strip_anchors(md))
        md_paths.append(path.rstrip("/") + ".md")
        sitemap.append((path, s.build_date))
    write("/404.html", shell(s, head(s, title=f"Not found — {s.name}", description="Page not found", path="/404.html"),
                             '<h1>Not found</h1><p>That page does not exist. <a href="/#seat-maps">Browse seat maps</a>.</p>')
          .replace('<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">',
                   '<meta name="robots" content="noindex">'))

    catalog = {"name": s.name, "generated": s.build_date, "license": s.data_license,
               "seat_maps": [api_record(s, p, p.cfg["provenance"].get("authored_at", s.build_date), with_seats=False) for p in pages]}
    write("/api/v1/index.json", json.dumps(catalog, indent=1))
    write("/openapi.json", json.dumps(openapi(s), indent=1))
    write("/.well-known/api-catalog", json.dumps({"linkset": [{
        "anchor": s.url + "/api/v1/",
        "service-desc": [{"href": s.url + "/openapi.json", "type": "application/openapi+json"}],
        "service-doc": [{"href": s.url + "/methodology/#api", "type": "text/html"}]}]}, indent=1))
    write("/llms.txt", llms_txt(s, pages))
    write("/llms-full.txt", "\n\n---\n\n".join(
        [llms_txt(s, pages)] + [seatmap_md(s, p, p.cfg["provenance"].get("authored_at", s.build_date)) for p in pages]
        + [strip_anchors(METHODOLOGY_MD)]))
    write("/robots.txt", robots_txt(s))
    write("/sitemap.xml", sitemap_xml(s, sitemap))
    write("/_headers", headers_file(s, md_paths))
    write("/assets/site.css", SITE_CSS)
    write("/assets/app.js", APP_JS.replace("__AI_REFERRERS__", json.dumps(AI_REFERRERS)))
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
    if not (STATIC_DIR / "og.png").exists():
        warnings.append("static/og.png missing: social previews will have no image.")
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
