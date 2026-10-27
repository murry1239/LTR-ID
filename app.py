"""문검기 (문헌검색기) - 검색어와 기간을 입력해 문헌을 검색하고 엑셀로 정리한다.

실행: run.bat 더블클릭 (또는 .venv\\Scripts\\streamlit run app.py)
"""
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import time

from core import excel, koreamed, nanet, riss, settings, summarizer
from core.log import LOG_DIR, get_logger, tail

log = get_logger()

# 사이트 추가 순서: KoreaMed → RISS → 국회도서관 → PubMed
SOURCES = {
    "KoreaMed": koreamed,
    "RISS": riss,
    "국회도서관": nanet,
    "PubMed": None,
}
MANUAL = summarizer.MANUAL_PROVIDER
CUSTOM_MODEL = "✏️ 직접 입력"
APP_VERSION = "0.5"
LOG_HINT = "\n\n🧾 자세한 내용은 왼쪽 아래 ‘로그 보기’에서 확인하세요."

st.set_page_config(page_title="문검기", page_icon="📚", layout="wide")


def _autosave(widget_key: str, apply):
    """입력값이 바뀌면 바로 settings.json에 저장 (저장 버튼 없음)."""
    def callback():
        cfg = settings.load()
        value = st.session_state[widget_key]
        apply(cfg, value.strip() if isinstance(value, str) else value)
        settings.save(cfg)
    return callback


def model_picker(cfg: dict, provider: str, info: dict, api_key: str) -> str:
    """번역 모델 선택: 불러온 모델 목록(없으면 추천 모델) 중에서 고르거나 직접 입력."""
    saved = cfg["models"].get(provider, info["default_model"])
    listed = cfg.get("model_lists", {}).get(provider) or info.get("suggested", [])
    options = listed + ([saved] if saved and saved not in listed else []) + [CUSTOM_MODEL]
    k = f"cfg_modelpick_{provider}"
    choice = st.sidebar.selectbox(
        "번역 모델", options, index=options.index(saved) if saved in options else 0, key=k,
        help="‘사용 가능한 모델 불러오기’를 누르면 API 키로 쓸 수 있는 모델이 모두 표시됩니다.",
        on_change=_autosave(k, lambda c, v, p=provider: v != CUSTOM_MODEL and c["models"].__setitem__(p, v)))
    if choice == CUSTOM_MODEL:
        k = f"cfg_model_{provider}"
        model = st.sidebar.text_input(
            "모델 이름", value=saved, help=info["model_help"],
            key=k, on_change=_autosave(k, lambda c, v, p=provider: c["models"].__setitem__(p, v)))
    else:
        model = choice

    if info.get("suggested") and st.sidebar.button("🔄 사용 가능한 모델 불러오기", use_container_width=True,
                                                   disabled=not api_key, key=f"list_{provider}"):
        with st.sidebar, st.spinner("모델 목록 불러오는 중..."):
            try:
                names = summarizer.list_models(provider, api_key)
                log.info("모델 목록 불러오기 (%s): %d개", provider, len(names))
            except Exception as e:  # noqa: BLE001
                log.exception("모델 목록 불러오기 실패 (%s)", provider)
                st.sidebar.error(f"목록을 불러오지 못했습니다: {summarizer.explain_error(e)}{LOG_HINT}")
                names = []
        if names:
            cfg = settings.load()
            cfg.setdefault("model_lists", {})[provider] = names
            settings.save(cfg)
            st.rerun()
    return model.strip()


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

    # 기본값: Gemini 무료 (목록 첫 번째)
    names = [summarizer.FREE_PROVIDER, MANUAL] + [p for p in summarizer.PROVIDERS if p != summarizer.FREE_PROVIDER]
    provider = st.sidebar.selectbox(
        "AI 선택", names, index=names.index(cfg["provider"]) if cfg["provider"] in names else 0,
        format_func=summarizer.label, help="🆓 무료 2가지, 💳 유료 API 3가지 (ChatGPT·Claude·Gemini)",
        key="cfg_provider", on_change=_autosave("cfg_provider", lambda c, v: c.update(provider=v)))

    opts = {"provider": provider, "mode": mode, "model": "", "api_key": "", "endpoint": "", "chat": "",
            "fallback": True}
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
        saved_key = cfg["api_keys"].get(provider)
        if not saved_key and provider == summarizer.PAID_GEMINI:  # 무료로 쓰던 키에 결제를 붙인 경우가 많음
            saved_key = cfg["api_keys"].get(summarizer.FREE_PROVIDER)
        k = f"cfg_key_{provider}"
        opts["api_key"] = st.sidebar.text_input(
            "API 키", value=saved_key or os.environ.get(info["env"], ""), type="password",
            help="입력 후 Enter 키를 누르면 저장됩니다. (암호화 없이 이 PC에만 저장)",
            key=k, on_change=_autosave(k, lambda c, v, p=provider: c["api_keys"].__setitem__(p, v))).strip()
        st.sidebar.caption(f"API 키 발급: {info['key_url']}")
        opts["model"] = model_picker(cfg, provider, info, opts["api_key"])
        opts["fallback"] = True
        if info.get("fallbacks"):
            opts["fallback"] = st.sidebar.checkbox(
                "선택한 모델이 붐비면 다른 모델로 자동 전환", value=cfg.get("fallback", True),
                help=" → ".join(info["fallbacks"]) + " 순서로 시도합니다.",
                key="cfg_fallback", on_change=_autosave("cfg_fallback", lambda c, v: c.update(fallback=v)))
        if provider == summarizer.FREE_PROVIDER:
            st.sidebar.caption("무료 한도(분당 약 10회) 때문에 번역은 천천히 진행됩니다.")
        else:
            st.sidebar.caption("💳 유료 API는 사용량만큼 요금이 나갑니다. (ChatGPT Plus·Claude Pro·Gemini Advanced "
                               "같은 채팅 구독과는 별개로, 각 회사 API 결제 설정이 필요합니다)")
        if settings.load()["api_keys"].get(provider) or (saved_key and opts["api_key"] == saved_key):
            st.sidebar.caption(f"✅ 이 PC에 저장된 API 키가 있습니다.")
        elif not os.environ.get(info["env"]):
            st.sidebar.caption("⚠️ 저장된 API 키가 없습니다.")
        if st.sidebar.button("연결 테스트", use_container_width=True):
            with st.sidebar, st.spinner("AI에 시험 요청 중..."):
                ok, msg = summarizer.test_connection(provider, opts["model"], opts["api_key"],
                                                     fallback=opts["fallback"])
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
            log.info("웹 채팅 답변 반영: %d번째 묶음, %d건 반영, 누락 %s", i + 1, filled, missing or "없음")
        except Exception as e:  # noqa: BLE001 - 형식이 깨진 답변
            log.warning("웹 채팅 답변 읽기 실패 (%d번째 묶음): %s / 답변 앞부분: %r", i + 1, e, answer[:200])
            st.error(f"답변을 읽지 못했습니다. AI 답변 전체(대괄호 [ ] 포함)를 붙여 넣었는지 확인하세요. ({e})")
            return False
        if missing:
            st.warning(f"{filled}건 반영, {len(missing)}건 누락 (ID: {', '.join(missing)}). "
                       "누락분은 빈칸으로 남습니다.")
        done.add(i)
        st.rerun()
    return False


def _split_terms(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def _show_group(site: str, group: dict, articles: list, shown_mode):
    st.markdown(f"#### 🔎 {', '.join(group['terms'])}")
    st.caption(f"검색식: {group['query']}")
    if not articles:
        st.info("검색 결과 0건 — 엑셀에는 검색 조건과 0건 결과가 기록됩니다.")
        return
    years = pd.Series([a.year for a in articles]).value_counts().sort_index()
    m1, m2, m3 = st.columns(3)
    m1.metric("검색 결과", f"{len(articles)}건")
    m2.metric("초록 있음", f"{sum(a.has_abstract for a in articles)}건")
    m3.metric("초록 없음", f"{sum(not a.has_abstract for a in articles)}건")
    st.caption("연도별: " + " · ".join(f"{int(y)}년 {c}건" for y, c in years.items() if pd.notna(y)))
    show_table(results_table(articles, shown_mode), site)


def _search_inputs(key: str, module):
    """검색어 입력 화면 → (실행할 작업 목록, 시작연도, 종료연도, 버튼 눌림)."""
    second_label = getattr(module, "SECOND_QUERY_LABEL", None)
    first_label = getattr(module, "FIRST_QUERY_LABEL", "검색어 (제품명·성분명)")
    cols = st.columns([3, 3, 1, 1]) if second_label else st.columns([3, 1, 1])
    terms_text = cols[0].text_input(
        first_label, key=key + "terms",
        placeholder=("예: 피오글리타존  ·  동의어는 쉼표로: 티옥트산, 치옥트산" if second_label else
                     "예: pioglitazone   ·  동의어는 쉼표로 구분: thioctic acid, lipoic acid"))
    second_text = cols[1].text_input(second_label, key=key + "terms2",
                                     placeholder="예: pioglitazone") if second_label else ""
    this_year = date.today().year
    year_from = int(cols[-2].number_input("시작연도", 1950, this_year, 2021, key=key + "y1"))
    year_to = int(cols[-1].number_input("종료연도", 1950, this_year + 1, this_year, key=key + "y2"))
    if second_label:
        st.caption("한글 검색어와 영문 검색어를 함께 넣으면 한 번에 검색하고, 엑셀은 검색어별로 따로 만듭니다.")
    clicked = st.button("🔍 검색", key=key + "search", type="primary")
    jobs = [(terms, lambda progress, t=terms: module.search(t, year_from, year_to, progress=progress))
            for terms in (_split_terms(terms_text), _split_terms(second_text)) if terms]
    return jobs, year_from, year_to, clicked


def _upload_inputs(key: str, module):
    """결과 파일 올리기 화면 (국회도서관처럼 직접 검색이 막힌 사이트)."""
    st.markdown(module.GUIDE)
    this_year = date.today().year
    slots = []
    for col, label, n, example in zip(st.columns(2), (module.FIRST_QUERY_LABEL, module.SECOND_QUERY_LABEL),
                                      ("1", "2"), ("메트포르민", "metformin")):
        with col:
            name = st.text_input(f"{label} (파일명에 사용)", key=f"{key}uname{n}", placeholder=f"예: {example}")
            file = st.file_uploader(f"{label} 검색결과 파일 (.xls)", type=["xls", "xlsx"], key=f"{key}file{n}")
            slots.append((name.strip(), file))
    y1, y2, _ = st.columns([1, 1, 4])
    year_from = int(y1.number_input("시작연도", 1950, this_year, 2021, key=key + "y1"))
    year_to = int(y2.number_input("종료연도", 1950, this_year + 1, this_year, key=key + "y2"))
    st.caption("파일의 논문을 제목으로 RISS·PubMed에서 찾아 저자·학술지·초록을 채웁니다 (100건에 1~2분).")
    clicked = st.button("📥 불러와서 정리", key=key + "search", type="primary")
    jobs = []
    for name, file in slots:
        if file is None:
            continue
        data, filename = file.getvalue(), file.name
        jobs.append(([name or Path(filename).stem],
                     lambda progress, d=data, f=filename: module.load(d, f, year_from, year_to, progress=progress)))
    return jobs, year_from, year_to, clicked


def source_tab(site: str, module, opts: dict):
    key = f"{site}_"
    upload = getattr(module, "UPLOAD", False)
    st.subheader(f"{site} {'검색결과 정리' if upload else '검색'}")
    jobs, year_from, year_to, clicked = (_upload_inputs if upload else _search_inputs)(key, module)

    if clicked:
        if not jobs:
            st.warning("검색결과 파일을 올리세요." if upload else "검색어를 입력하세요.")
        elif year_from > year_to:
            st.warning("시작연도가 종료연도보다 늦습니다.")
        else:
            groups, pool = [], {}
            for terms, run in jobs:
                label = f"[{', '.join(terms)}] {'정리' if upload else '검색'} 중"
                bar = st.progress(0.0, text=label + "...")
                log.info("[%s] %s 시작: %s, %d–%d", site, "파일 정리" if upload else "검색", terms, year_from, year_to)
                started = time.monotonic()
                try:
                    query, found = run(lambda d, t, lb=label: bar.progress(min(d / max(t, 1), 1.0), text=f"{lb} {d}/{t}"))
                except Exception as e:  # noqa: BLE001
                    bar.empty()
                    log.exception("[%s] 실패: %s", site, terms)
                    st.error(f"{'파일 정리' if upload else '검색'} 중 오류가 발생했습니다: {type(e).__name__} {e}{LOG_HINT}")
                    return
                bar.empty()
                log.info("[%s] 완료: %s → %d건 (%.0f초) · %s", site, terms, len(found),
                         time.monotonic() - started, query)
                for a in found:  # 한글·영문 검색에 같은 논문이 나오면 한 번만 번역하도록 공유
                    pool.setdefault(a.source_id, a)
                groups.append({"terms": terms, "query": query, "ids": [a.source_id for a in found]})
            st.session_state[key + "result"] = {
                "groups": groups, "pool": pool, "articles": list(pool.values()),
                "year_from": year_from, "year_to": year_to,
                "today": date.today(), "ai_mode": None, "saved": [],
            }

    result = st.session_state.get(key + "result")
    if not result:
        return

    articles, pool = result["articles"], result["pool"]
    mode, provider = opts["mode"], opts["provider"]
    manual = provider == MANUAL
    shown_mode = result["ai_mode"] or (mode if manual and result.get("manual_done") else None)
    st.caption(f"검색일: {result['today']:%Y-%m-%d}")
    for group in result["groups"]:
        _show_group(site, group, [pool[i] for i in group["ids"]], shown_mode)

    st.divider()
    st.subheader("📄 엑셀 파일 만들기")
    use_ai = bool(articles) and st.checkbox(f"AI {summarizer.MODES[mode]} 포함 ({summarizer.label(provider)})",
                                            value=True, key=key + "ai")
    manual_ready = True
    if use_ai and manual:
        manual_ready = manual_panel(key, result, opts)
        st.divider()

    subfolder = st.text_input("하위 폴더 (선택, 예: 그리타존정(피오글리타존))", key=key + "sub")
    folder = Path(opts["output_dir"]) / subfolder if subfolder else Path(opts["output_dir"])
    names = []
    for i, group in enumerate(result["groups"]):
        name = st.text_input(f"파일명에 쓸 검색어명 ({', '.join(group['terms'])})", value=group["terms"][0],
                             key=f"{key}name_{i}") or group["terms"][0]
        names.append(name)
        filename = excel.make_filename(name, result["year_from"], result["year_to"], module.FILE_TAG, result["today"])
        st.caption(f"저장 위치: {folder / filename}")
    if use_ai and manual and not manual_ready:
        st.caption("※ 복사·붙여넣기를 다 마치지 않고 저장하면, 남은 논문의 한글 칸은 비어 있습니다.")

    if st.button("💾 엑셀 만들기", key=key + "make", type="primary"):
        if use_ai and not manual and result["ai_mode"] != mode:
            if not opts["api_key"] or not opts["model"]:
                log.warning("엑셀 만들기 중단: AI 모델 또는 API 키 없음 (%s)", provider)
                st.error("왼쪽 설정에서 AI 모델과 API 키를 입력하세요. (AI 없이 저장하려면 체크를 해제)")
                return
            label = summarizer.MODES[mode]
            bar = st.progress(0.0, text=f"AI {label} 중...")
            errors, used_models = summarizer.summarize(
                articles, provider, opts["model"], opts["api_key"], opts["endpoint"], mode=mode,
                fallback=opts["fallback"],
                progress=lambda d, t: bar.progress(d / t, text=f"AI {label} 중 {d}/{t}"))
            bar.empty()
            if errors:
                st.warning("일부 논문 처리에 실패했습니다 (해당 칸은 비어 있음):\n\n" +
                           "\n".join(f"- {e}" for e in errors) + LOG_HINT)
            result["ai_mode"] = mode
            result["ai_name"] = f"{summarizer.label(provider)} · {', '.join(used_models) or opts['model']}"
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
        saved = []
        for group, name in zip(result["groups"], names):
            filename = excel.make_filename(name, result["year_from"], result["year_to"],
                                           module.FILE_TAG, result["today"])
            try:
                saved.append(str(excel.write_workbook(
                    excel.unique_path(folder, filename), [pool[i] for i in group["ids"]],
                    search_name=name, site=site, query=group["query"],
                    year_from=result["year_from"], year_to=result["year_to"], today=result["today"],
                    ai_note=f"[문검기 v{APP_VERSION}] {ai_note}", abstract_header=header)))
            except PermissionError:
                log.exception("엑셀 저장 실패 (파일이 열려 있거나 권한 없음): %s", folder / filename)
                st.error(f"{filename} 파일을 저장할 수 없습니다. 같은 이름의 엑셀 파일이 열려 있으면 닫고 다시 시도하세요.")
                return
            except Exception as e:  # noqa: BLE001
                log.exception("엑셀 저장 실패: %s", folder / filename)
                st.error(f"{filename} 저장 중 오류: {type(e).__name__} {e}{LOG_HINT}")
                return
            log.info("[%s] 엑셀 저장: %s (%d건)", site, saved[-1], len(group["ids"]))
        result["saved"] = saved
        st.rerun()

    for n, saved in enumerate(result.get("saved") or []):
        path = Path(saved)
        st.success(f"저장 완료: {path}")
        d1, d2 = st.columns([1, 1])
        if path.exists():
            d1.download_button(f"⬇️ {path.name} 내려받기", path.read_bytes(), file_name=path.name,
                               key=f"{key}dl_{n}")
        if n == 0 and d2.button("📂 폴더 열기", key=key + "open"):
            os.startfile(path.parent)


@st.dialog("🧾 작업 기록 (로그)", width="large")
def log_dialog():
    st.caption(f"최근 300줄 · 파일: {LOG_DIR / 'munkeomgi.log'} · API 키는 가려서 기록합니다.")
    text = tail(300)
    st.code(text, language=None, height=480)
    c1, c2 = st.columns(2)
    c1.download_button("⬇️ 로그 파일 내려받기", text.encode("utf-8"), file_name="munkeomgi_log.txt")
    if c2.button("📂 로그 폴더 열기"):
        os.startfile(LOG_DIR)


def main():
    st.title(f"📚 문검기 (문헌검색기) v{APP_VERSION}")
    st.caption("검색어와 기간을 입력해 문헌을 검색하고, 결과를 확인한 뒤 엑셀 파일로 정리합니다.")
    opts = sidebar()
    st.sidebar.divider()
    if st.sidebar.button("🧾 로그 보기", use_container_width=True, help="오류가 나면 여기서 어느 단계에서 났는지 확인할 수 있습니다."):
        log_dialog()
    tabs = st.tabs(list(SOURCES))
    for tab, (site, module) in zip(tabs, SOURCES.items()):
        with tab:
            if module is None:
                st.info(f"{site} 검색은 준비 중입니다.")
            else:
                source_tab(site, module, opts)


try:
    main()
except Exception as e:  # noqa: BLE001 - 예상하지 못한 오류도 로그에 남기고 화면에 안내
    if type(e).__name__ in ("RerunException", "StopException"):  # Streamlit 내부 흐름 제어
        raise
    log.exception("예상하지 못한 오류")
    st.error(f"예상하지 못한 오류가 발생했습니다: {type(e).__name__} {e}{LOG_HINT}")
