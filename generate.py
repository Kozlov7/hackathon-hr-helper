import io
import json
import os
import re

import llm

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "data", "template_irz.txt")

VACANCY_SYSTEM = """Ты — HR-редактор промышленного предприятия. Напиши текст вакансии для публикации
строго по шаблону компании (структура и стиль даны ниже).

Жёсткие правила:
1. Используй только факты из разбора заявки и из ответов руководителя. Ничего не добавляй от себя:
   ни зарплатных цифр, ни льгот, ни адреса, ни графика, если их нет во входных данных.
2. Ответы руководителя важнее заявки: если ответ меняет требование (например, допускает среднее
   специальное образование), пиши по ответу.
3. Перенеси ВСЕ обязанности, требования и условия из разбора — ни один пункт не пропускай
   (можно только объединить явные повторы).
4. Если сведений для раздела нет — сократи раздел, но не выдумывай. Не вставляй заглушки вида [уточнить].
5. Не включай ничего из списка «не публиковать». Если для пункта дана безопасная формулировка — используй её.
6. Пиши коротко, деловым тоном, пункты с маленькой буквы без точки в конце, как в шаблоне.
7. Требования не ужесточай и не смягчай сверх ответов руководителя — передай смысл точно.
8. Раздел «Контактная информация» не пиши — его добавит HR. Строку «Вакансия в ООО …» не пиши.
Верни только текст вакансии."""

CHECK_SYSTEM = """Ты — проверяющий. Сравни текст вакансии с исходной заявкой руководителя
(и ответами руководителя, если они есть).
Найди:
- unsupported: утверждения в вакансии, которых нет в заявке или ответах (выдуманные факты);
- distorted: требования, которые в вакансии ужесточены, смягчены или искажены по сравнению с заявкой и ответами;
- leaked: внутренняя информация, попавшая в вакансию (вопросы к собеседованию, этапы отбора, заготовки вида [X]);
- missing: обязанности, требования и условия из заявки, которых нет в вакансии (кроме того, что
  заявка просит не публиковать: вопросы к собеседованию, этапы отбора, кто принимает решение).
Проверь каждый пункт заявки по очереди.
Верни JSON: {"unsupported": [{"quote": "...", "why": "..."}], "distorted": [{"quote": "...", "why": "..."}],
"leaked": [{"quote": "...", "why": "..."}], "missing": [{"quote": "пункт заявки", "why": "..."}]}.
Пустые списки, если всё в порядке."""

LEAK_PATTERNS = {
    r"\[[^\]]{0,20}\]": "незаполненная заготовка в квадратных скобках",
    r"этап\w* отбора|принимает решение": "внутренний порядок отбора",
    r"эпилепс|инвалидност\w* противопоказ": "перечисление заболеваний",
}

FIELD_SECTION = {"experience": "requirements", "education": "requirements"}
FIELD_REPLACES = {"schedule": "график", "salary": "оклад", "address": "адрес"}


def load_template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _item(text):
    text = text.strip().rstrip(".;")
    return text[:1].lower() + text[1:]


def template_vacancy(request, answers):
    duties = list(request["duties"])
    requirements = [r["text"] for r in request["requirements"]]
    conditions = list(request["conditions"])
    for answer in answers:
        key = answer["key"]
        if key.startswith("req"):
            index = int(key[3:]) - 1
            if 0 <= index < len(requirements):
                requirements[index] = answer["answer"]
        elif key.startswith("market") or FIELD_SECTION.get(key) == "requirements":
            requirements.append(answer["answer"])
        else:
            word = FIELD_REPLACES.get(key)
            if word:
                conditions = [c for c in conditions if word not in c.lower()]
            conditions.append(answer["answer"])
    for item in request.get("do_not_publish", []):
        if item.get("action") == "переформулировать" and item.get("suggestion"):
            conditions.append(item["suggestion"])
    sections = [
        request["profile"]["title"],
        "",
        "Задачи",
        *[f"- {_item(x)}" for x in duties],
        "",
        "Требования",
        *[f"- {_item(x)}" for x in requirements],
        "",
        "Условия",
        *[f"- {_item(x)}" for x in conditions],
    ]
    return "\n".join(sections)


def generate_vacancy(request, answers):
    if not llm.enabled():
        return template_vacancy(request, answers)
    user = (
        f"Шаблон компании:\n{load_template()}\n\n"
        f"Разбор заявки:\n{json.dumps(request, ensure_ascii=False, indent=1)}\n\n"
        f"Ответы руководителя на уточняющие вопросы:\n"
        f"{json.dumps(answers, ensure_ascii=False, indent=1) if answers else 'нет'}"
    )
    text = _clean(llm.ask_text("generate_vacancy", VACANCY_SYSTEM, user))
    missing = missing_items(request, text)
    if missing:
        fix = (
            f"Текст вакансии:\n{text}\n\nВ нём пропущены пункты заявки:\n"
            + "\n".join(f"- {m['quote']}" for m in missing)
            + "\n\nДобавь их в подходящие разделы, остальное не меняй. Верни весь текст вакансии."
        )
        text = _clean(llm.ask_text("generate_vacancy", VACANCY_SYSTEM, fix))
    return text


def _clean(text):
    text = re.split(r"\n\s*Контактная информация", text)[0]
    return "\n".join(line for line in text.splitlines() if not line.startswith("Вакансия в ")).strip()


def _stems(text):
    return {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", text.lower().replace("ё", "е"))}


def missing_items(request, vacancy_text):
    have = _stems(vacancy_text)
    missing = []
    sections = (("Обязанность", request["duties"]), ("Требование", [r["text"] for r in request["requirements"]]),
                ("Условие", request["conditions"]))
    for title, items in sections:
        for item in items:
            words = _stems(item)
            if words and len(words & have) / len(words) < 0.6:
                missing.append({"quote": item, "why": f"{title} из заявки не найдено в тексте вакансии"})
    return missing


def check_vacancy(request, request_text, answers, vacancy_text):
    leaked = []
    for pattern, why in LEAK_PATTERNS.items():
        match = re.search(pattern, vacancy_text, re.IGNORECASE)
        if match:
            line = next(l for l in vacancy_text.splitlines() if match.group(0) in l)
            leaked.append({"quote": line.strip(" -"), "why": why})
    if not llm.enabled():
        return {"unsupported": [], "distorted": [], "leaked": leaked, "missing": missing_items(request, vacancy_text), "demo": True}
    user = (
        f"Заявка руководителя:\n{request_text}\n\n"
        f"Ответы руководителя:\n{json.dumps(answers, ensure_ascii=False) if answers else 'нет'}\n\n"
        f"Текст вакансии:\n{vacancy_text}"
    )
    result = llm.ask_json("check_vacancy", CHECK_SYSTEM, user)
    known = {item["quote"] for item in result.get("leaked", [])}
    result["leaked"] = result.get("leaked", []) + [x for x in leaked if x["quote"] not in known]
    result["missing"] = missing_items(request, vacancy_text)
    return result


def questions_letter(position, ours, market):
    lines = [
        f"Тема: уточнения по вакансии «{position}»",
        "",
        "Добрый день!",
        "",
        f"Подготовили вакансию «{position}» по вашей заявке. Перед публикацией сравнили её "
        "с похожими вакансиями на рынке — есть несколько вопросов. Вакансия выйдет и без ответов, "
        "но с ними она будет точнее.",
    ]
    number = 1
    for title, items in (
        ("Требования, которые у нас строже или реже, чем на рынке", ours),
        ("Что обычно указывают в похожих вакансиях, а у нас нет", market),
    ):
        if not items:
            continue
        lines += ["", title.upper(), ""]
        for item in items:
            lines.append(f"{number}. {item['question']}")
            lines.append(f"   — {item['fact']}.")
            lines.append("")
            number += 1
    lines += [
        "Если какой-то пункт указывать не нужно — так и напишите, вакансия выйдет без него.",
        "",
        "Спасибо!",
    ]
    return "\n".join(lines).replace("\n\n\n", "\n\n")


def to_docx(text, title):
    import docx

    document = docx.Document()
    document.add_heading(title, level=1)
    for line in text.splitlines():
        if line.strip():
            document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
