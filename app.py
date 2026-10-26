"""문검기 (문헌검색기) - 검색어와 기간을 입력해 문헌을 검색하고 엑셀로 정리한다.

실행: run.bat 더블클릭 (또는 .venv\\Scripts\\streamlit run app.py)
"""
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from core import excel, koreamed, settings, summarizer

# 사이트 추가 순서: KoreaMed → RISS → 국회도서관 → PubMed
SOURCES = {
    "KoreaMed": koreamed,
    "RISS": None,
    "국회도서관": None,
    "PubMed": None,
}
MANUAL = summarizer.MANUAL_PROVIDER

st.set_page_config(page_title="문검기", page_icon="📚", layout="wide")


def _autosave(widget_key: str, apply):
    """입력값이 바뀌면 바로 settings.json에 저장 (저장 버튼 없음)."""
    def callback():
        cfg = settings.load()
        value = st.session_state[widget_key]
        apply(cfg, value.strip() if isinstance(value, str) else value)
        settings.save(cfg)
    return callback


def sidebar() -> dict:
    cfg = settings.load()
    st.sidebar.header("⚙️ 설정")
    st.sidebar.caption("입력한 설정은 이 PC(settings.json)에 자동 저장됩니다.")

    st.sidebar.subheader("AI 한글 번역")
    modes = list(summarizer.MODES)
    mode = st.sidebar.radio(
        "AI 작업", modes, format_func=summarizer.MODES.get,
        index=modes.index(cfg.get("mode", summarizer.TRANSLATE)),
        key="cfg_mode", on_change=_autosave("cfg_mode", lambda c, v: c.update(mode=v)))

    names = [summarizer.FREE_PROVIDER, MANUAL] + [p for p in summarizer.PROVIDERS if p != summarizer.FREE_PROVIDER]
    provider = st.sidebar.selectbox(
        "AI 선택", names, index=names.index(cfg["provider"]) if cfg["provider"] in names else 0,
        key="cfg_provider", on_change=_autosave("cfg_provider", lambda c, v: c.update(provider=v)))

    opts = {"provider": provider, "mode": mode, "model": "", "api_key": "", "endpoint": "", "chat": ""}
    if provider == MANUAL:
        chats = list(summarizer.CHAT_LIMITS)
        opts["chat"] = st.sidebar.selectbox(
            "붙여 넣을 채팅", chats, index=chats.index(cfg.get("chat")) if cfg.get("chat") in chats else 0,
            key="cfg_chat", on_change=_autosave("cfg_chat", lambda c, v: c.update(chat=v)))
        st.sidebar.caption("API 키 없이 무료 웹 채팅을 씁니다. 문검기가 만든 질문을 복사해 채팅에 붙여 넣고, "
                           "AI 답변을 다시 문검기에 붙여 넣는 방식입니다. 채팅별 글자 수 한도에 맞춰 나눠 드립니다.")
        if opts["chat"].startswith("Copilot - 개인"):
            st.sidebar.warning("개인 Copilot은 한 번에 약 4,000자만 입력돼 논문 1건씩 보내야 합니다. "
                               "회사 계정 Copilot이나 ChatGPT를 권장합니다.")
    else:
        info = summarizer.PROVIDERS[provider]
        k = f"cfg_model_{provider}"
        opts["model"] = st.sidebar.text_input(
            "모델", value=cfg["models"].get(provider, info["default_model"]), help=info["model_help"],
            key=k, on_change=_autosave(k, lambda c, v, p=provider: c["models"].__setitem__(p, v))).strip()
        k = f"cfg_key_{provider}"
        opts["api_key"] = st.sidebar.text_input(
            "API 키", value=cfg["api_keys"].get(provider) or os.environ.get(info["env"], ""), type="password",
            help="입력 후 Enter 키를 누르면 저장됩니다. (암호화 없이 이 PC에만 저장)",
            key=k, on_change=_autosave(k, lambda c, v, p=provider: c["api_keys"].__setitem__(p, v))).strip()
        if info.get("needs_endpoint"):
            opts["endpoint"] = st.sidebar.text_input(
                "Azure 엔드포인트", value=cfg.get("azure_endpoint", ""),
                placeholder="https://<리소스명>.openai.azure.com/",
                key="cfg_endpoint",
                on_change=_autosave("cfg_endpoint", lambda c, v: c.update(azure_endpoint=v))).strip()
        if provider == summarizer.FREE_PROVIDER:
            st.sidebar.caption("Google 계정으로 https://aistudio.google.com/apikey 에서 무료 API 키를 받으세요. "
                               "무료 한도(분당 약 10회) 때문에 번역은 천천히 진행됩니다.")
        if settings.load()["api_keys"].get(provider):
            st.sidebar.caption(f"✅ 이 PC에 저장된 API 키가 있습니다.")
        elif not os.environ.get(info["env"]):
            st.sidebar.caption("⚠️ 저장된 API 키가 없습니다.")
        if st.sidebar.button("연결 테스트", use_container_width=True):
            with st.sidebar, st.spinner("AI에 시험 요청 중..."):
                ok, msg = summarizer.test_connection(provider, opts["model"], opts["api_key"], opts["endpoint"])
            (st.sidebar.success if ok else st.sidebar.error)(msg)

    st.sidebar.subheader("저장 위치")
    opts["output_dir"] = st.sidebar.text_input(
        "엑셀 저장 폴더", value=cfg["output_dir"],
        key="cfg_output", on_change=_autosave("cfg_output", lambda c, v: c.update(output_dir=v))).strip()
    return opts


def results_table(articles, ai_mode: str | None) -> pd.DataFrame:
    rows = []
    for n, a in enumerate(excel.sort_articles(articles), 1):
        row = {"번호": n, "연도": a.year, "월": a.month, "논문명 (원문)": a.title}
        if ai_mode:
            row["논문명 (한글)"] = a.title_ko
        row["서지정보"] = a.citation
        if ai_mode:
            row["연구유형"] = a.study_type
            row[summarizer.ABSTRACT_HEADERS[ai_mode]] = a.abstract_ko
        else:
            row["초록"] = "있음" if a.has_abstract else "없음"
        row["링크"] = a.url
        rows.append(row)
    return pd.DataFrame(rows)


def show_table(df: pd.DataFrame, site: str):
    st.dataframe(df, hide_index=True, use_container_width=True,
                 column_config={"링크": st.column_config.LinkColumn("링크", display_text=site),
                                "연도": st.column_config.NumberColumn(format="%d")})


def manual_panel(key: str, result: dict, opts: dict) -> bool:
    """웹 채팅 복사·붙여넣기 진행 화면. 모든 묶음을 마쳤으면 True."""
    mode, chat = opts["mode"], opts["chat"]
    plan_id = (mode, chat)
    if result.get("manual_plan") != plan_id:  # 작업 종류나 채팅이 바뀌면 처음부터
        articles = excel.sort_articles(result["articles"])
        result["manual_plan"] = plan_id
        result["manual_batches"] = summarizer.manual_batches(articles, summarizer.CHAT_LIMITS[chat], mode)
        result["manual_done"] = set()
    batches, done = result["manual_batches"], result["manual_done"]

    st.markdown(f"**웹 채팅 복사·붙여넣기** · {chat} · 총 {len(batches)}회")
    st.progress(len(done) / len(batches), text=f"{len(done)}/{len(batches)}회 완료")
    pending = [i for i in range(len(batches)) if i not in done]
    if not pending:
        st.success("모든 논문의 AI 답변을 반영했습니다. 아래 ‘엑셀 만들기’를 누르세요.")
        return True

    i = pending[0]
    batch = batches[i]
    prompt = summarizer.manual_prompt(batch, mode)
    st.markdown(f"**{i + 1}번째** ({len(batch)}건, {len(prompt):,}자)")
    st.markdown("① 오른쪽 위 복사 아이콘으로 아래 질문을 복사 → ② 채팅에 붙여 넣고 전송 → "
                "③ AI 답변 전체를 복사해 아래 칸에 붙여 넣기 → ④ ‘답변 반영’")
    if len(prompt) > summarizer.CHAT_LIMITS[chat]:
        st.warning("이 논문은 초록이 길어 채팅 글자 수 한도를 넘을 수 있습니다. 잘리면 ChatGPT나 Gemini를 사용하세요.")
    st.code(prompt, language=None, height=220)
    answer = st.text_area("AI 답변 붙여넣기", key=f"{key}answer_{mode}_{i}", height=180)
    if st.button("✅ 답변 반영", key=f"{key}apply_{mode}_{i}"):
        try:
            filled, missing = summarizer.apply_manual_answer(batch, answer)
        except Exception as e:  # noqa: BLE001 - 형식이 깨진 답변
            st.error(f"답변을 읽지 못했습니다. AI 답변 전체(대괄호 [ ] 포함)를 붙여 넣었는지 확인하세요. ({e})")
            return False
        if missing:
            st.warning(f"{filled}건 반영, {len(missing)}건 누락 (ID: {', '.join(missing)}). "
                       "누락분은 빈칸으로 남습니다.")
        done.add(i)
        st.rerun()
    return False


def source_tab(site: str, module, opts: dict):
    key = f"{site}_"
    st.subheader(f"{site} 검색")

    c1, c2, c3 = st.columns([3, 1, 1])
    terms_text = c1.text_input("검색어 (제품명·성분명)", key=key + "terms",
                               placeholder="예: pioglitazone   ·  동의어는 쉼표로 구분: thioctic acid, lipoic acid")
    this_year = date.today().year
    year_from = c2.number_input("시작연도", 1950, this_year, 2021, key=key + "y1")
    year_to = c3.number_input("종료연도", 1950, this_year + 1, this_year, key=key + "y2")

    if st.button("🔍 검색", key=key + "search", type="primary"):
        terms = [t.strip() for t in terms_text.split(",") if t.strip()]
        if not terms:
            st.warning("검색어를 입력하세요.")
        elif year_from > year_to:
            st.warning("시작연도가 종료연도보다 늦습니다.")
        else:
            bar = st.progress(0.0, text="검색 중...")
            try:
                query, articles = module.search(
                    terms, int(year_from), int(year_to),
                    progress=lambda d, t: bar.progress(min(d / max(t, 1), 1.0), text=f"결과 가져오는 중 {d}/{t}"))
            except Exception as e:  # noqa: BLE001
                bar.empty()
                st.error(f"검색 중 오류가 발생했습니다: {e}")
                return
            bar.empty()
            st.session_state[key + "result"] = {
                "terms": terms, "query": query, "articles": articles,
                "year_from": int(year_from), "year_to": int(year_to),
                "today": date.today(), "ai_mode": None, "saved": None,
            }

    result = st.session_state.get(key + "result")
    if not result:
        return

    articles = result["articles"]
    st.markdown(f"**검색식:** `{result['query']}`  ·  **검색일:** {result['today']:%Y-%m-%d}")
    if not articles:
        st.info("검색 결과가 없습니다.")
        return

    years = pd.Series([a.year for a in articles]).value_counts().sort_index()
    m1, m2, m3 = st.columns(3)
    m1.metric("검색 결과", f"{len(articles)}건")
    m2.metric("초록 있음", f"{sum(a.has_abstract for a in articles)}건")
    m3.metric("초록 없음", f"{sum(not a.has_abstract for a in articles)}건")
    st.caption("연도별: " + " · ".join(f"{int(y)}년 {c}건" for y, c in years.items()))

    mode, provider = opts["mode"], opts["provider"]
    manual = provider == MANUAL
    shown_mode = result["ai_mode"] or (mode if manual and result.get("manual_done") else None)
    show_table(results_table(articles, shown_mode), site)

    st.divider()
    st.subheader("📄 엑셀 파일 만들기")
    use_ai = st.checkbox(f"AI {summarizer.MODES[mode]} 포함 ({provider})", value=True, key=key + "ai")
    manual_ready = True
    if use_ai and manual:
        manual_ready = manual_panel(key, result, opts)
        st.divider()

    e1, e2 = st.columns([2, 2])
    name = e1.text_input("파일명에 쓸 검색어명", value=result["terms"][0], key=key + "name")
    subfolder = e2.text_input("하위 폴더 (선택, 예: 그리타존정(피오글리타존))", key=key + "sub")
    filename = excel.make_filename(name or result["terms"][0], result["year_from"], result["year_to"],
                                   module.FILE_TAG, result["today"])
    folder = Path(opts["output_dir"]) / subfolder if subfolder else Path(opts["output_dir"])
    st.caption(f"저장 위치: {folder / filename}")
    if use_ai and manual and not manual_ready:
        st.caption("※ 복사·붙여넣기를 다 마치지 않고 저장하면, 남은 논문의 한글 칸은 비어 있습니다.")

    if st.button("💾 엑셀 만들기", key=key + "make", type="primary"):
        if use_ai and not manual and result["ai_mode"] != mode:
            if not opts["api_key"] or not opts["model"]:
                st.error("왼쪽 설정에서 AI 모델과 API 키를 입력하세요. (AI 없이 저장하려면 체크를 해제)")
                return
            label = summarizer.MODES[mode]
            bar = st.progress(0.0, text=f"AI {label} 중...")
            errors, used_models = summarizer.summarize(
                articles, provider, opts["model"], opts["api_key"], opts["endpoint"], mode=mode,
                progress=lambda d, t: bar.progress(d / t, text=f"AI {label} 중 {d}/{t}"))
            bar.empty()
            if errors:
                st.warning("일부 논문 처리에 실패했습니다 (해당 칸은 비어 있음):\n\n" + "\n".join(f"- {e}" for e in errors))
            result["ai_mode"] = mode
            result["ai_name"] = f"{provider} · {', '.join(used_models) or opts['model']}"
        elif use_ai and manual and result.get("manual_done"):
            result["ai_mode"] = mode
            result["ai_name"] = f"웹 채팅 · {opts['chat']}"

        if use_ai and result["ai_mode"]:
            ai_note = (f"논문명·초록 한글 {('번역' if result['ai_mode'] == summarizer.TRANSLATE else '요약')}: "
                       f"AI({result.get('ai_name', '')}) 작성 — 원문과 대조 확인 필요. "
                       f"단독 저자는 et al. 생략. 초록이 없는 문헌은 '{summarizer.NO_ABSTRACT}'.")
            header = summarizer.ABSTRACT_HEADERS[result["ai_mode"]]
        else:
            ai_note, header = "AI 번역 없음 (원문만 수록).", summarizer.ABSTRACT_HEADERS[mode]
        try:
            path = excel.write_workbook(
                excel.unique_path(folder, filename), articles,
                search_name=name or result["terms"][0], site=site, query=result["query"],
                year_from=result["year_from"], year_to=result["year_to"], today=result["today"],
                ai_note=ai_note, abstract_header=header)
        except PermissionError:
            st.error("파일을 저장할 수 없습니다. 같은 이름의 엑셀 파일이 열려 있으면 닫고 다시 시도하세요.")
            return
        result["saved"] = str(path)
        st.rerun()

    if result.get("saved"):
        path = Path(result["saved"])
        st.success(f"저장 완료: {path}")
        d1, d2 = st.columns([1, 1])
        if path.exists():
            d1.download_button("⬇️ 엑셀 내려받기", path.read_bytes(), file_name=path.name, key=key + "dl")
        if d2.button("📂 폴더 열기", key=key + "open"):
            os.startfile(path.parent)


def main():
    st.title("📚 문검기 (문헌검색기)")
    st.caption("검색어와 기간을 입력해 문헌을 검색하고, 결과를 확인한 뒤 엑셀 파일로 정리합니다.")
    opts = sidebar()
    tabs = st.tabs(list(SOURCES))
    for tab, (site, module) in zip(tabs, SOURCES.items()):
        with tab:
            if module is None:
                st.info(f"{site} 검색은 준비 중입니다.")
            else:
                source_tab(site, module, opts)


main()
