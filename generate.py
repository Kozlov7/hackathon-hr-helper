import html
import io
import json
import os
import re

import llm

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "data", "template_irz.txt")
COMPANY = "АО «ИРЗ»"
COMPANY_INTRO = [
    "ИЖЕВСКИЙ РАДИОЗАВОД — российская группа приборостроительных компаний, реализующая российские "
    "и международные проекты в области космоса, железной дороги, топливно-энергетического комплекса.",
    "Дата основания — 6 марта 1958 года. На текущий момент предприятие насчитывает более 5500 человек.",
]
HEADER_FIELDS = (
    ("experience", "Опыт работы"),
    ("schedule", "График"),
    ("employment", "Оформление"),
    ("address", "Адрес"),
)
SECTIONS = (("duties", "Обязанности"), ("requirements", "Требования"), ("conditions", "Условия"))
LEAK_PATTERNS = r"\[[^\]]{0,20}\]|эпилепс|противопоказ|заболеван|невролог|этап\w* отбора|принимает решение"

VACANCY_SYSTEM = """Ты — HR-редактор АО «ИРЗ». Подготовь вакансию для hh.ru строго в формате компании (шаблон ниже).

Жёсткие правила:
1. Используй только факты из разбора заявки. Ничего не добавляй от себя: ни зарплатных цифр, ни льгот,
   ни адреса, ни графика, если их нет в заявке.
2. Перенеси ВСЕ обязанности, требования и условия из разбора — ни один пункт не пропускай
   (можно только объединить явные повторы).
3. Не включай ничего из списка do_not_publish. Если для пункта дана безопасная формулировка (suggestion) — используй её.
4. Пункты — с маленькой буквы, без точки в конце, как в шаблоне. Требования не ужесточай и не смягчай.
5. skills — 3-6 ключевых навыков короткими тегами, только из требований и обязанностей заявки.
Верни JSON: {"title": "должность", "duties": ["..."], "requirements": ["..."], "conditions": ["..."], "skills": ["..."]}"""


def load_template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _item(text):
    text = str(text).strip().strip("-•").strip().rstrip(".;")
    return text[:1].lower() + text[1:]


def _stems(text):
    return {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", text.lower().replace("ё", "е"))}


def plain_text(vacancy):
    lines = [vacancy["title"], ""]
    for key, title in SECTIONS:
        if vacancy[key]:
            lines += [f"{title}:", *[f"- {x}" for x in vacancy[key]], ""]
    if vacancy["skills"]:
        lines.append("Ключевые навыки: " + ", ".join(vacancy["skills"]))
    return "\n".join(lines).strip()


def missing_items(request, text):
    have = _stems(text)
    missing = []
    sections = (request["duties"], [r["text"] for r in request["requirements"]], request["conditions"])
    for items in sections:
        for item in items:
            words = _stems(item)
            if words and len(words & have) / len(words) < 0.6:
                missing.append(item)
    return missing


def header(request):
    fields = request["fields"]
    salary = fields.get("salary", {})
    rows = [("Уровень дохода", salary["value"] if salary.get("status") != "нет" and salary.get("value") else "не указан")]
    for key, label in HEADER_FIELDS:
        field = fields.get(key, {})
        if field.get("status") != "нет" and field.get("value"):
            rows.append((label, field["value"]))
    return rows


def _finish(data, request):
    vacancy = {"title": str(data.get("title") or request["profile"]["title"]).strip()}
    for key in ("duties", "requirements", "conditions", "skills"):
        items = [_item(x) for x in data.get(key, []) if str(x).strip()]
        vacancy[key] = [x for x in items if not re.search(LEAK_PATTERNS, x, re.IGNORECASE)]
    vacancy["skills"] = [s[:1].upper() + s[1:] for s in vacancy["skills"]]
    vacancy["header"] = header(request)
    vacancy["intro"] = list(COMPANY_INTRO)
    return vacancy


def demo_vacancy(request):
    conditions = list(request["conditions"])
    for item in request.get("do_not_publish", []):
        if item.get("action") == "переформулировать" and item.get("suggestion"):
            conditions.append(item["suggestion"])
    data = {
        "title": request["profile"]["title"],
        "duties": request["duties"],
        "requirements": [r["text"] for r in request["requirements"]],
        "conditions": conditions,
        "skills": [],
    }
    return _finish(data, request)


def generate_vacancy(request):
    if not llm.enabled():
        return demo_vacancy(request)
    user = (
        f"Шаблон компании:\n{load_template()}\n\n"
        f"Разбор заявки:\n{json.dumps(request, ensure_ascii=False, indent=1)}"
    )
    vacancy = _finish(llm.ask_json("generate_vacancy", VACANCY_SYSTEM, user), request)
    missing = missing_items(request, plain_text(vacancy))
    if missing:
        fix = (
            f"{user}\n\nЧерновик вакансии:\n{json.dumps(vacancy, ensure_ascii=False)}\n\n"
            "В черновике пропущены пункты заявки:\n" + "\n".join(f"- {m}" for m in missing)
            + "\n\nДобавь их в подходящие разделы, остальное не меняй. Верни весь JSON."
        )
        vacancy = _finish(llm.ask_json("generate_vacancy", VACANCY_SYSTEM, fix), request)
    return vacancy


def hh_html(vacancy):
    e = html.escape
    parts = [f"<p>{e(p)}</p>" for p in vacancy["intro"]]
    for key, title in SECTIONS:
        if vacancy[key]:
            parts.append(f"<p><strong>{title}:</strong></p>")
            parts.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in vacancy[key]) + "</ul>")
    return "\n".join(parts)


CSS = """
.vacancy {font-family: Arial, "Helvetica Neue", sans-serif; color: #000; max-width: 820px; line-height: 1.5;}
.vacancy h1 {font-size: 30px; line-height: 1.2; margin: 0 0 12px 0; font-weight: 700;}
.vacancy .salary {font-size: 22px; margin: 0 0 14px 0;}
.vacancy .meta {color: #5e6c77; font-size: 15px; margin: 2px 0;}
.vacancy .meta b {color: #000; font-weight: 400;}
.vacancy .company {margin: 18px 0; padding: 14px 18px; border: 1px solid #dce3e8; border-radius: 12px;
  font-size: 17px; font-weight: 600;}
.vacancy .company span {display: block; font-weight: 400; font-size: 14px; color: #5e6c77;}
.vacancy .description {font-size: 16px;}
.vacancy .description p {margin: 0 0 10px 0;}
.vacancy .description ul {margin: 0 0 16px 0; padding-left: 22px;}
.vacancy .description li {margin: 2px 0;}
.vacancy h3 {font-size: 20px; margin: 22px 0 10px 0;}
.vacancy .skills span {display: inline-block; background: #eef1f3; border-radius: 8px; padding: 5px 12px;
  margin: 0 6px 8px 0; font-size: 14px;}
"""


def render_html(vacancy):
    e = html.escape
    salary = dict(vacancy["header"]).get("Уровень дохода", "не указан")
    rows = [(label, value) for label, value in vacancy["header"] if label != "Уровень дохода"]
    if re.search(r"\d", salary):
        salary_line = e(salary)
    else:
        salary_line = "Уровень дохода не указан"
        if salary != "не указан":
            rows.insert(0, ("Оплата", salary))
    meta = "".join(f'<p class="meta">{e(label)}: <b>{e(value)}</b></p>' for label, value in rows)
    skills = ""
    if vacancy["skills"]:
        skills = '<h3>Ключевые навыки</h3><div class="skills">' + "".join(f"<span>{e(s)}</span>" for s in vacancy["skills"]) + "</div>"
    return (
        f'<div class="vacancy"><h1>{e(vacancy["title"])}</h1>'
        f'<p class="salary">{salary_line}</p>{meta}'
        f'<div class="company">{COMPANY}<span>Ижевск</span></div>'
        f'<div class="description">{hh_html(vacancy)}</div>{skills}</div>'
    )


def html_document(vacancy):
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(vacancy["title"])} — {COMPANY}</title>
<style>
body {{margin: 0; padding: 32px 16px; background: #fff;}}
.vacancy {{margin: 0 auto;}}
.print {{display: block; margin: 0 auto 24px auto; max-width: 820px; text-align: right;}}
.print button {{font: 15px Arial, sans-serif; padding: 9px 18px; border: 0; border-radius: 8px;
  background: #0070ff; color: #fff; cursor: pointer;}}
@media print {{ .print {{display: none;}} body {{padding: 0;}} }}
{CSS}
</style>
</head>
<body>
<div class="print"><button onclick="window.print()">Печать / сохранить в PDF</button></div>
{render_html(vacancy)}
</body>
</html>
"""


def to_docx(vacancy):
    import docx

    document = docx.Document()
    document.add_heading(vacancy["title"], level=1)
    for label, value in vacancy["header"]:
        document.add_paragraph(f"{label}: {value}")
    document.add_paragraph(f"{COMPANY}, Ижевск")
    for paragraph in vacancy["intro"]:
        document.add_paragraph(paragraph)
    for key, title in SECTIONS:
        if vacancy[key]:
            document.add_paragraph().add_run(f"{title}:").bold = True
            for item in vacancy[key]:
                document.add_paragraph(item, style="List Bullet")
    if vacancy["skills"]:
        document.add_paragraph().add_run("Ключевые навыки:").bold = True
        document.add_paragraph(", ".join(vacancy["skills"]))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def text_docx(text, title):
    import docx

    document = docx.Document()
    document.add_heading(title, level=1)
    for line in text.splitlines():
        if line.strip():
            document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def questions_letter(position, market, ours):
    lines = [
        f"Тема: уточнения по вакансии «{position}»",
        "",
        "Добрый день!",
        "",
        f"Готовим к публикации вакансию «{position}» по вашей заявке. Сравнили её с похожими вакансиями "
        "на рынке — есть несколько вопросов. Вакансия выйдет и без ответов, но с ними она будет точнее.",
    ]
    number = 1
    for title, items in (
        ("Что обычно указывают в похожих вакансиях, а у нас нет", market),
        ("Требования, которые у нас строже или реже, чем на рынке", ours),
    ):
        if not items:
            continue
        lines += ["", title.upper(), ""]
        for item in items:
            lines.append(f"{number}. {item['question']}")
            lines.append(f"   — {item['fact']}.")
            lines.append("")
            number += 1
    lines += ["Если какой-то пункт указывать не нужно — так и напишите, вакансия выйдет без него.", "", "Спасибо!"]
    return "\n".join(lines).replace("\n\n\n", "\n\n")
