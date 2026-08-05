# app/core/promo_watch.py
"""Polls the public ava_promokode Telegram channel for new promo codes.

Reads https://t.me/s/<channel> — Telegram serves that address as static,
server-rendered HTML (it's what link-preview bots use), so no login, no
bot, and no JS engine is needed to read it. It still needs Telegram itself
reachable, though: in Russia that can mean a VPN, exactly like the regular
app would.
"""
from __future__ import annotations
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape

import requests
from PySide6.QtCore import QThread, Signal

CHANNEL      = "ava_promokode"
PREVIEW_URL  = f"https://t.me/s/{CHANNEL}"
POLL_INTERVAL   = 30.0
# User-initiated actions (the "Запустить детект" and "Вывести данные"
# buttons, and the check that gates turning detection on) fail fast — 5s,
# so a dead Telegram connection reads as a clear log line, not a stall.
CHECK_TIMEOUT   = 5
# The background watch's own steady-state polling is more tolerant — a
# slow reply there just means this cycle's check runs a little late, not
# a button the user is staring at.
POLL_TIMEOUT    = 15
LATEST_COUNT    = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

POST_ID_RE = re.compile(rf'data-post="{CHANNEL}/(\d+)"')
TEXT_RE    = re.compile(r'tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
# A code is either unlabelled (applies everywhere) or follows "ДЛЯ ВК:" /
# "ДЛЯ ОК:" / "ДЛЯ ТГ:" when a post carries a different code per platform.
CODE_RE = re.compile(
    r'(?:ДЛЯ\s+(?P<platform>ВК|ОК|ТГ)\s*:\s*)?<code>(?P<code>pr_[A-Za-z0-9]+)</code>')
TAG_RE  = re.compile(r'<[^>]+>')
# "Промокод: <what it's for>. Доступен для ввода до ..."
ITEM_RE = re.compile(r'Промокод:\s*(.*?)\.\s*Доступ', re.S)
# "до 04.08., 23:59" / "до 29.07, 12:59" — day.month, dot before the comma
# optional, year is never given.
DATE_RE = re.compile(r'до\s+(\d{2})\.(\d{2})\.?\s*,')
# Trailing "х30" / "х25 000" quantity marker on an item's name.
QTY_RE  = re.compile(r'\s*х\s*([\d\s]+)\s*$')


class PromoUnavailable(Exception):
    """Telegram itself did not answer — as opposed to answering with an
    HTTP error, which is a different, unmasked exception."""


def _fetch_preview(timeout: int) -> str:
    try:
        resp = requests.get(PREVIEW_URL, headers=HEADERS, timeout=timeout)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        raise PromoUnavailable(
            "не получается открыть Telegram, вероятно он не работает")
    resp.raise_for_status()
    return resp.text


def _parse_posts(html: str) -> list[tuple[int, str]]:
    """[(post_id, text_html), ...] in the order the page lists them."""
    posts = []
    for chunk in html.split("tgme_widget_message_wrap")[1:]:
        id_match   = POST_ID_RE.search(chunk)
        text_match = TEXT_RE.search(chunk)
        if id_match and text_match:
            posts.append((int(id_match.group(1)), text_match.group(1)))
    return posts


def _code_for_vk(text_html: str) -> str | None:
    """The VK-labelled code, or the one unlabelled code, or None when the
    post only carries codes for other platforms (ОК/ТГ)."""
    matches = list(CODE_RE.finditer(text_html))
    if not matches:
        return None
    labelled = {m.group("platform"): m.group("code")
                for m in matches if m.group("platform")}
    if "ВК" in labelled:
        return labelled["ВК"]
    if not labelled:
        return matches[0].group("code")
    return None


def _format_title(raw: str) -> str | None:
    """"макияжная кисть х30" → "Макияжная кисть (30x)". Capitalised, and
    a trailing "хN" quantity marker turned into "(Nx)" if there is one."""
    raw = raw.strip()
    if not raw:
        return None
    qty_match = QTY_RE.search(raw)
    if qty_match:
        qty  = qty_match.group(1).replace(" ", "")
        base = raw[:qty_match.start()].strip()
        raw  = f"{base} ({qty}x)"
    return raw[0].upper() + raw[1:]


@dataclass
class PromoEntry:
    post_id: int
    code: str
    title: str | None          # None → could not be determined
    date_display: str | None   # "04.08.26", None → could not be determined
    status: str                # "valid" | "expired" | "unknown"


def _parse_entry(post_id: int, text_html: str) -> PromoEntry | None:
    code = _code_for_vk(text_html)
    if not code:
        return None

    text = unescape(TAG_RE.sub(" ", text_html))

    item_match = ITEM_RE.search(text)
    title = _format_title(item_match.group(1)) if item_match else None

    date_display = None
    status = "unknown"
    date_match = DATE_RE.search(text)
    if date_match:
        day, month = date_match.group(1), date_match.group(2)
        year = datetime.now().year
        try:
            expiry = date(year, int(month), int(day))
        except ValueError:
            expiry = None
        if expiry is not None:
            status = "expired" if expiry < date.today() else "valid"
            date_display = f"{day}.{month}.{year % 100:02d}"

    return PromoEntry(post_id=post_id, code=code, title=title,
                      date_display=date_display, status=status)


class PromoFetchLatest(QThread):
    """One-shot: the newest LATEST_COUNT posts that carry a VK code,
    newest first — for the "Вывести данные по последним промокодам"
    button, independent of PromoWatch's own running/stopped state."""
    fetched = Signal(list)   # [PromoEntry, ...]
    error   = Signal(str)

    def run(self):
        try:
            html = _fetch_preview(CHECK_TIMEOUT)
        except PromoUnavailable as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:
            self.error.emit(str(exc))
            return

        entries = []
        for post_id, text_html in reversed(_parse_posts(html)):
            entry = _parse_entry(post_id, text_html)
            if entry:
                entries.append(entry)
            if len(entries) >= LATEST_COUNT:
                break
        self.fetched.emit(entries)


class PromoCheck(QThread):
    """One-shot reachability probe, gating "Запустить автоматический
    детект промокодов" — the button turns the detector on only once this
    confirms Telegram actually answered within CHECK_TIMEOUT."""
    ok    = Signal()
    error = Signal(str)

    def run(self):
        try:
            _fetch_preview(CHECK_TIMEOUT)
        except PromoUnavailable as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.ok.emit()


class PromoWatch(QThread):
    """Watches ava_promokode and reports each new post's VK code, oldest
    to newest. The first poll after a fresh start (last_post_id == 0) only
    baselines on the newest post currently up — it does not replay the
    channel's back-catalogue of codes.
    """
    code_found = Signal(str, int)   # code, post_id
    armed      = Signal(int)        # post_id — baseline set, nothing missed yet
    error      = Signal(str)

    def __init__(self, last_post_id: int = 0):
        super().__init__()
        self._last_post_id = last_post_id
        self._stop_event = threading.Event()

    def stop_watch(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except PromoUnavailable as exc:
                self.error.emit(str(exc))
            except Exception as exc:
                self.error.emit(str(exc))
            self._stop_event.wait(POLL_INTERVAL)

    def _poll_once(self):
        # Only the very first poll (arming) is gated by the button that
        # started this thread — fail fast there. Every poll after that is
        # this watch's own steady-state upkeep, and gets the more patient
        # timeout.
        timeout = CHECK_TIMEOUT if self._last_post_id == 0 else POLL_TIMEOUT
        html = _fetch_preview(timeout)
        posts = _parse_posts(html)
        if not posts:
            return

        if self._last_post_id == 0:
            self._last_post_id = max(post_id for post_id, _ in posts)
            self.armed.emit(self._last_post_id)
            return

        for post_id, text_html in posts:
            if post_id <= self._last_post_id:
                continue
            code = _code_for_vk(text_html)
            if code:
                self.code_found.emit(code, post_id)
            self._last_post_id = post_id
