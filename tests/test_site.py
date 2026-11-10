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
                "assets/favicon.svg", "abc123def456.txt", "methodology/index.html", "methodology.md", "privacy.md"]:
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
    assert "Draft" in page
    assert "%" not in re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S).split("Which version")[0]


def test_match_index_and_lucky_assets(site):
    out, _ = site
    mi = json.loads((out / "api/v1/match-index.json").read_text())
    assert {c["id"] for c in mi["configs"]} == {c["id"] for c in list_configs()}
    assert all(c["path"].startswith("/seat-maps/") for c in mi["configs"])
    lucky = (out / "assets/lucky.js").read_text().lower()
    for word in ("safe", "risk", "surviv", "danger"):
        assert word not in lucky, word
    page = (out / "seat-maps/indigo-airbus-a321neo-232-seats/index.html").read_text()
    assert 'src="/assets/lucky.js"' in page and "Just for fun" in page
    assert "not mapped yet" in page and "28–29 in" in page
    home = (out / "index.html").read_text()
    assert 'id="lookupform"' in home
