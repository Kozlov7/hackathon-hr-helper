import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import llm

BASE = os.path.dirname(__file__)
VACANCY_DIR = os.path.join(BASE, "data", "vacancies")
CACHE_DIR = os.path.join(BASE, "data", "cache")

FIELDS = {
    "salary": "Зарплата (цифры)",
    "schedule": "График работы",
    "address": "Адрес / площадка",
    "experience": "Требуемый опыт",
    "education": "Образование",
    "social": "Соцпакет, ДМС, медицина",
    "transport": "Транспорт до работы",
    "food": "Питание",
    "training": "Обучение, адаптация, наставничество",
    "growth": "Карьерный рост",
    "company": "О компании и продукции",
    "employment": "Оформление по ТК РФ",
}

FIELDS_HINT = "\n".join(f'- "{k}": {v}' for k, v in FIELDS.items())

REQUEST_SYSTEM = f"""Ты — опытный HR-аналитик промышленного предприятия.
Тебе дают заявку руководителя на подбор сотрудника. Разбери её, ничего не придумывая:
каждое значение бери только из текста заявки.
Сохраняй ВСЕ пункты заявки: не объединяй, не сокращай и не пропускай обязанности, требования и условия.
Формулируй близко к тексту заявки, без точки в конце пункта. Подчинение, размер бригады, адаптация
рабочего места — это тоже условия.

Верни JSON:
{{
  "profile": {{"title": "должность", "area": "отрасль и тип производства",
              "level": "уровень позиции (линейный руководитель, специалист...)",
              "summary": "1-2 предложения: кто нужен и зачем"}},
  "search_titles": ["4-6 вариантов, как такую должность называют в вакансиях на сайтах, в именительном падеже
                     единственного числа. Сначала — все названия из самой заявки, включая варианты в скобках
                     и через «/» («начальник смены / участка» → «начальник смены», «начальник участка»).
                     Потом короткие общепринятые варианты (например, «мастер участка»)"],
  "duties": ["обязанность", ...],
  "requirements": [{{"text": "требование", "must": true}}, ...],
  "conditions": ["условие работы", ...],
  "fields": {{"<ключ>": {{"status": "есть" | "расплывчато" | "нет", "value": "что сказано в заявке или пусто"}}, ...}},
  "do_not_publish": [{{"quote": "точная цитата из заявки", "reason": "почему нельзя публиковать",
                      "action": "убрать" | "переформулировать", "suggestion": "как сказать в вакансии или пусто"}}]
}}

Ключи fields (заполни все):
{FIELDS_HINT}

Статус "есть" — сказано конкретно. "расплывчато" — сказано общими словами без конкретики:
«график сменный» без чередования смен — расплывчато; «оклад + премия, обсуждается» без цифр — расплывчато.
"нет" — в заявке об этом ничего. employment — именно официальное оформление по ТК РФ:
«полная занятость» или «постоянная работа» — это не оформление, ставь "нет".
В value пиши, что сказано в заявке (для "расплывчато" — тоже).

В do_not_publish отметь то, что не должно попасть в публичную вакансию:
внутренние вопросы для собеседования, этапы отбора и кто принимает решение,
незаполненные заготовки вида «[X]» или «№ __» (каждую отдельно), формулировки, которые могут выглядеть как
дискриминация по здоровью, полу, возрасту (их можно переформулировать через требования к работе)."""

VACANCY_SYSTEM = f"""Ты — HR-аналитик. Тебе дают текст вакансии с сайта поиска работы.
Разложи её по полям, ничего не додумывая: если чего-то нет в тексте — пиши пусто или "нет".

Верни JSON:
{{
  "title": "должность",
  "company": "компания",
  "city": "город",
  "industry": "отрасль и тип производства, коротко",
  "salary_from": число или null,
  "salary_to": число или null,
  "duties": ["обязанность", ...],
  "requirements": ["требование", ...],
  "conditions": ["условие", ...],
  "fields": {{"<ключ>": {{"status": "есть" | "расплывчато" | "нет", "value": "что сказано, коротко"}}, ...}}
}}

Ключи fields (заполни все):
{FIELDS_HINT}"""


def read_docx(file):
    import docx

    lines = []
    for paragraph in docx.Document(file).paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        numbering = paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
        if numbering:
            level = paragraph._p.pPr.numPr.ilvl
            indent = "  " * (level.val if level is not None else 0)
            text = f"{indent}- {text}"
        lines.append(text)
    return "\n".join(lines)


def read_upload(file):
    if file.name.lower().endswith(".docx"):
        return read_docx(file)
    raw = file.getvalue()
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def as_markdown(text):
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\.\s", stripped):
            lines.append(f"\n**{stripped}**\n")
        elif line.startswith(("- ", "  -")):
            lines.append(line)
        elif stripped:
            lines.append(f"{stripped}  ")
    return "\n".join(lines)


HEALTH_LIMITS = r"противопоказ|эпилепс"
CONCRETE = {"salary": r"\d", "schedule": r"\d"}


def _guard_health(request):
    flagged = " ".join(item.get("quote", "") for item in request.get("do_not_publish", []))
    for section in ("conditions", "requirements"):
        for item in list(request.get(section, [])):
            text = item["text"] if isinstance(item, dict) else item
            if not re.search(HEALTH_LIMITS, text, re.IGNORECASE):
                continue
            request[section].remove(item)
            if not re.search(HEALTH_LIMITS, flagged, re.IGNORECASE):
                flagged += text
                request.setdefault("do_not_publish", []).append(
                    {
                        "quote": text,
                        "reason": "Перечисление заболеваний в публичной вакансии может выглядеть как дискриминация по здоровью",
                        "action": "переформулировать",
                        "suggestion": "работа связана с визуальным контролем качества продукции и чтением чертежей",
                    }
                )


def _guard_concrete(request):
    for key, pattern in CONCRETE.items():
        field = request["fields"].get(key, {})
        if field.get("status") == "есть" and not re.search(pattern, field.get("value", "")):
            field["status"] = "расплывчато"


def parse_request(text):
    request = llm.ask_json("parse_request", REQUEST_SYSTEM, f"Заявка руководителя:\n\n{text}")
    _guard_health(request)
    _guard_concrete(request)
    flagged = " ".join(item.get("quote", "") for item in request.get("do_not_publish", []))
    for match in re.finditer(r"[^\n.;]*\[[^\]]{0,20}\][^\n.;]*", text):
        placeholder = re.search(r"\[[^\]]{0,20}\]", match.group(0)).group(0)
        if placeholder not in flagged:
            request.setdefault("do_not_publish", []).insert(
                0,
                {
                    "quote": match.group(0).strip(" -•"),
                    "reason": f"Незаполненная заготовка {placeholder} — нельзя публиковать",
                    "action": "убрать",
                    "suggestion": "",
                },
            )
    return request


def split_vacancy(raw):
    head, _, body = raw.partition("\n---\n")
    meta = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, body.strip()


def read_vacancy_file(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    meta, body = split_vacancy(raw)
    return meta, body, raw


def _cache_path(vacancy_id):
    return os.path.join(CACHE_DIR, f"{vacancy_id}.json")


def parse_vacancy(vacancy_id, raw):
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path = _cache_path(vacancy_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("source_sha256") == digest:
            return cached["data"]
    if llm.enabled():
        data = llm.ask_json("parse_vacancy", VACANCY_SYSTEM, f"Вакансия:\n\n{raw}")
        source = "gigachat"
    else:
        data = keyword_parse(raw)
        source = "keywords"
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source_sha256": digest, "source": source, "data": data}, f, ensure_ascii=False, indent=2)
    return data


def load_base():
    base = []
    for name in sorted(os.listdir(VACANCY_DIR)):
        if name.endswith(".txt"):
            meta, body, raw = read_vacancy_file(os.path.join(VACANCY_DIR, name))
            base.append({"id": name[:-4], "meta": meta, "body": body, "raw": raw})
    return base


def _stems(text):
    words = re.findall(r"[а-яa-z]+", text.lower().replace("ё", "е"))
    return [w[:5] for w in words if len(w) > 2]


def title_matches(title, search_titles):
    have = set(_stems(title))
    return any(stems and set(stems) <= have for stems in map(_stems, search_titles))


def prefilter(base, search_titles, city):
    candidates, rejected = [], []
    for vacancy in base:
        title = vacancy["meta"].get("Должность", "")
        place = vacancy["meta"].get("Город", "")
        if not title_matches(title, search_titles):
            rejected.append((vacancy, "другая должность"))
        elif city.strip() and not place.lower().startswith(city.strip().lower()):
            rejected.append((vacancy, f"другой город: {place}"))
        else:
            candidates.append(vacancy)
    return candidates, rejected


def enrich(vacancies):
    with ThreadPoolExecutor(max_workers=llm.PARALLEL) as pool:
        results = pool.map(lambda v: parse_vacancy(v["id"], v["raw"]), vacancies)
    for vacancy, data in zip(vacancies, results):
        vacancy["data"] = data
    return vacancies


KEYWORDS = {
    "schedule": r"2/2|5/2|сменн\w* график|график",
    "address": r"ул\.|улица|проезд|площадка",
    "experience": r"опыт",
    "education": r"образовани",
    "social": r"ДМС|соцпакет|социальн\w* пакет|мед\. центр",
    "transport": r"транспорт",
    "food": r"питани|обед",
    "training": r"обучени|наставни|квалификаци",
    "growth": r"карьерн",
    "company": r"концерн|выпускает|дивизион|ГК «|группу? компаний",
    "employment": r"ТК РФ|официальн\w* (?:оформлени|трудоустройств)",
}


def keyword_line(body, pattern):
    for line in re.split(r"[\n;]", body):
        if re.search(pattern, line, re.IGNORECASE):
            line = line.strip(" -;:.☑️")
            return line if len(line) <= 90 else line[:87] + "…"
    return ""


def keyword_parse(raw):
    meta, body = split_vacancy(raw)
    fields = {}
    for key in FIELDS:
        fields[key] = {"status": "нет", "value": ""}
    salary = meta.get("Зарплата", "")
    numbers = [int(n.replace(" ", "")) for n in re.findall(r"\d[\d ]{3,}\d", salary)]
    if numbers:
        fields["salary"] = {"status": "есть", "value": salary}
    for key, pattern in KEYWORDS.items():
        line = keyword_line(body, pattern)
        if line:
            fields[key] = {"status": "есть", "value": line}
    return {
        "title": meta.get("Должность", ""),
        "company": meta.get("Компания", ""),
        "city": meta.get("Город", ""),
        "industry": "",
        "salary_from": numbers[0] if numbers else None,
        "salary_to": numbers[1] if len(numbers) > 1 else None,
        "duties": [],
        "requirements": [],
        "conditions": [],
        "fields": fields,
    }

