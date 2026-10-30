import json
import statistics

import llm
from parse import FIELDS

RELEVANT_SCORE = 50

RELEVANCE_SYSTEM = """Ты — HR-аналитик. Оцени, насколько каждая вакансия с рынка похожа на нашу позицию
и годится для сравнения. Учитывай: роль (тот же уровень руководства), тип производства
(дискретное машиностроение/приборостроение ближе, чем стройка, автосервис или металлургия), город.
Верни JSON: {"items": [{"id": "id вакансии", "score": 0-100, "reason": "одно короткое предложение"}]}"""

REQUIREMENTS_SYSTEM = """Ты — HR-аналитик. Сравни требования нашей заявки с требованиями вакансий на рынке
по смыслу, а не по словам («опыт мастером от 1 года» и «2 года на руководящей должности
в производстве» — одно и то же требование).

Для каждого нашего требования укажи:
- same_in: вакансии, где есть такое же или более строгое требование;
- softer_in: вакансии, где требование есть, но мягче (например, допускают среднее специальное вместо высшего,
  меньше лет опыта, «приветствуется» вместо «обязательно»);
- market_wording: как это требование сформулировано В ВАКАНСИЯХ РЫНКА (своими словами по их текстам,
  а не повтор нашей формулировки); если в вакансиях его нет — напиши «в вакансиях не указывают».
В same_in и softer_in для каждой вакансии дай id и quote — точную короткую цитату из её текста.

Отдельно найди market_only: требования, навыки и компетенции, которые встречаются в вакансиях рынка,
но которых нет в нашей заявке (с id и цитатами). Внимательно просмотри требования и обязанности
каждой вакансии; обычно таких пунктов 2-5. Условия работы (соцпакет, график, зарплата) сюда не включай.

Используй только id из списка. Не придумывай вакансий, требований и цитат, которых нет в текстах.
Верни JSON: {"ours": [{"index": номер, "same_in": [{"id": "...", "quote": "..."}],
"softer_in": [{"id": "...", "quote": "..."}], "market_wording": "..."}],
"market_only": [{"text": "...", "in": [{"id": "...", "quote": "..."}]}]}"""

FIELD_QUESTIONS = {
    "salary": "Какой уровень дохода можно указать в вакансии: вилку оклада, «от …» или ориентир с учётом премии?",
    "schedule": "Какой именно сменный график: 2/2, 5/2, длительность смены, есть ли ночные смены?",
    "address": "Где находится рабочее место: адрес или площадка завода?",
    "experience": "Сколько лет опыта и в какой должности действительно необходимо?",
    "education": "Какое образование необходимо?",
    "social": "Что входит в соцпакет: ДМС, медицинское обслуживание, путёвки?",
    "transport": "Есть ли корпоративный транспорт или компенсация проезда?",
    "food": "Есть ли питание на территории или компенсация питания?",
    "training": "Есть ли адаптация, наставничество, обучение для нового сотрудника?",
    "growth": "Какие возможности карьерного роста можно указать?",
    "company": "Что рассказать о цехе и продукции, чтобы кандидат понимал, чем будет заниматься?",
    "employment": "Подтверждаем официальное оформление по ТК РФ?",
}


def company(vacancy):
    return vacancy.get("data", {}).get("company") or vacancy["meta"].get("Компания") or vacancy["id"]


def _brief(vacancy):
    data = vacancy["data"]
    return {
        "id": vacancy["id"],
        "title": data.get("title") or vacancy["meta"].get("Должность", ""),
        "company": company(vacancy),
        "city": data.get("city") or vacancy["meta"].get("Город", ""),
        "text": vacancy["body"][:2500],
    }


def relevance(request, vacancies):
    user = (
        f"Наша позиция: {json.dumps(request['profile'], ensure_ascii=False)}\n\n"
        f"Вакансии:\n{json.dumps([_brief(v) for v in vacancies], ensure_ascii=False, indent=1)}"
    )
    result = llm.ask_json("relevance", RELEVANCE_SYSTEM, user)
    scores = {item["id"]: item for item in result.get("items", [])}
    for vacancy in vacancies:
        item = scores.get(vacancy["id"], {"score": 0, "reason": "ИИ не оценил эту вакансию"})
        vacancy["score"] = int(item.get("score", 0))
        vacancy["reason"] = item.get("reason", "")
        vacancy["relevant"] = vacancy["score"] >= RELEVANT_SCORE
    return vacancies


def _examples(items, names):
    found = []
    for item in items:
        if isinstance(item, str):
            item = {"id": item, "quote": ""}
        if item.get("id") in names and item["id"] not in [f["id"] for f in found]:
            found.append({"id": item["id"], "company": names[item["id"]], "quote": item.get("quote", "")})
    return found


def requirements(request, vacancies):
    ours = "\n".join(f"{i}. {r['text']}" for i, r in enumerate(request["requirements"], 1))
    user = (
        f"Требования нашей заявки:\n{ours}\n\n"
        f"Вакансии рынка:\n{json.dumps([_brief(v) for v in vacancies], ensure_ascii=False, indent=1)}"
    )
    result = llm.ask_json("requirements", REQUIREMENTS_SYSTEM, user)
    names = {v["id"]: company(v) for v in vacancies}
    total = len(vacancies)
    by_index = {item.get("index"): item for item in result.get("ours", [])}
    rows = []
    for i, req in enumerate(request["requirements"], 1):
        item = by_index.get(i, {})
        same = _examples(item.get("same_in", []), names)
        softer = [e for e in _examples(item.get("softer_in", []), names) if e["id"] not in [s["id"] for s in same]]
        if len(softer) > len(same) and len(softer) >= max(2, total / 3):
            verdict = "мягче на рынке"
        elif len(same) + len(softer) >= total / 2:
            verdict = "типично"
        else:
            verdict = "редко на рынке"
        rows.append(
            {
                "key": f"req{i}",
                "text": req["text"],
                "same": same,
                "softer": softer,
                "total": total,
                "verdict": verdict,
                "market_wording": item.get("market_wording", ""),
            }
        )
    market_only = []
    for i, item in enumerate(result.get("market_only", []), 1):
        found = _examples(item.get("in", []), names)
        if len(found) >= 2:
            market_only.append({"key": f"market{i}", "text": item["text"], "examples": found, "total": total})
    market_only.sort(key=lambda x: -len(x["examples"]))
    return rows, market_only


def ours_vs_market(rows):
    questions = []
    for row in rows:
        if row["verdict"] == "мягче на рынке":
            questions.append(
                {
                    "key": row["key"],
                    "title": row["text"],
                    "fact": f"У нас строже, чем на рынке: мягче в {len(row['softer'])} из {row['total']} похожих вакансий",
                    "market": row["market_wording"],
                    "examples": row["softer"],
                    "question": f"В заявке: «{row['text']}». На рынке обычно: {row['market_wording']}. "
                    "Оставляем как есть или можно смягчить?",
                    "default": True,
                }
            )
        elif row["verdict"] == "редко на рынке":
            count = len(row["same"]) + len(row["softer"])
            questions.append(
                {
                    "key": row["key"],
                    "title": row["text"],
                    "fact": f"На рынке встречается редко: {count} из {row['total']} похожих вакансий",
                    "market": row["market_wording"],
                    "examples": row["same"] + row["softer"],
                    "question": f"В заявке: «{row['text']}». В похожих вакансиях это требование почти не указывают. "
                    "Оно действительно обязательно?",
                    "default": False,
                }
            )
    return questions


def market_vs_ours(request, vacancies, market_only):
    total = len(vacancies)
    questions = []
    for key, label in FIELDS.items():
        ours = request["fields"].get(key, {"status": "нет", "value": ""})
        if ours.get("status") == "есть":
            continue
        stated = [v for v in vacancies if v["data"]["fields"].get(key, {}).get("status") == "есть"]
        if len(stated) < 2:
            continue
        ours_text = f"сказано общо: «{ours['value']}»" if ours.get("status") == "расплывчато" else "не указано"
        questions.append(
            {
                "key": key,
                "title": label,
                "fact": f"Указано в {len(stated)} из {total} похожих вакансий, у нас {ours_text}",
                "examples": [{"id": v["id"], "company": company(v), "quote": v["data"]["fields"][key].get("value", "")} for v in stated],
                "question": FIELD_QUESTIONS[key],
                "count": len(stated),
                "default": True,
            }
        )
    for item in market_only:
        questions.append(
            {
                "key": item["key"],
                "title": item["text"],
                "fact": f"Требуют в {len(item['examples'])} из {item['total']} похожих вакансий, в нашей заявке нет",
                "examples": item["examples"],
                "question": f"Похожие вакансии требуют: {item['text'].lower()}. Добавить это в требования?",
                "count": len(item["examples"]),
                "default": True,
            }
        )
    questions.sort(key=lambda q: -q["count"])
    return questions


def salary_stats(vacancies):
    ranges = []
    sources = []
    for vacancy in vacancies:
        data = vacancy["data"]
        low, high = data.get("salary_from"), data.get("salary_to")
        if low or high:
            ranges.append((low or high, high or low))
            sources.append(f"{company(vacancy)}: {vacancy['meta'].get('Зарплата', '')}")
    if not ranges:
        return None
    return {
        "count": len(ranges),
        "total": len(vacancies),
        "min": min(r[0] for r in ranges),
        "max": max(r[1] for r in ranges),
        "median": statistics.median((r[0] + r[1]) / 2 for r in ranges),
        "sources": sources,
    }
