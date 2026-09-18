# modules/ava_dancers/vk_scrape_test.py
"""Standalone probe: does a plain HTTP GET see VK wall post text?

Run manually (not part of the app):
    Python313\\python.exe modules\\ava_dancers\\vk_scrape_test.py

Prints how many post-shaped text blocks it found and dumps the first
couple of them, so we can tell whether requests+headers is enough or
whether VK is only rendering posts via JS for guests (in which case we
need a headless browser instead).
"""
from __future__ import annotations

import re

import requests

GROUP_URL = "https://vk.com/ava_promokode"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# VK embeds each wall post's text in a JSON blob inside the page as
# "text":"..."; this is a crude probe, not the real parser.
TEXT_BLOB = re.compile(r'"text":"((?:[^"\\]|\\.)*)"')


def main() -> None:
    resp = requests.get(GROUP_URL, headers=HEADERS, timeout=15)
    print(f"status: {resp.status_code}, bytes: {len(resp.text)}")

    with open("vk_page_dump.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("saved raw HTML to vk_page_dump.html for inspection")

    matches = TEXT_BLOB.findall(resp.text)
    print(f"found {len(matches)} text-shaped blobs")
    for m in matches[:5]:
        decoded = m.encode().decode("unicode_escape")
        print("---")
        print(decoded[:300])


if __name__ == "__main__":
    main()
