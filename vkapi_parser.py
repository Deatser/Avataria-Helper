
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime

import requests

DEFAULT_FIREBASE_URL = (
    "https://avataria-helper-default-rtdb.europe-west1.firebasedatabase.app"
)

# .env переопределяет базу (тот же ключ, что читает сборщик), но по
# умолчанию хелпер ходит в открытую боевую базу и ничего не требует.
FIREBASE_URL = (os.getenv("FIREBASE_URL") or DEFAULT_FIREBASE_URL).rstrip("/")

# Запрос падает быстро: мёртвая сеть должна давать одну понятную строку в
# логе, а не подвешивать поток на минуту.
FETCH_TIMEOUT = 8
LATEST_COUNT  = 10

# "04.08.2026, 23:59" / "05.08, 12:59" / "07.08.2026 23:59" / "04.08.2026" —
# год и время в базе не гарантированы, поэтому обе части необязательные.
EXPIRE_RE = re.compile(
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})(?:\.(?P<year>\d{2,4}))?"
    r"(?:[\s,]+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
# "07.08.2026 21:03" — то, что vkapi.py кладёт в created/date.
CREATED_RE = re.compile(
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)

# Срок без явного времени в посте vkapi.py уже проставляет сам (20:30);
# сюда это попадает только если время всё-таки потерялось.
DEFAULT_EXPIRE_TIME = (20, 30)


class PromoUnavailable(Exception):
    """База не ответила — в отличие от ответа с HTTP-ошибкой, который
    поднимается как обычное исключение requests."""


@dataclass
class PromoEntry:
    code: str
    title: str | None           # награда; None → в базе не указана
    date_display: str | None    # "07.08.2026 23:59", None → срок не разобран
    status: str                 # "valid" | "expired" | "unknown"
    expire_at: datetime | None
    created_at: datetime | None
    post_id: str                # source_post, для порядка вывода

    @property
    def reward(self) -> str | None:
        """Синоним title — в базе поле называется reward."""
        return self.title


# ── Разбор дат ───────────────────────────────────────────────────────────────


def _parse_expire(raw: str | None) -> tuple[datetime | None, str | None]:
    """"05.08, 12:59" → (datetime, "05.08.2026 12:59").

    Год, если его нет в строке, берётся текущий: посты живут сутки, так
    что «тот же год» — единственное осмысленное прочтение.
    """
    if not raw:
        return None, None
    match = EXPIRE_RE.search(str(raw))
    if not match:
        return None, None

    year = match.group("year")
    if year is None:
        year_num = datetime.now().year
    else:
        year_num = int(year)
        if year_num < 100:
            year_num += 2000

    if match.group("hour") is not None:
        hour, minute = int(match.group("hour")), int(match.group("minute"))
    else:
        hour, minute = DEFAULT_EXPIRE_TIME

    try:
        moment = datetime(year_num, int(match.group("month")),
                          int(match.group("day")), hour, minute)
    except ValueError:
        return None, None
    return moment, moment.strftime("%d.%m.%Y %H:%M")


def _parse_created(raw: str | None) -> datetime | None:
    if not raw:
        return None
    match = CREATED_RE.search(str(raw))
    if not match:
        return None
    hour   = int(match.group("hour")   or 0)
    minute = int(match.group("minute") or 0)
    try:
        return datetime(int(match.group("year")), int(match.group("month")),
                        int(match.group("day")), hour, minute)
    except ValueError:
        return None


# ── Загрузка ─────────────────────────────────────────────────────────────────


def fetch_raw(timeout: int = FETCH_TIMEOUT) -> dict:
    """Весь снимок базы одним GET. Ветки маленькие (несколько десятков
    кодов), поэтому дробить запрос смысла нет."""
    try:
        response = requests.get(f"{FIREBASE_URL}/.json", timeout=timeout)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        raise PromoUnavailable("база промокодов недоступна, проверьте интернет")
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def _iter_post_codes(posts) -> list[dict]:
    """Коды из ветки posts. codes приходит то словарём {"1": {...}}, то
    списком с дырой в нулевом элементе (Firebase сам решает, как хранить
    словарь с числовыми ключами), поэтому оба варианта разбираются одинаково.
    """
    result: list[dict] = []
    if not isinstance(posts, dict):
        return result
    for post_id, post in posts.items():
        if not isinstance(post, dict):
            continue
        codes = post.get("codes")
        if isinstance(codes, dict):
            items = list(codes.values())
        elif isinstance(codes, list):
            items = codes
        else:
            items = []
        for item in items:
            if not isinstance(item, dict) or not item.get("code"):
                continue
            result.append({
                "code":        item["code"],
                "reward":      item.get("reward", ""),
                "expire":      post.get("expire", ""),
                "created":     post.get("date", ""),
                "source_post": str(post.get("source_post") or post_id),
            })
    return result


def _to_entry(record: dict) -> PromoEntry:
    expire_at, date_display = _parse_expire(record.get("expire"))
    reward = (record.get("reward") or "").strip()

    if expire_at is None:
        status = "unknown"
    elif expire_at < datetime.now():
        status = "expired"
    else:
        status = "valid"

    return PromoEntry(
        code         = record["code"],
        title        = reward or None,
        date_display = date_display,
        status       = status,
        expire_at    = expire_at,
        created_at   = _parse_created(record.get("created")),
        post_id      = str(record.get("source_post") or ""),
    )


def _sort_key(entry: PromoEntry):
    """Новые сверху: сначала по времени записи, при его отсутствии — по
    сроку годности, затем по номеру поста (он растёт со временем)."""
    stamp = entry.created_at or entry.expire_at or datetime.min
    try:
        post = int(entry.post_id)
    except (TypeError, ValueError):
        post = 0
    return (stamp, post)


def parse_entries(data: dict) -> list[PromoEntry]:
    """Снимок базы → PromoEntry, новые первыми, без дублей."""
    records: dict[str, dict] = {}

    promocodes = data.get("promocodes")
    if isinstance(promocodes, dict):
        for code, item in promocodes.items():
            if isinstance(item, dict) and item.get("code"):
                records[item["code"]] = item
            elif isinstance(item, dict):
                records[code] = {**item, "code": code}

    # Ветка posts — подстраховка: код, который есть в посте, но почему-то
    # не записан отдельно, всё равно попадёт в список.
    for item in _iter_post_codes(data.get("posts")):
        records.setdefault(item["code"], item)

    entries = [_to_entry(record) for record in records.values()]
    entries.sort(key=_sort_key, reverse=True)
    return entries


def fetch_entries(timeout: int = FETCH_TIMEOUT) -> list[PromoEntry]:
    return parse_entries(fetch_raw(timeout))


def fetch_latest(count: int = LATEST_COUNT,
                 timeout: int = FETCH_TIMEOUT) -> list[PromoEntry]:
    """Последние `count` промокодов — то, что показывает кнопка «Вывести
    данные по последним промокодам»."""
    return fetch_entries(timeout)[:count]


def fetch_available(timeout: int = FETCH_TIMEOUT) -> list[PromoEntry]:
    """Все ещё не просроченные коды — кандидаты на автоактивацию. Уже
    активированные отсеиваются выше, по журналу activated_promo_log."""
    return [entry for entry in fetch_entries(timeout) if entry.status != "expired"]


if __name__ == "__main__":
    for item in fetch_latest():
        print(f"{item.code}  {item.title or '—'}  [{item.date_display or '?'}]"
              f"  {item.status}")
