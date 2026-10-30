import json
import os
import time

import pandas as pd
import streamlit as st

import compare
import generate
import llm
import parse

SAMPLE_REQUEST = os.path.join(os.path.dirname(__file__), "data", "sample_request.docx")
STEPS = ["Заявка", "Разбор заявки", "Рынок", "Сравнение и вопросы", "Вакансия"]
STATUS_ICON = {"есть": "✅", "расплывчато": "🟡", "нет": "⚪"}
DEFAULT_CITY = "Ижевск"

st.set_page_config(page_title="HR-помощник: вакансия", page_icon="📋", layout="wide", initial_sidebar_state="collapsed")
state = st.session_state
state.setdefault("step", 0)
for key in list(state.keys()):
    if key.startswith(("on_", "q_", "a_")) or key in ("letter_text", "vacancy_edit"):
        state[key] = state[key]

st.markdown(
    """
<style>
.stepper {display: flex; align-items: center; margin: 0.2rem 0 1.4rem 0;}
.stepper .item {display: flex; align-items: center; gap: 0.5rem; white-space: nowrap;}
.stepper .dot {width: 2rem; height: 2rem; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-weight: 600; border: 2px solid #c9ced6; color: #8a919c; background: #fff;}
.stepper .label {color: #8a919c;}
.stepper .done .dot {background: #2e9d5b; border-color: #2e9d5b; color: #fff;}
.stepper .done .label {color: #2e3440;}
.stepper .current .dot {background: #1f5fbf; border-color: #1f5fbf; color: #fff;}
.stepper .current .label {color: #1f5fbf; font-weight: 600;}
.stepper .line {flex: 1; height: 2px; background: #c9ced6; margin: 0 0.7rem; min-width: 1.5rem;}
.stepper .line.done {background: #2e9d5b;}
.example {color: #5b6472; font-size: 0.92rem; margin: 0.1rem 0 0 0.2rem;}
</style>
""",
    unsafe_allow_html=True,
)


def stepper(current):
    parts = []
    for i, name in enumerate(STEPS):
        status = "done" if i < current else "current" if i == current else ""
        mark = "✓" if i < current else str(i + 1)
        if i:
            parts.append(f'<div class="line {"done" if i <= current else ""}"></div>')
        parts.append(f'<div class="item {status}"><div class="dot">{mark}</div><div class="label">{name}</div></div>')
    st.markdown(f'<div class="stepper">{"".join(parts)}</div>', unsafe_allow_html=True)


def go(step):
    state.step = step
    st.rerun()


def nav(forward_label=None, forward_enabled=True, on_forward=None):
    st.divider()
    left, _, right = st.columns([1, 2, 1])
    if state.step > 0 and left.button("← Назад", width="stretch"):
        go(state.step - 1)
    if forward_label and right.button(forward_label, type="primary", disabled=not forward_enabled, width="stretch"):
        if on_forward:
            on_forward()
        go(state.step + 1)


def run(label, func, *args):
    try:
        with st.spinner(label):
            return func(*args)
    except llm.LLMError as error:
        st.error(str(error))
        st.stop()


def copy_button(text, label="📋 Скопировать в буфер обмена"):
    st.iframe(
        f"""
<button id="copy" style="padding:0.45rem 0.9rem;border:1px solid #c9ced6;border-radius:0.5rem;background:#fff;
  font-size:0.9rem;cursor:pointer;font-family:sans-serif">{label}</button>
<script>
const text = {json.dumps(text).replace("</", "<\\/")};
const button = document.getElementById("copy");
button.onclick = async () => {{
  try {{ await navigator.clipboard.writeText(text); }}
  catch (e) {{
    const area = document.createElement("textarea");
    area.value = text; document.body.appendChild(area); area.select();
    document.execCommand("copy"); area.remove();
  }}
  button.innerText = "✅ Скопировано";
  setTimeout(() => button.innerText = {json.dumps(label)}, 2000);
}};
</script>""",
        height=48,
    )


def examples_block(examples, limit=4):
    for example in examples[:limit]:
        quote = f" — «{example['quote']}»" if example.get("quote") else ""
        st.markdown(f'<div class="example">• <b>{example["company"]}</b>{quote}</div>', unsafe_allow_html=True)
    if len(examples) > limit:
        st.markdown(f'<div class="example">и ещё {len(examples) - limit}</div>', unsafe_allow_html=True)


top_left, top_right = st.columns([3, 1])
top_left.title("📋 HR-помощник: от заявки к вакансии")
with top_right:
    if llm.enabled():
        st.success(f"GigaChat подключён ({llm.MODEL})")
    else:
        st.warning("Демо-режим: без ключа GigaChat")
    if "started" in state:
        st.caption(f"Работаем над вакансией {(time.time() - state.started) / 60:.0f} мин")
    if st.button("Начать заново"):
        state.clear()
        st.rerun()

stepper(state.step)

if state.step == 0:
    st.subheader("Заявка руководителя")
    source = st.radio("Откуда взять заявку", ["Пример из материалов", "Загрузить файл", "Вставить текст"], horizontal=True)
    text = ""
    if source == "Пример из материалов":
        text = parse.read_docx(SAMPLE_REQUEST)
    elif source == "Загрузить файл":
        file = st.file_uploader("Заявка руководителя (.docx или .txt)", type=["docx", "txt"])
        if file:
            text = parse.read_upload(file)
    else:
        text = st.text_area("Текст заявки", height=300)
    if text.strip() and source != "Вставить текст":
        with st.container(border=True, height=480):
            st.markdown(parse.as_markdown(text))

    def parse_request():
        state.started = state.get("started", time.time())
        state.request_text = text
        state.request = run("GigaChat разбирает заявку…", parse.parse_request, text)
        for key in ("market", "rows", "vacancy_edit", "check", "letter_text"):
            state.pop(key, None)

    nav("Разобрать заявку →", forward_enabled=bool(text.strip()), on_forward=parse_request)

elif state.step == 1:
    request = state.request
    profile = request["profile"]
    st.info(
        "**Что здесь.** GigaChat прочитал заявку и разложил её по полочкам. Ничего не добавлено: "
        "каждый пункт взят из текста заявки. Из этого разбора дальше строится всё остальное — "
        "поиск похожих вакансий, сравнение с рынком и текст вакансии."
    )

    st.subheader(f"Профиль позиции: {profile['title']}")
    st.caption("Кого ищем — по профилю подбираются похожие вакансии на рынке.")
    col1, col2 = st.columns(2)
    col1.markdown(f"**Отрасль и производство:** {profile['area']}")
    col2.markdown(f"**Уровень позиции:** {profile['level']}")
    st.markdown(f"> {profile['summary']}")
    st.markdown("**Как такую должность называют в вакансиях:** " + ", ".join(request.get("search_titles", [])))

    st.subheader("Заявка по пунктам")
    st.caption("Из этих пунктов собирается текст вакансии на последнем шаге.")
    col1, col2, col3 = st.columns(3)
    with col1.container(border=True):
        st.markdown("**Обязанности**\n" + "\n".join(f"- {d}" for d in request["duties"]))
    with col2.container(border=True):
        st.markdown("**Требования**\n" + "\n".join(f"- {r['text']}" for r in request["requirements"]))
    with col3.container(border=True):
        st.markdown("**Условия**\n" + "\n".join(f"- {c}" for c in request["conditions"]))

    st.subheader("Что обычно указывают в вакансии — и что есть в заявке")
    st.caption(
        "12 пунктов, которые встречаются в вакансиях. ✅ сказано конкретно · 🟡 сказано общими словами · "
        "⚪ не сказано. Пробелы — не ошибка: вакансия соберётся и без них. На шаге «Сравнение» "
        "проверим, какие из них указывает рынок."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Пункт": label,
                    "В заявке": f"{STATUS_ICON.get(f.get('status'), '')} {f.get('status', '')}",
                    "Что сказано": f.get("value", "") or "—",
                }
                for key, label in parse.FIELDS.items()
                for f in [request["fields"].get(key, {"status": "нет", "value": ""})]
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    if request.get("do_not_publish"):
        st.subheader("🚫 Не попадёт в публикацию")
        st.caption("Это есть в заявке, но кандидатам показывать нельзя. В текст вакансии не попадёт.")
        with st.container(border=True):
            for item in request["do_not_publish"]:
                quote = item["quote"].replace("\n", " ")
                quote = quote if len(quote) <= 140 else quote[:137] + "…"
                result = f"→ будет: *{item['suggestion']}*" if item.get("suggestion") else "→ убрано"
                st.markdown(f"- «{quote}» — {item['reason'].rstrip('.')} {result}")

    with st.expander("Как получен разбор"):
        st.markdown(
            "1. Текст заявки отправляется в GigaChat вместе с инструкцией: роль «HR-аналитик», "
            "список пунктов, которые нужно заполнить, и правило «ничего не придумывать».\n"
            "2. GigaChat возвращает ответ в строгом формате (JSON) — программа раскладывает его по блокам этой страницы.\n"
            "3. Статусы ✅/🟡/⚪ ставит ИИ по смыслу: например, «график сменный» — это 🟡, "
            "потому что не сказано, какие смены."
        )
    nav("Далее: рынок →")

elif state.step == 2:
    request = state.request
    st.info(
        "**Как отбираем вакансии для сравнения — в два этапа.** "
        "① Быстрый фильтр по названию должности и городу — без ИИ, мгновенно. "
        "При подключении hh.ru этот же фильтр уйдёт в поиск hh: он вернёт десятки свежих вакансий, а не тысячи. "
        "② GigaChat читает только прошедшие фильтр и оценивает, насколько они похожи по смыслу."
    )
    col1, col2 = st.columns([3, 1])
    titles_text = col1.text_input(
        "Названия должности (через запятую) — подобраны ИИ по заявке, можно изменить",
        ", ".join(request.get("search_titles", [])),
    )
    city = col2.text_input("Город", DEFAULT_CITY, help="В заявке город не указан — по умолчанию город компании")
    titles = [t.strip() for t in titles_text.split(",") if t.strip()]
    market_key = (tuple(titles), city.strip())

    if state.get("market", {}).get("key") != market_key:
        base = parse.load_base()
        candidates, rejected = parse.prefilter(base, titles, city)
        candidates = run("Читаю вакансии, прошедшие фильтр…", parse.enrich, candidates)
        if candidates:
            candidates = run("GigaChat оценивает похожесть…", compare.relevance, request, candidates)
        state.market = {"key": market_key, "base": len(base), "candidates": candidates, "rejected": rejected}
        state.pop("rows", None)
    market = state.market
    candidates = market["candidates"]

    col1, col2, col3 = st.columns(3)
    col1.metric("В базе", market["base"])
    col2.metric("① По должности и городу", len(candidates))
    col3.metric("② Похожи по смыслу", sum(v["relevant"] for v in candidates))

    if candidates:
        st.caption(f"Вакансии с похожестью от {compare.RELEVANT_SCORE}% попадают в сравнение. Галочку можно поменять вручную.")
        table = pd.DataFrame(
            [
                {
                    "В сравнении": v["relevant"],
                    "Похожесть": v["score"],
                    "Должность": v["meta"].get("Должность", ""),
                    "Компания": compare.company(v),
                    "Зарплата": v["meta"].get("Зарплата", ""),
                    "Почему": v["reason"],
                    "Ссылка": v["meta"].get("Ссылка", ""),
                }
                for v in candidates
            ]
        )
        edited = st.data_editor(
            table,
            hide_index=True,
            width="stretch",
            disabled=[c for c in table.columns if c != "В сравнении"],
            column_config={
                "Похожесть": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
                "Ссылка": st.column_config.LinkColumn(display_text="открыть"),
                "Почему": st.column_config.TextColumn(width="large"),
            },
        )
        for vacancy, included in zip(candidates, edited["В сравнении"]):
            if vacancy["relevant"] != bool(included):
                vacancy["relevant"] = bool(included)
                state.pop("rows", None)
    else:
        st.warning("Под фильтр не попала ни одна вакансия — измените названия должности или город.")

    if market["rejected"]:
        with st.expander(f"Отсеяно фильтром ①: {len(market['rejected'])}"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Должность": v["meta"].get("Должность", ""),
                            "Компания": v["meta"].get("Компания", ""),
                            "Город": v["meta"].get("Город", ""),
                            "Причина": reason,
                        }
                        for v, reason in market["rejected"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    chosen = [v for v in candidates if v["relevant"]]
    stats = compare.salary_stats(chosen)
    if chosen:
        if stats:
            values = f"{stats['min']:,.0f} ₽" if stats["min"] == stats["max"] else f"{stats['min']:,.0f} – {stats['max']:,.0f} ₽"
            st.markdown(
                f"**Зарплата у похожих вакансий:** указана в {stats['count']} из {stats['total']} — "
                f"{values.replace(',', ' ')} ({'; '.join(stats['sources'])})"
            )
        else:
            st.markdown(f"**Зарплата у похожих вакансий:** не указана ни в одной из {len(chosen)}")
    nav("Далее: сравнение →", forward_enabled=len(chosen) > 0)

elif state.step == 3:
    request = state.request
    chosen = [v for v in state.market["candidates"] if v["relevant"]]
    if "rows" not in state:
        state.rows, state.market_only = run("GigaChat сравнивает требования с рынком…", compare.requirements, request, chosen)
        state.pop("letter_text", None)
    ours_items = compare.ours_vs_market(state.rows)
    market_items = compare.market_vs_ours(request, chosen, state.market_only)
    st.caption(
        f"Сравнение с {len(chosen)} похожими вакансиями. Каждый пункт — вопрос руководителю. "
        "Если ответ уже известен, впишите его: он попадёт в вакансию, а вопрос уйдёт из письма. "
        "Снимите галочку, если вопрос задавать не нужно."
    )

    def question_cards(items):
        selected, answered = [], []
        for item in items:
            with st.container(border=True):
                head, toggle = st.columns([4, 1])
                head.markdown(f"**{item['title']}**")
                state.setdefault(f"on_{item['key']}", item["default"])
                state.setdefault(f"q_{item['key']}", item["question"])
                on = toggle.checkbox("В письмо", key=f"on_{item['key']}")
                st.caption(item["fact"])
                if item.get("market"):
                    st.markdown(f"На рынке обычно: *{item['market']}*")
                examples_block(item["examples"])
                question = st.text_input("Вопрос руководителю", key=f"q_{item['key']}")
                answer = st.text_input("Ответ, если уже известен", key=f"a_{item['key']}", placeholder="например: подойдёт и среднее специальное")
                if answer.strip():
                    answered.append({"key": item["key"], "topic": item["title"], "question": question, "answer": answer.strip()})
                elif on:
                    selected.append({**item, "question": question})
        return selected, answered

    st.subheader("1. Есть у нас — а на рынке иначе")
    st.caption("Требования заявки, которые строже, чем у похожих вакансий, или почти не встречаются на рынке. Могут сужать круг кандидатов.")
    ours_selected, ours_answered = question_cards(ours_items)
    typical = [r for r in state.rows if r["verdict"] == "типично"]
    if typical:
        with st.expander(f"✅ Совпадает с рынком: {len(typical)}"):
            for row in typical:
                st.markdown(f"- **{row['text']}** — так же в {len(row['same']) + len(row['softer'])} из {row['total']}")

    st.subheader("2. Есть на рынке — а у нас нет")
    st.caption("Что похожие вакансии указывают, а в заявке этого нет или сказано общими словами.")
    market_selected, market_answered = question_cards(market_items)
    state.answers = ours_answered + market_answered

    st.subheader("✉️ Письмо руководителю")
    auto = generate.questions_letter(request["profile"]["title"], ours_selected, market_selected)
    if state.get("letter_text") in (None, state.get("letter_auto")):
        state.letter_text = auto
    state.letter_auto = auto
    edited = state.letter_text != auto
    st.caption(
        f"Вопросов в письме: {len(ours_selected) + len(market_selected)}. Письмо можно редактировать прямо здесь."
        + (" Вы изменили текст — галочки выше его больше не меняют." if edited else "")
    )
    letter = st.text_area("Текст письма", key="letter_text", height=420, label_visibility="collapsed")
    col1, col2, col3, col4 = st.columns([1.3, 1, 1, 1.3])
    with col1:
        copy_button(letter)
    col2.download_button("Скачать .txt", letter, file_name="вопросы_руководителю.txt")
    col3.download_button("Скачать .docx", generate.to_docx(letter, "Вопросы по вакансии"), file_name="вопросы_руководителю.docx")
    col4.button("↺ Собрать письмо заново", disabled=not edited, on_click=lambda: state.update(letter_text=auto))
    nav("Далее: вакансия →")

else:
    answers = state.get("answers", [])

    def build_vacancy():
        state.vacancy_edit = generate.generate_vacancy(state.request, answers)
        state.answers_used = [dict(a) for a in answers]
        state.pop("check", None)

    if "vacancy_edit" not in state:
        try:
            with st.spinner("GigaChat пишет вакансию по шаблону компании…"):
                build_vacancy()
        except llm.LLMError as error:
            st.error(str(error))
            st.stop()

    if state.answers_used != answers:
        st.warning("После сборки появились новые ответы руководителя. Нажмите «Собрать заново», чтобы их учесть.")
    if state.answers_used:
        with st.expander(f"Учтены ответы руководителя: {len(state.answers_used)}"):
            for a in state.answers_used:
                st.markdown(f"- **{a['topic']}** → {a['answer']}")

    text = st.text_area("Текст вакансии — можно редактировать", key="vacancy_edit", height=540)
    col1, col2, col3, col4 = st.columns([1.3, 1.3, 1, 1])
    col1.button(
        "↻ Собрать заново",
        on_click=build_vacancy,
        help="ИИ напишет текст заново из заявки и ответов руководителя. Ваши правки в тексте будут заменены.",
    )
    if col2.button("🔍 Проверить на соответствие заявке"):
        state.check = run("Сверяю вакансию с заявкой…", generate.check_vacancy, state.request, state.request_text, answers, text)
    col3.download_button("Скачать .txt", text, file_name="вакансия.txt")
    col4.download_button("Скачать .docx", generate.to_docx(text, "Вакансия"), file_name="вакансия.docx")
    copy_button(text)
    st.caption("«Собрать заново» — заново пишет текст из заявки и ответов (ваши правки заменятся). Правки сохраняются сами, пока вы не нажмёте эту кнопку.")

    if "check" in state:
        check = state.check
        problems = check.get("unsupported", []) + check.get("distorted", []) + check.get("leaked", [])
        problems += check.get("missing", [])
        if not problems:
            if check.get("demo"):
                st.success("Все пункты заявки на месте, внутренней информации и заготовок нет. Сверка по смыслу — после подключения GigaChat.")
            else:
                st.success("Расхождений с заявкой не найдено: выдуманных фактов, искажённых требований и внутренней информации нет.")
        for title, key in (
            ("Нет в заявке", "unsupported"),
            ("Искажено", "distorted"),
            ("Внутренняя информация", "leaked"),
            ("Пропущено из заявки", "missing"),
        ):
            for item in check.get(key, []):
                st.warning(f"**{title}:** «{item['quote']}» — {item['why']}")

    if state.request.get("do_not_publish"):
        with st.expander("🚫 Что убрано из публикации и почему"):
            for item in state.request["do_not_publish"]:
                st.write(f"- «{item['quote']}» — {item['reason']}")
    nav()
