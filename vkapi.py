import requests
import os
import sys
import time
import re
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

SOURCE_GROUP_ID = 157127262

USER_TOKEN = os.getenv("VK_TOKEN")

# База открыта на запись, так что адрес не секрет — он нужен только чтобы
# можно было подменить её на тестовую через .env или секрет репозитория.
# `or`, а не второй аргумент getenv: незаданный секрет подставляется в
# workflow пустой строкой, и переменная оказывается задана, но пуста.
FIREBASE_URL = (
    os.getenv("FIREBASE_URL")
    or
    "https://avataria-helper-default-rtdb.europe-west1.firebasedatabase.app"
).rstrip("/")

API_VERSION = "5.199"

CHECK_INTERVAL = 60

LOG_FILE = "bot.log"


# Один прогон и выход — режим для GitHub Actions, где расписание задаёт
# сам workflow, а вечный цикл только жёг бы оплачиваемые минуты. Локально
# без флага всё работает как раньше: бесконечный цикл раз в CHECK_INTERVAL.
RUN_ONCE = (
    "--once" in sys.argv
    or
    os.getenv("GITHUB_ACTIONS") == "true"
)



# =========================
# LOG
# =========================


def log(text):

    message = (
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}"
    )

    print(message, flush=True)


    # На раннере файл всё равно исчезает вместе с машиной, а вывод job'а
    # виден в интерфейсе Actions — так что там пишем только в stdout.
    if RUN_ONCE:

        return


    with open(
        LOG_FILE,
        "a",
        encoding="utf-8"
    ) as file:

        file.write(
            message + "\n"
        )



# =========================
# FIREBASE
# =========================


def firebase_get(path):

    response = requests.get(
        f"{FIREBASE_URL}/{path}.json",
        timeout=10
    )

    return response.json()



def firebase_put(path, data):

    requests.put(
        f"{FIREBASE_URL}/{path}.json",
        json=data,
        timeout=10
    )



def promo_exists(code):

    data = firebase_get(
        f"promocodes/{code}"
    )

    return data is not None



# =========================
# VK API
# =========================


def vk_request(method, params):

    url = (
        f"https://api.vk.com/method/{method}"
    )


    params.update(
        {
            "access_token": USER_TOKEN,
            "v": API_VERSION
        }
    )


    response = requests.get(
        url,
        params=params,
        timeout=10
    )


    return response.json()



def get_posts():

    data = vk_request(
        "wall.get",
        {
            "owner_id": -SOURCE_GROUP_ID,
            "count": 5
        }
    )


    if "error" in data:

        log(
            f"Ошибка VK: {data['error']}"
        )

        return []


    posts = data["response"]["items"]


    posts.reverse()


    return posts



def get_comments(post_id):

    data = vk_request(
        "wall.getComments",
        {
            "owner_id": -SOURCE_GROUP_ID,
            "post_id": post_id,
            "count": 100,
            "sort": "asc"
        }
    )


    if "error" in data:

        log(
            f"Ошибка комментариев: {data['error']}"
        )

        return []


    return data["response"]["items"]



# =========================
# PARSER
# =========================


def normalize_text(text):

    return (
        text
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )



def extract_codes(text):

    result = []


    text = normalize_text(
        text
    )


    codes = re.findall(
        r"pr_[A-Za-z0-9]+",
        text,
        re.IGNORECASE
    )


    for code in codes:


        reward = ""


        match = re.search(
            re.escape(code)
            +
            r"\s*[-–—:]\s*(.+)",
            text,
            re.IGNORECASE
        )


        if match:

            reward = (
                match.group(1)
                .strip()
            )


        result.append(
            {
                "code": code,
                "reward": reward
            }
        )


    return result



def extract_reward_from_post(text):

    match = re.search(
        r"Промокод на (.+?) от",
        text,
        re.IGNORECASE
    )


    if match:

        return (
            match.group(1)
            .strip()
        )


    return ""



def extract_expire(text):

    match = re.search(
        r"Доступен для ввода до (.+?) по Мск",
        text,
        re.IGNORECASE
    )


    if match:

        return (
            match.group(1)
            .strip()
        )


    return ""



def get_expire(post):

    text = post.get(
        "text",
        ""
    )


    expire = extract_expire(
        text
    )


    if expire:

        return expire



    date = datetime.fromtimestamp(
        post["date"]
    )


    return (
        date.strftime("%d.%m.%Y")
        +
        " 20:30"
    )



def normalize_reward(text):

    if not text:

        return ""


    return (
        text[0].upper()
        +
        text[1:]
    )



def merge_codes(codes, post_text):

    result = []

    used = set()


    post_reward = extract_reward_from_post(
        post_text
    )


    for item in codes:


        code = item["code"]


        if code in used:

            continue


        used.add(
            code
        )


        reward = item["reward"]


        if not reward:

            reward = normalize_reward(
                post_reward
            )


        result.append(
            {
                "code": code,
                "reward": reward
            }
        )


    return result



# =========================
# SAVE
# =========================


def save_promocode(
    item,
    expire,
    source_post
):

    code = item["code"]


    if promo_exists(code):

        return



    firebase_put(
        f"promocodes/{code}",
        {
            "code": code,

            "reward": item["reward"],

            "expire": expire,

            "source_post": source_post,

            "created": datetime.now().strftime(
                "%d.%m.%Y %H:%M"
            )
        }
    )


    log(
        f"Добавлен промокод: {code}"
    )



def save_post(
    source_id,
    post,
    codes,
    expire
):


    post_codes = {}


    for index, item in enumerate(
        codes,
        start=1
    ):

        post_codes[str(index)] = {

            "code": item["code"],

            "reward": item["reward"]

        }



    firebase_put(
        f"posts/{source_id}",
        {

            "source_post": source_id,

            "date": datetime.fromtimestamp(
                post["date"]
            ).strftime(
                "%d.%m.%Y %H:%M"
            ),

            "expire": expire,

            "codes": post_codes

        }
    )



# =========================
# PROCESS
# =========================


def process_post(post):


    source_id = str(
        post["id"]
    )


    comments = get_comments(
        post["id"]
    )


    all_codes = []


    for comment in comments:


        all_codes.extend(
            extract_codes(
                comment.get(
                    "text",
                    ""
                )
            )
        )



    codes = merge_codes(
        all_codes,
        post.get(
            "text",
            ""
        )
    )



    if not codes:

        return



    expire = get_expire(
        post
    )



    for item in codes:

        save_promocode(
            item,
            expire,
            source_id
        )



    save_post(
        source_id,
        post,
        codes,
        expire
    )



    log(
        f"Пост {source_id} обработан. Кодовых записей: {len(codes)}"
    )



# =========================
# MAIN
# =========================


def main():

    posts = get_posts()


    log(
        f"Проверка выполнена. Постов: {len(posts)}"
    )


    for post in posts:


        try:

            process_post(
                post
            )


        except Exception as e:

            log(
                f"Ошибка обработки поста {post.get('id')}: {e}"
            )



    return len(posts)



# =========================
# START
# =========================


if __name__ == "__main__":


    if not USER_TOKEN:

        log(
            "Не задан VK_TOKEN — нечем ходить в API"
        )

        sys.exit(1)



    if RUN_ONCE:

        log(
            "Разовая проверка"
        )


        # Ноль постов — это не «сегодня тихо», это отказ VK (get_posts на
        # ошибке возвращает пустой список): роняем job, чтобы GitHub
        # прислал письмо о падении, а не молчал с зелёной галочкой.
        if not main():

            log(
                "VK не отдал ни одного поста — проверьте токен"
            )

            sys.exit(1)


        sys.exit(0)



    log(
        "Бот запущен"
    )


    try:


        while True:


            try:

                main()


            except Exception as e:

                log(
                    f"Ошибка: {e}"
                )


            time.sleep(
                CHECK_INTERVAL
            )


    except KeyboardInterrupt:


        log(
            "Бот остановлен пользователем"
        )