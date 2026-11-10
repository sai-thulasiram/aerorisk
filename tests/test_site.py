import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

from aerorisk.layout import list_configs
from aerorisk.site import Settings, build, robots_txt

URL = "https://seatfacts.test"


class Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.h1 = 0; self.links = []; self.scripts = []; self.canonical = None; self.style_attrs = 0
        self._in_script = None; self.jsonld = []; self._buf = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "style" in a:
            self.style_attrs += 1
        if tag == "h1":
            self.h1 += 1
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "link" and a.get("rel") == "canonical":
            self.canonical = a["href"]
        if tag == "script":
            self.scripts.append(a); self._in_script = a.get("type"); self._buf = ""

    def handle_data(self, data):
        if self._in_script is not None:
            self._buf += data

    def handle_endtag(self, tag):
        if tag == "script":
            if self._in_script == "application/ld+json":
                self.jsonld.append(json.loads(self._buf))
            self._in_script = None


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    res = build(out, Settings(url=URL, umami_website_id="00000000-0000-0000-0000-000000000000",
                              indexnow_key="abc123def456", build_date="2026-11-09"))
    return out, res


def html_files(out):
    return [p for p in out.rglob("*.html")]


def test_expected_files(site):
    out, res = site
    for rel in ["index.html", "404.html", "robots.txt", "sitemap.xml", "llms.txt", "llms-full.txt", "_headers",
                "openapi.json", ".well-known/api-catalog", "api/v1/index.json", "assets/app.js", "assets/site.css",
                "assets/favicon.svg", "abc123def456.txt", "research/index.html", "research.md", "privacy.md"]:
        assert (out / rel).exists(), rel
    assert res["seat_maps"] == len(list_configs()) and not any("SITE_URL" in w for w in res["warnings"])


def test_every_page_is_well_formed_for_crawlers(site):
    out, _ = site
    for p in html_files(out):
        c = Collect(); c.feed(p.read_text())
        assert c.h1 == 1, p
        assert c.style_attrs == 0, f"inline style attribute breaks CSP: {p}"
        for s in c.scripts:   # CSP: no executable inline scripts
            assert s.get("src") or s.get("type") in ("application/ld+json", "application/json"), p
        if p.name != "404.html":
            assert c.canonical and c.canonical.startswith(URL), p
        for block in c.jsonld:
            assert block["@context"] == "https://schema.org"


def test_internal_links_resolve(site):
    out, _ = site
    for p in html_files(out):
        c = Collect(); c.feed(p.read_text())
        for href in c.links:
            if href.startswith(("http", "mailto:", "#")):
                continue
            path = href.split("#")[0]
            if not path:
                continue
            target = out / path.lstrip("/")
            assert target.exists() or (target / "index.html").exists() or target.with_suffix(".html").exists(), (p, href)


def test_sitemap_and_robots(site):
    out, _ = site
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [e.text for e in ET.parse(out / "sitemap.xml").getroot().findall("s:url/s:loc", ns)]
    assert len(locs) == len(list_configs()) + 3 and all(l.startswith(URL) for l in locs)
    for l in locs:
        rel = l[len(URL):].lstrip("/")
        assert (out / rel / "index.html").exists() or (out / "index.html").exists()
    robots = (out / "robots.txt").read_text()
    assert f"Sitemap: {URL}/sitemap.xml" in robots and "OAI-SearchBot" in robots and "Disallow: /" not in robots
    assert "Disallow: /" in robots_txt(Settings(url=URL, allow_ai_training=False))


def test_llms_txt_links_exist(site):
    out, _ = site
    txt = (out / "llms.txt").read_text()
    assert txt.startswith("# ") and "\n> " in txt
    for link in re.findall(r"\]\((https?://[^)]+)\)", txt):
        assert (out / link[len(URL):].lstrip("/")).exists(), link


def test_api_and_openapi(site):
    out, _ = site
    cat = json.loads((out / "api/v1/index.json").read_text())
    assert len(cat["seat_maps"]) == len(list_configs())
    for sm in cat["seat_maps"]:
        full = json.loads((out / f"api/v1/seatmaps/{sm['id']}.json").read_text())
        assert len(full["seats"]) == full["facts"]["total_seats"]
        assert full["seat_safety"]["estimable"] is False
    spec = json.loads((out / "openapi.json").read_text())
    assert spec["openapi"].startswith("3.1") and spec["servers"][0]["url"] == URL


def test_analytics_wiring(site):
    out, _ = site
    page = (out / "index.html").read_text()
    assert 'data-website-id="00000000-0000-0000-0000-000000000000"' in page and 'data-domains="seatfacts.test"' in page
    headers = (out / "_headers").read_text()
    assert "https://cloud.umami.is" in headers and "static.cloudflareinsights.com" in headers
    js = (out / "assets/app.js").read_text()
    for ev in ["ai_referral", "seat_click", "seatmap_view", "seatmap_filter"]:
        assert ev in js


def test_no_analytics_when_unconfigured(tmp_path):
    build(tmp_path, Settings(url=URL))
    assert "umami" not in (tmp_path / "index.html").read_text()


def test_seatmap_page_states_facts_in_html(site):
    out, _ = site
    page = (out / "seat-maps/ryanair-boeing-737-800-189-seats/index.html").read_text()
    assert "Over-wing exit rows 16 and 17" in page
    assert "no row 13" in page
    assert "Built from public seat guides" in page and "Draft" not in page.split("<main")[0]
    assert "%" not in re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S).split("Which version")[0]


def test_match_index_and_lucky_assets(site):
    out, _ = site
    mi = json.loads((out / "api/v1/match-index.json").read_text())
    assert {c["id"] for c in mi["configs"]} == {c["id"] for c in list_configs()}
    assert all(c["path"].startswith("/seat-maps/") for c in mi["configs"])
    for asset in ("assets/lucky.js", "assets/astro.js"):
        lucky = (out / asset).read_text().lower()
        for word in ("safe", "risk", "surviv", "danger"):
            assert word not in lucky, (asset, word)
    page = (out / "seat-maps/indigo-airbus-a321neo-232-seats/index.html").read_text()
    assert 'src="/assets/lucky.js?v=' in page and 'src="/assets/astro.js?v=' in page and "Just for fun" in page
    for fid in ('id="sys"', 'id="tob"', 'id="tz"', 'id="fdate"', 'id="ftime"', 'value="vedic"', 'value="chinese"'):
        assert fid in page, fid
    assert "not mapped yet" in page and "28–29 in" in page
    home = (out / "index.html").read_text()
    assert 'id="lookupform"' in home


def test_research_page_and_pattern_view(site):
    out, _ = site
    research = (out / "research/index.html").read_text()
    for needle in ("Popular Mechanics", "TIME (2015)", "Greenwich", "no safest seat", "37 crashes"):
        assert needle in research or needle in (out / "research.md").read_text(), needle
    page = (out / "seat-maps/ryanair-boeing-737-800-189-seats/index.html").read_text()
    assert 'data-view="pattern"' in page and "sec-rear" in page and "low confidence" in page
    assert "No seat is reliably safest" in page and "rows 23–33" in page
    assert not any(b.get("@type") == "FAQPage" for b in Collect_jsonld(page))
    assert "/api/v1/" not in page.split("</head>")[1] and "llms.txt" not in page.split("</head>")[1]
    api = json.loads((out / "api/v1/seatmaps/FR-B738-189Y.json").read_text())
    assert api["sections"]["rear"]["band"] == "lower" and api["seat_safety"]["estimable"] is False


def Collect_jsonld(html):
    c = Collect(); c.feed(html); return c.jsonld


def test_assets_are_cache_busted(site):
    out, _ = site
    for p in html_files(out):
        html = p.read_text()
        for asset in ("/assets/app.js", "/assets/site.css"):
            assert re.search(re.escape(asset) + r"\?v=[0-9a-f]{10}", html), (p, asset)


def test_aircraft_section(site):
    out, _ = site
    page = (out / "seat-maps/ryanair-boeing-737-max-8-200-197-seats/index.html").read_text()
    assert "About the Boeing 737 MAX 8" in page and "Renton" in page and "LEAP-1B" in page and "Turkish Aerospace" in page
    page = (out / "seat-maps/indigo-airbus-a320neo-186-seats/index.html").read_text()
    assert "PW1100G" in page and "MTU Aero Engines" in page and "Broughton" in page and "Engines on IndiGo" in page
    api = json.loads((out / "api/v1/seatmaps/U2-A20N-186Y.json").read_text())
    assert api["aircraft"]["manufacturer"]["name"] == "Airbus" and "LEAP-1A" in api["engine_on_this_fleet"]["text"]


def test_every_aircraft_fact_has_a_source():
    from pathlib import Path
    for f in (Path(__file__).resolve().parent.parent / "aircraft").glob("*.json"):
        a = json.loads(f.read_text())
        keys = [a.get("final_assembly_source")] + [m["source"] for m in a["milestones"]] + \
               [e["source"] for e in a["engine_options"]] + [c["source"] for c in a["components"]]
        assert all(k in a["sources"] for k in keys if k), f.name


def test_multi_cabin_page(site):
    out, _ = site
    page = (out / "seat-maps/air-india-airbus-a320neo-164-seats/index.html").read_text()
    assert "8 Business in rows 1–2, 2-2" in page and "Premium Economy" in page and "cab-business" in page
    assert "share unknown" in page
    ix = (out / "seat-maps/air-india-express-boeing-737-max-8-189-seats/index.html").read_text()
    assert "Rows &amp; exits only" in ix


def test_ads_off_by_default(site):
    out, _ = site
    assert not (out / "ads.txt").exists()
    home = (out / "index.html").read_text()
    assert "adsbygoogle" not in home and "Sponsored" not in home
    assert "unsafe-inline" not in (out / "_headers").read_text()


def test_adsense_and_sponsors_when_configured(tmp_path, monkeypatch):
    import aerorisk.site as site_mod
    sp = tmp_path / "sponsors.json"
    sp.write_text(json.dumps({"items": [
        {"id": "flights", "enabled": True, "title": "Compare fares", "url": "https://partner.example/x?aff=1", "where": ["home"]},
        {"id": "off", "enabled": False, "title": "Hidden", "url": "https://partner.example/y"},
        {"id": "nourl", "enabled": True, "title": "No link", "url": ""}]}))
    monkeypatch.setattr(site_mod, "SPONSORS_FILE", sp)
    out = tmp_path / "out"
    build(out, Settings(url=URL, adsense_client="ca-pub-1234567890123456", adsense_slot="9876543210"))
    assert (out / "ads.txt").read_text().strip() == "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0"
    home = (out / "index.html").read_text()
    assert 'data-ad-slot="9876543210"' in home and "adsbygoogle.js?client=ca-pub-1234567890123456" in home
    assert 'rel="sponsored noopener"' in home and "Compare fares" in home
    assert "Hidden" not in home and "No link" not in home
    seat = (out / "seat-maps/ryanair-boeing-737-800-189-seats/index.html").read_text()
    assert "Compare fares" not in seat          # placed on home only
    assert seat.index("adslot") > seat.index("Cabin sections and crash studies")   # never above the crash-study section
    headers = (out / "_headers").read_text()
    assert "pagead2.googlesyndication.com" in headers and "frame-src" in headers
    privacy = (out / "privacy/index.html").read_text()
    assert "Google AdSense" in privacy and "Sponsored" in privacy


def test_bad_adsense_client_is_rejected(monkeypatch):
    monkeypatch.setenv("ADSENSE_CLIENT", "pub-123")
    with pytest.raises(SystemExit):
        Settings.from_env()


def test_flight_page_template_and_sitemap_hint(site):
    out, _ = site
    tpl = (out / "_tpl/flight.tpl").read_text()
    for marker in ("__TITLE__", "__DESC__", "__SLUG__", "<!--BODY-->", "<!--BAND-->", "<!--JSONLD-->"):
        assert marker in tpl, marker
    assert "Sitemap: https://seatfacts.test/sitemap-flights.xml" in (out / "robots.txt").read_text()
    assert "/_tpl/*" in (out / "_headers").read_text()
    assert "/flights/6e-1234/" in (out / "llms.txt").read_text()
