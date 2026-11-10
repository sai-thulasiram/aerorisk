"""Regenerate static/hero.jpg, static/band.jpg and static/og.jpg from tools/sky.html (own artwork)."""
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent.parent
sky = (root / "tools" / "sky.html").as_uri()
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 2400, "height": 1200})
    pg.goto(sky); pg.screenshot(path=str(root / "static" / "hero.jpg"), type="jpeg", quality=80)
    pg = b.new_page(viewport={"width": 2400, "height": 700})
    pg.goto(sky); pg.evaluate("document.querySelector('.blur').style.setProperty('--b','14px')")
    pg.screenshot(path=str(root / "static" / "band.jpg"), type="jpeg", quality=74)
    pg = b.new_page(viewport={"width": 1200, "height": 630})
    pg.goto((root / "tools" / "og.html").as_uri()); pg.screenshot(path=str(root / "static" / "og.jpg"), type="jpeg", quality=85)
    b.close()
print("wrote static/hero.jpg, static/band.jpg, static/og.jpg")
