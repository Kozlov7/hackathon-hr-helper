import base64
import functools
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
LEAK_PATTERNS = r"\[[^\]]{0,20}\]|эпилепс|противопоказ|заболеван|невролог|этап\w* отбора|принимает решение|мужчин|женщин|\bдо \d{2} лет|возраст"

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


def missing_by_section(request, text):
    have = _stems(text)
    sections = (
        ("duties", request["duties"]),
        ("requirements", [r["text"] for r in request["requirements"]]),
        ("conditions", request["conditions"]),
    )
    missing = []
    for key, items in sections:
        for item in items:
            words = _stems(item)
            if words and len(words & have) / len(words) < 0.6:
                missing.append((key, item))
    return missing


def missing_items(request, text):
    return [item for _, item in missing_by_section(request, text)]


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
    for key, item in missing_by_section(request, plain_text(vacancy)):
        if not re.search(LEAK_PATTERNS, item, re.IGNORECASE):
            vacancy[key].append(_item(item))
    return vacancy


def hh_html(vacancy):
    e = html.escape
    parts = []
    for key, title in SECTIONS:
        if vacancy[key]:
            parts.append(f"<p><strong>{title}:</strong></p>")
            parts.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in vacancy[key]) + "</ul>")
    return "\n".join(parts)


BRAND_DIR = os.path.join(os.path.dirname(__file__), "data", "brand")
RED = "#c31727"
SLIDES = (
    ("391524.jpg", "65+", "лет<br> истории"),
    ("391527.jpg", "190 000 м2", "производственной площади"),
    ("391528.jpg", "9", "товаро-производящих направлений"),
    ("391533.jpg", "5500+", "<br> сотрудников"),
)
ABOUT = (
    "<strong>ИЖЕВСКИЙ РАДИОЗАВОД</strong> — предприятие приборостроительной отрасли России, выпускающее уникальные "
    "наукоёмкие изделия. Наша аппаратура участвует в космических программах, обеспечивает безопасность и связь "
    "на железной дороге, сотовую связь в удалённых и труднодоступных местах, работает на нефтедобывающих месторождениях.",
    "<strong>ИРЗ</strong> — это место работы, где каждый сотрудник может найти свою зону роста и внести вклад "
    "в создание крупных отраслевых проектов.",
)
VALUES = (
    ("391513.svg", "Достойная оплата труда", "Выплата заработной платы 2 раза в месяц, премии за регистрацию патентов, премии по результатам работы"),
    ("391512.svg", "Трудоустройство в соответствии с ТК РФ", "Оплачиваемый отпуск, больничный"),
    ("391519.svg", "Реферальная программа", "Финансовая поддержка в начале карьеры, релокационный пакет для иногородних кандидатов и молодых специалистов, акция «Приведи друга»"),
    ("391514.svg", "Организация питания", "Доступные комплексные обеды в комбинате питания, 2 буфета-кондитерских, комнаты отдыха и приёма пищи"),
    ("391515.svg", "Забота о здоровье", "Собственный медицинский пункт, первичные и регулярные медосмотры"),
    ("391517.svg", "Соблюдение условий охраны труда", "СИЗы и технологические комплекты одежды"),
    ("391516.svg", "Поддержка в личных вопросах", "Профсоюзный комитет, материальная поддержка в важных жизненных ситуациях"),
    ("391518.svg", "Корпоративная культура", "Корпоративные праздники, подарки, путёвки в санаторий и лагерь"),
    ("391523.svg", "Возможность совмещать учёбу с работой", "Прохождение оплачиваемой практики с дальнейшим трудоустройством, помощь с ВКР"),
    ("391520.svg", "Обучение за счет предприятия", "Переподготовка и повышение квалификации, курсы, языковые занятия, форумы, выставки, конференции"),
    ("391521.svg", "Поддержка творчества и спорта", "Мастер-классы, театральная студия, студия современного танца, спортивный туризм и командные виды спорта"),
    ("391522.svg", "Скидки для сотрудников", "Бесплатное посещение спортивной инфраструктуры предприятия, бонусная профсоюзная карта, скидки от партнёров в фитнес-залы"),
)


@functools.lru_cache(maxsize=None)
def asset(name):
    mime = "image/svg+xml" if name.endswith(".svg") else "image/jpeg"
    with open(os.path.join(BRAND_DIR, name), "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


CSS = f"""
.vacancy {{font-family: Arial, "Helvetica Neue", sans-serif; color: #101010; max-width: 690px; margin: 0 auto;
  font-size: 16px; line-height: 140%;}}
.vacancy * {{box-sizing: border-box;}}
.vacancy h1 {{font-size: 30px; line-height: 1.2; margin: 0 0 10px 0; font-weight: 700;}}
.vacancy .salary {{font-size: 22px; margin: 0 0 12px 0;}}
.vacancy .meta {{color: #5e6c77; font-size: 15px; margin: 2px 0;}}
.vacancy .meta b {{color: #101010; font-weight: 400;}}
.vacancy .card {{position: relative; overflow: hidden; margin-top: 22px; border: 1px solid #e2e2e2; border-radius: 16px;}}
.vacancy .box {{padding: 10px 30px 0 30px;}}
.vacancy .head {{position: relative; display: flex; align-items: center; gap: 16px; padding-top: 20px; padding-bottom: 20px;}}
.vacancy .head::after {{content: ""; position: absolute; top: 0; right: 0; width: 221px; height: 166px; z-index: 2;
  background: url("{{triangle}}") center/cover no-repeat;}}
.vacancy .head img {{height: 44px; display: block;}}
.vacancy .head .divider {{align-self: stretch; width: 1px; background: #b6b6b6;}}
.vacancy .head .slogan {{color: {RED}; font-weight: 700; font-size: 32px; line-height: 110%;}}
.vacancy .slider {{display: flex; overflow-x: auto; scroll-snap-type: x mandatory; scrollbar-width: none;}}
.vacancy .slider::-webkit-scrollbar {{display: none;}}
.vacancy .slide {{flex: 0 0 100%; scroll-snap-align: start; background: center top/cover no-repeat; padding-top: 46%;}}
.vacancy .slide p {{display: flex; align-items: center; height: 140px; margin: 0; padding: 0 30px; font-weight: 700;
  font-size: 22px; line-height: 100%; max-width: 330px;}}
.vacancy .slide span {{color: {RED}; margin-right: 6px;}}
.vacancy .dots {{display: flex; gap: 6px; padding: 8px 30px 0 30px;}}
.vacancy .dots i {{width: 8px; height: 8px; border-radius: 50%; background: #d0d0d0;}}
.vacancy .dots i:first-child {{background: {RED};}}
.vacancy .about p {{margin: 0 0 12px 0;}}
.vacancy .about strong {{font-size: 22px; line-height: 130%;}}
.vacancy .content {{padding-top: 18px;}}
.vacancy .content .title {{margin: 0 0 12px 0; font-weight: 700; font-size: 22px; line-height: 130%;}}
.vacancy .content ul {{list-style: none; margin: 0 0 32px 18px; padding: 0;}}
.vacancy .content li {{position: relative; margin-bottom: 4px;}}
.vacancy .content li::before {{content: ""; position: absolute; top: 8px; left: -14px; width: 6px; height: 6px;
  border-radius: 100%; background: {RED};}}
.vacancy .values {{padding-top: 18px; padding-bottom: 40px;}}
.vacancy .values ul {{display: flex; flex-wrap: wrap; gap: 10px; list-style: none; margin: 0; padding: 0;}}
.vacancy .values li {{position: relative; flex: 1 1 150px; min-width: 150px; height: 143px; perspective: 800px;}}
.vacancy .values .inner {{position: absolute; inset: 0; transition: transform 0.3s ease-in; transform-style: preserve-3d;}}
.vacancy .values li:hover .inner {{transform: rotateY(-180deg);}}
.vacancy .values .front, .vacancy .values .back {{position: absolute; inset: 0; padding: 12px; border-radius: 8px;
  backface-visibility: hidden; display: flex; flex-direction: column;}}
.vacancy .values .front {{background: #ededed; justify-content: space-between;}}
.vacancy .values .front img {{width: 40px; height: 40px; margin: 0 0 0 auto;}}
.vacancy .values .front p {{margin: 0; font-weight: 700; font-size: 14px; line-height: 110%;}}
.vacancy .values .back {{background: #1f3a60; transform: rotateY(180deg); justify-content: center;}}
.vacancy .values .back p {{margin: 0; color: #fff; font-size: 12px; line-height: 120%;}}
.vacancy .footer {{position: relative; height: 344px; background: url("{{footer}}") center/cover no-repeat;}}
.vacancy .footer div {{position: absolute; top: 0; right: 30px; text-align: right;}}
.vacancy .footer h4 {{margin: 0 0 6px 0; color: {RED}; font-weight: 700; font-size: 32px; line-height: 110%;}}
.vacancy .footer p {{margin: 0 0 2px 0;}}
.vacancy .skills {{margin-top: 22px;}}
.vacancy .skills h3 {{font-size: 20px; margin: 0 0 10px 0;}}
.vacancy .skills span {{display: inline-block; background: #eef1f3; border-radius: 8px; padding: 5px 12px;
  margin: 0 6px 8px 0; font-size: 14px;}}
@media (max-width: 655px) {{
  .vacancy .box {{padding-left: 20px; padding-right: 20px;}}
  .vacancy .head {{display: block;}}
  .vacancy .head .divider {{display: block; width: 165px; height: 1px; margin: 4px 0;}}
  .vacancy .head::after {{width: 130px; height: 110px;}}
  .vacancy .slide {{padding-top: 60%;}}
}}
@media print {{
  .vacancy .slide:not(:first-child), .vacancy .dots, .vacancy .values .back {{display: none;}}
  .vacancy .card {{border: 0;}}
}}
"""


def styles():
    return CSS.replace("{triangle}", asset("391511.svg")).replace("{footer}", asset("391531.jpg"))


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
    slides = "".join(
        f'<div class="slide" style="background-image:url({asset(image)})"><p><span>{number}</span> {text}</p></div>'
        for image, number, text in SLIDES
    )
    sections = ""
    for key, title in SECTIONS:
        if vacancy[key]:
            items = "".join(f"<li>{e(x)}</li>" for x in vacancy[key])
            sections += f'<p class="title">{title}:</p><ul>{items}</ul>'
    values = "".join(
        f'<li><div class="inner"><div class="front"><img src="{asset(icon)}" alt=""><p>{title}</p></div>'
        f'<div class="back"><p>{text}</p></div></div></li>'
        for icon, title, text in VALUES
    )
    skills = ""
    if vacancy["skills"]:
        skills = '<div class="skills"><h3>Ключевые навыки</h3>' + "".join(f"<span>{e(s)}</span>" for s in vacancy["skills"]) + "</div>"
    return (
        f'<div class="vacancy"><h1>{e(vacancy["title"])}</h1><p class="salary">{salary_line}</p>{meta}'
        f'<div class="card">'
        f'<div class="head box"><img src="{asset("391510.svg")}" alt="Ижевский Радиозавод">'
        f'<span class="divider"></span><span class="slogan">для жизни</span></div>'
        f'<div class="slider">{slides}</div><div class="dots">{"<i></i>" * len(SLIDES)}</div>'
        f'<div class="about box">{"".join(f"<p>{p}</p>" for p in ABOUT)}</div>'
        f'<div class="content box">{sections}</div>'
        f'<div class="values box"><p class="title" style="font-weight:700;font-size:22px;margin:0 0 12px 0">'
        f"Нас выбирают, потому что:</p><ul>{values}</ul></div>"
        f'<div class="footer"><div><h4>Работай на ИРЗ!</h4><p>Жизнь — цель, работа — средство</p>'
        f"<p>Выбирай возможность!</p></div></div>"
        f"</div>{skills}</div>"
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
.print {{max-width: 690px; margin: 0 auto 24px auto; text-align: right;}}
.print button {{font: 15px Arial, sans-serif; padding: 9px 18px; border: 0; border-radius: 8px;
  background: {RED}; color: #fff; cursor: pointer;}}
@media print {{ .print {{display: none;}} body {{padding: 0;}} }}
{styles()}
</style>
</head>
<body>
<div class="print"><button onclick="window.print()">Печать / сохранить в PDF</button></div>
{render_html(vacancy)}
<script>
const slider = document.querySelector(".vacancy .slider");
const dots = document.querySelectorAll(".vacancy .dots i");
let current = 0;
setInterval(() => {{
  current = (current + 1) % dots.length;
  slider.scrollTo({{left: slider.clientWidth * current, behavior: "smooth"}});
  dots.forEach((dot, i) => dot.style.background = i === current ? "{RED}" : "#d0d0d0");
}}, 4000);
</script>
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
