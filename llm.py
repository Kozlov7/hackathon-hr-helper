import json
import os
import re
import time
import uuid

import requests
import urllib3
from dotenv import load_dotenv

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-Pro")
SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY", "").strip()
STATIC_TOKEN = os.getenv("GIGACHAT_TOKEN", "").strip()
VERIFY_SSL = os.getenv("GIGACHAT_VERIFY_SSL", "0") == "1"
PARALLEL = int(os.getenv("GIGACHAT_PARALLEL", "1"))
DEMO_DIR = os.path.join(os.path.dirname(__file__), "data", "demo")

_token = {"value": "", "expires": 0.0}


class LLMError(Exception):
    pass


def enabled():
    return bool(AUTH_KEY or STATIC_TOKEN)


def _access_token():
    if STATIC_TOKEN and not AUTH_KEY:
        return STATIC_TOKEN
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    resp = requests.post(
        OAUTH_URL,
        headers={
            "Authorization": f"Basic {AUTH_KEY}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"scope": SCOPE},
        verify=VERIFY_SSL,
        timeout=30,
    )
    if resp.status_code != 200:
        raise LLMError(f"Авторизация GigaChat: {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    _token["value"] = data["access_token"]
    _token["expires"] = data["expires_at"] / 1000
    return _token["value"]


def chat(system, user, temperature=0.2):
    for attempt in range(4):
        resp = _post_chat(system, user, temperature)
        if resp.status_code != 429:
            break
        time.sleep(2 ** attempt * 2)
    if resp.status_code != 200:
        raise LLMError(f"GigaChat: {resp.status_code} {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def _post_chat(system, user, temperature):
    return requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {_access_token()}", "Accept": "application/json"},
        json={
            "model": MODEL,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        verify=VERIFY_SSL,
        timeout=120,
    )


def _extract_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("в ответе нет JSON")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])


def ask_json(task, system, user):
    if not enabled():
        return demo(task)
    system = system + "\nОтвечай только валидным JSON без пояснений и без markdown."
    answer = chat(system, user, temperature=0.1)
    try:
        return _extract_json(answer)
    except ValueError:
        answer = chat(system, user + "\n\nВерни строго JSON, ничего кроме JSON.", temperature=0.0)
        return _extract_json(answer)


def ask_text(task, system, user):
    if not enabled():
        return demo(task)["text"]
    return chat(system, user, temperature=0.1).strip()


def demo(task):
    path = os.path.join(DEMO_DIR, f"{task}.json")
    if not os.path.exists(path):
        raise LLMError(f"Нет ключа GigaChat и нет демо-ответа для «{task}»")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
