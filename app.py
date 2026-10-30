import hashlib
import json
import os

import pandas as pd
import streamlit as st

import compare
import generate
import llm
import parse

SAMPLE_REQUEST = os.path.join(os.path.dirname(__file__), "data", "sample_request.docx")
STEPS = ["Заявка", "Аналитика рынка", "Вакансия"]
CITY = "Ижевск"
SECTIONS = (("ed_duties", "duties", "Обязанности"), ("ed_requirements", "requirements", "Требования"),
            ("ed_conditions", "conditions", "Условия"), ("ed_skills", "skills", "Ключевые навыки"))

st.set_page_config(page_title="HR-помощник: вакансия", page_icon="📋", layout="wide", initial_sidebar_state="collapsed")
state = st.session_state
state.setdefault("step", 0)
for key in list(state.keys()):
    if key.startswith("ed_") or key in ("request_text", "editing"):
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


def copy_button(text, label="📋 Скопировать"):
    st.iframe(
        f"""
<style>
body {{margin: 0;}}
#copy {{width: 100%; height: 40px; border: 1px solid rgba(49, 51, 63, 0.2); border-radius: 0.5rem; background: #fff;
  font: 14px "Source Sans Pro", "Source Sans 3", sans-serif; color: rgb(49, 51, 63); cursor: pointer;}}
#copy:hover {{border-color: #1f5fbf; color: #1f5fbf;}}
</style>
<button id="copy">{label}</button>
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
        height=42,
    )


def examples_block(examples, limit=4):
    for example in examples[:limit]:
        quote = f" — «{example['quote']}»" if example.get("quote") else ""
        st.markdown(f'<div class="example">• <b>{example["company"]}</b>{quote}</div>', unsafe_allow_html=True)
    if len(examples) > limit:
        st.markdown(f'<div class="example">и ещё {len(examples) - limit}</div>', unsafe_allow_html=True)


def analyze(text):
    with st.status("Сравниваем заявку с рынком — обычно около минуты…", expanded=True) as status:
        st.write("① GigaChat разбирает заявку")
        request = parse.parse_request(text)
        st.write("② Отбираем вакансии по должности и городу")
        base = parse.load_base()
        candidates, rejected = parse.prefilter(base, request.get("search_titles", []), CITY)
        st.write(f"③ Читаем вакансии ({len(candidates)})")
        parse.enrich(candidates)
        rows, market_only = [], []
        if candidates:
            st.write("④ GigaChat оценивает похожесть")
            compare.relevance(request, candidates)
            chosen = [v for v in candidates if v["relevant"]]
            if chosen:
                st.write("⑤ GigaChat сравнивает требования")
                rows, market_only = compare.requirements(request, chosen)
        status.update(label="Готово", state="complete")
    return {"request": request, "base": len(base), "candidates": candidates, "rejected": rejected,
            "rows": rows, "market_only": market_only}


def set_editor(vacancy):
    state.ed_title = vacancy["title"]
    for key, field, _ in SECTIONS:
        state[key] = "\n".join(vacancy[field])


top_left, top_right = st.columns([3, 1])
top_left.title("📋 HR-помощник: от заявки к вакансии")
with top_right:
    if llm.enabled():
        st.success(f"GigaChat подключён ({llm.MODEL})")
    else:
        st.warning("Демо-режим: без ключа GigaChat")
    if st.button("Начать заново"):
        state.clear()
        st.rerun()

stepper(state.step)

if state.step == 0:
    head, action = st.columns([3, 1], vertical_alignment="bottom")
    head.subheader("Заявка руководителя")
    compare_clicked = action.button("Сравнить с рынком →", type="primary", width="stretch")
    progress = st.container()
    source = st.radio("Откуда взять заявку", ["Пример из материалов", "Загрузить файл", "Вставить текст"], horizontal=True)
    marker, loaded = (source,), ""
    if source == "Пример из материалов":
        loaded = parse.read_docx(SAMPLE_REQUEST)
    elif source == "Загрузить файл":
        file = st.file_uploader("Заявка руководителя (.docx или .txt)", type=["docx", "txt"])
        if file:
            marker, loaded = (source, file.name, file.size), parse.read_upload(file)
    if state.get("loaded_marker") != marker:
        state.request_text = loaded
        state.loaded_marker = marker
    text = st.text_area("Текст заявки — можно править прямо здесь", key="request_text", height=460)
    st.caption("Поправили заявку после аналитики — нажмите «Сравнить» ещё раз, результат пересчитается.")

    if compare_clicked and not text.strip():
        progress.warning("Заявка пустая — вставьте текст или загрузите файл.")
    elif compare_clicked:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if state.get("analysis", {}).get("digest") != digest:
            with progress:
                try:
                    state.analysis = {**analyze(text), "digest": digest}
                except llm.LLMError as error:
                    st.error(str(error))
                    st.stop()
        go(1)

elif state.step == 1:
    analysis = state.analysis
    request = analysis["request"]
    chosen = [v for v in analysis["candidates"] if v["relevant"]]
    if not chosen:
        st.warning("Похожих вакансий в базе не нашлось — проверьте название должности в заявке.")
    else:
        st.markdown(
            f"Сравнили заявку **«{request['profile']['title']}»** с **{len(chosen)}** похожими вакансиями "
            f"из {analysis['base']} в базе (город — {CITY})."
        )
        stats = compare.salary_stats(chosen)
        if stats:
            values = f"{stats['min']:,.0f} ₽" if stats["min"] == stats["max"] else f"{stats['min']:,.0f} – {stats['max']:,.0f} ₽"
            st.markdown(
                f"Зарплата указана в {stats['count']} из {stats['total']}: {values.replace(',', ' ')} "
                f"({'; '.join(stats['sources'])})"
            )
        with st.expander("С какими вакансиями сравнивали"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Похожесть": v["score"],
                            "Должность": v["meta"].get("Должность", ""),
                            "Компания": compare.company(v),
                            "Зарплата": v["meta"].get("Зарплата", ""),
                            "Почему": v["reason"],
                            "Ссылка": v["meta"].get("Ссылка", ""),
                        }
                        for v in sorted(chosen, key=lambda v: -v["score"])
                    ]
                ),
                hide_index=True,
                width="stretch",
                column_config={
                    "Похожесть": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
                    "Ссылка": st.column_config.LinkColumn(display_text="открыть"),
                },
            )
            skipped = [(v, f"похожесть {v['score']}%: {v['reason']}") for v in analysis["candidates"] if not v["relevant"]]
            skipped += analysis["rejected"]
            if skipped:
                st.caption("Не подошли: " + "; ".join(f"{v['meta'].get('Должность', '')} ({reason})" for v, reason in skipped))

        market_items = compare.market_vs_ours(request, chosen, analysis["market_only"])
        ours_items = compare.ours_vs_market(analysis["rows"])

        def cards(items, empty):
            if not items:
                st.success(empty)
            for item in items:
                with st.container(border=True):
                    st.markdown(f"**{item['title']}**")
                    st.caption(item["fact"])
                    if item.get("market"):
                        st.markdown(f"На рынке обычно: *{item['market']}*")
                    examples_block(item["examples"])
                    st.markdown(f"❓ **Вопрос руководителю:** {item['question']}")

        left, right = st.columns(2, gap="large")
        with left:
            st.subheader("Есть на рынке — нет у нас")
            st.caption("Что похожие вакансии указывают, а в заявке этого нет или сказано общими словами.")
            cards(market_items, "Всё, что обычно указывают на рынке, в заявке есть.")
        with right:
            st.subheader("Есть у нас — на рынке иначе")
            st.caption("Требования заявки, которые строже, чем у похожих вакансий, или почти не встречаются.")
            cards(ours_items, "Требования заявки совпадают с рынком.")

        st.info("Хотите что-то учесть — вернитесь к заявке, поправьте текст и сравните заново.")
        letter = generate.questions_letter(request["profile"]["title"], market_items, ours_items)
        with st.expander(f"✉️ Вопросы руководителю ({len(market_items) + len(ours_items)})"):
            st.text(letter)
            col1, col2, _ = st.columns([1, 1, 2])
            with col1:
                copy_button(letter, "📋 Скопировать вопросы")
            col2.download_button("Скачать .txt", letter, file_name="вопросы_руководителю.txt")

    st.divider()
    left, _, right = st.columns([1, 2, 1])
    if left.button("← Поправить заявку", width="stretch"):
        go(0)
    if right.button("Сформировать вакансию →", type="primary", disabled=not chosen, width="stretch"):
        go(2)

else:
    analysis = state.analysis
    request = analysis["request"]

    def build():
        state.vacancy = generate.generate_vacancy(request)
        state.vacancy_for = analysis["digest"]
        set_editor(state.vacancy)

    if state.get("vacancy_for") != analysis["digest"]:
        try:
            with st.spinner("GigaChat оформляет вакансию в формате компании…"):
                build()
        except llm.LLMError as error:
            st.error(str(error))
            st.stop()
    vacancy = state.vacancy

    if st.toggle("✏️ Редактировать текст", key="editing"):
        with st.container(border=True):
            st.caption("Каждый пункт — с новой строки. Предпросмотр ниже обновляется сразу.")
            vacancy["title"] = st.text_input("Должность", key="ed_title")
            col1, col2 = st.columns(2)
            for i, (key, field, label) in enumerate(SECTIONS):
                value = (col1 if i % 2 == 0 else col2).text_area(label, key=key, height=200)
                vacancy[field] = [line.strip(" -•") for line in value.splitlines() if line.strip(" -•")]

    with st.container(border=True):
        st.html(f"<style>{generate.CSS}</style>{generate.render_html(vacancy)}")

    col1, col2, col3, col4 = st.columns(4)
    col1.download_button(
        "⬇️ Скачать HTML", generate.html_document(vacancy), file_name="вакансия.html", mime="text/html", width="stretch"
    )
    col2.download_button("⬇️ Скачать Word", generate.to_docx(vacancy), file_name="вакансия.docx", width="stretch")
    with col3:
        copy_button(generate.hh_html(vacancy), "📋 Скопировать для hh.ru")
    col4.button("↻ Сформировать заново", width="stretch", on_click=build, help="GigaChat оформит вакансию заново; правки пропадут")
    st.caption("PDF: откройте скачанный HTML и нажмите «Печать / сохранить в PDF». «Скопировать для hh.ru» — описание в разметке hh.")

    if request.get("do_not_publish"):
        st.subheader("🚫 Не попало в вакансию")
        st.caption("Это было в заявке, но кандидатам показывать нельзя.")
        for item in request["do_not_publish"]:
            quote = item["quote"].replace("\n", " ")
            quote = quote if len(quote) <= 160 else quote[:157] + "…"
            result = f" → в вакансии: *{item['suggestion']}*" if item.get("suggestion") else ""
            st.warning(f"«{quote}» — {item['reason'].rstrip('.')}{result}")

    st.divider()
    left, _ = st.columns([1, 3])
    if left.button("← К аналитике", width="stretch"):
        go(1)
