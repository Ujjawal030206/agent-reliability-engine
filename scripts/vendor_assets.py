"""
Bundle the dashboard's CDN dependencies into static/vendor/ so it renders offline.

The dashboard should work offline, without depending on cdn.tailwindcss.com or Google
Fonts being reachable. This downloads, from their official sources:

    Tailwind CSS play CDN build   -> static/vendor/tailwindcss.js
    Geist, JetBrains Mono,
    Material Symbols Outlined     -> static/vendor/fonts.css + static/vendor/fonts/*.woff2

Run it again to refresh the copies:  python scripts/vendor_assets.py
"""

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor"
FONT_DIR = VENDOR / "fonts"

TAILWIND_URL = "https://cdn.tailwindcss.com?plugins=forms,container-queries"
FONT_CSS_URLS = [
    "https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500;700&display=swap",
    "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap",
]
# Google Fonts serves woff2 only to browsers it recognises.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main():
    FONT_DIR.mkdir(parents=True, exist_ok=True)

    tailwind = fetch(TAILWIND_URL)
    (VENDOR / "tailwindcss.js").write_bytes(tailwind)
    print(f"tailwindcss.js  {len(tailwind) / 1024:.0f} KB")

    css_parts, total = [], 0
    for css_url in FONT_CSS_URLS:
        css = fetch(css_url).decode("utf-8")

        def localise(match):
            nonlocal total
            font_url = match.group(1)
            name = hashlib.sha1(font_url.encode()).hexdigest()[:16] + ".woff2"
            data = fetch(font_url)
            (FONT_DIR / name).write_bytes(data)
            total += len(data)
            return f"url(fonts/{name})"

        css_parts.append(re.sub(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", localise, css))

    (VENDOR / "fonts.css").write_text("\n".join(css_parts), encoding="utf-8")
    count = len(list(FONT_DIR.glob("*.woff2")))
    print(f"fonts.css + {count} font files  {total / 1024:.0f} KB")


if __name__ == "__main__":
    try:
        main()
    except OSError as exc:
        sys.exit(f"download failed: {exc}")
