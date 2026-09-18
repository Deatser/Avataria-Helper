# modules/ava_dancers/tg_scrape_test.py
"""Standalone probe: does the t.me public preview page show post text
without any login, bot, or JS?

Run manually (not part of the app):
    py modules\\ava_dancers\\tg_scrape_test.py

Prints how many posts it found and dumps the last few, so we can see
the real promo-code format before writing the regex.
"""
from __future__ import annotations

import re

import requests

CHANNEL = "ava_promokode"
PREVIEW_URL = f"https://t.me/s/{CHANNEL}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Each post's text lives in a <div class="tgme_widget_message_text ...">.
POST_TEXT = re.compile(
    r'tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S
)
TAG = re.compile(r"<[^>]+>")


def main() -> None:
    resp = requests.get(PREVIEW_URL, headers=HEADERS, timeout=15)
    print(f"status: {resp.status_code}, bytes: {len(resp.text)}")

    with open("tg_page_dump.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("saved raw HTML to tg_page_dump.html for inspection")

    posts = POST_TEXT.findall(resp.text)
    print(f"found {len(posts)} posts")
    for raw in posts[-5:]:
        text = TAG.sub("", raw).replace("&amp;", "&").strip()
        print("---")
        print(text[:400])


if __name__ == "__main__":
    main()
