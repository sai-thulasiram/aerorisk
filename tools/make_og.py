"""Regenerate static/og.png (1200x630) from tools/og.html. Edit the HTML first if you rename the site."""
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parent.parent
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
    pg.goto((root / "tools" / "og.html").as_uri())
    pg.screenshot(path=str(root / "static" / "og.png"))
    b.close()
print("wrote static/og.png")
