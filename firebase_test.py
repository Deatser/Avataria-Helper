import requests


FIREBASE_URL = "https://avataria-helper-default-rtdb.europe-west1.firebasedatabase.app"


data = {
    "code": "pr_TEST123",
    "reward": "Тестовый предмет",
    "expire": "06.08.2026 23:59"
}


response = requests.post(
    f"{FIREBASE_URL}/promocodes.json",
    json=data
)


print(response.json())