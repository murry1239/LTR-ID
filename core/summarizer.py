"""AI 한글 번역·요약 (논문명 한글 · 연구유형 · 초록 한글 번역 또는 요약).

무료: Gemini API 무료 티어(Google AI Studio 키), 웹 채팅(ChatGPT·Gemini·Copilot) 복사·붙여넣기.
유료: ChatGPT(OpenAI), Claude(Anthropic), Gemini(Google) API — 각 회사에서 결제 설정한 API 키 필요.
Microsoft Copilot은 무료 공개 API가 없어 복사·붙여넣기 모드로만 쓴다.
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .models import Article

NO_ABSTRACT = "초록 확인 안됨"
FREE_PROVIDER = "Gemini (Google) - 무료"
PAID_GEMINI = "Gemini (Google) - 유료"
MANUAL_PROVIDER = "웹 채팅 복사·붙여넣기 (ChatGPT·Copilot·Gemini 무료)"
MANUAL_LABEL = "🆓 무료 · 웹 채팅 복사·붙여넣기 (ChatGPT·Gemini·Copilot)"

# 키(이름)는 settings.json에 저장되므로 바꾸지 말 것. 화면에는 label을 보여 준다.
# family: 호출 방식 / batch: 한 번에 보낼 논문 수 / workers: 동시 요청 수 / interval: 요청 사이 최소 간격(초)
PROVIDERS = {
    FREE_PROVIDER: {
        "label": "🆓 무료 · Gemini API (Google AI Studio)",
        "family": "gemini",
        "env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/api-keys",
        "default_model": "gemini-3.5-flash",
        # 입력한 모델이 과부하(503)·시간초과·서비스 종료면 차례로 시도
        "fallbacks": ["gemini-flash-latest", "gemini-flash-lite-latest"],
        "suggested": ["gemini-3.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"],
        "model_help": "과부하 시 gemini-flash-latest → gemini-flash-lite-latest 순으로 자동 전환",
        "batch": 8, "workers": 1, "interval": 7.0,
    },
    "ChatGPT (OpenAI)": {
        "label": "💳 유료 · ChatGPT (OpenAI API)",
        "family": "openai",
        "env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys",
        "default_model": "gpt-5",
        "suggested": ["gpt-5", "gpt-5-mini"],
        "model_help": "OpenAI 계정에서 쓸 수 있는 모델명 (‘사용 가능한 모델 불러오기’로 확인)",
    },
    "Claude (Anthropic)": {
        "label": "💳 유료 · Claude (Anthropic API)",
        "family": "claude",
        "env": "ANTHROPIC_API_KEY",
        "key_url": "https://console.anthropic.com/settings/keys",
        "default_model": "claude-opus-5-5",
        "suggested": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
        "model_help": "claude-opus-5-5 (최고 품질), claude-sonnet-5-5, claude-haiku-5-5 (저렴)",
    },
    PAID_GEMINI: {
        "label": "💳 유료 · Gemini (Google API, 결제 설정한 키)",
        "family": "gemini",
        "env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/api-keys",
        "default_model": "gemini-pro-latest",
        "fallbacks": ["gemini-flash-latest"],
        "suggested": ["gemini-pro-latest", "gemini-flash-latest", "gemini-flash-lite-latest"],
        "model_help": "결제(Billing)를 설정한 Google AI Studio 프로젝트의 키 사용",
    },
}


def label(provider: str) -> str:
    return MANUAL_LABEL if provider == MANUAL_PROVIDER else PROVIDERS[provider]["label"]


TRANSLATE, SUMMARY = "translate", "summary"
MODES = {TRANSLATE: "초록 한글 번역 (전체)", SUMMARY: "초록 한글 요약"}
ABSTRACT_HEADERS = {TRANSLATE: "초록 (한글 번역)", SUMMARY: "초록 요약 (한글)"}

_COMMON = """당신은 제약회사 의학부를 위한 의학 문헌 번역가입니다.
입력으로 논문 목록(JSON)을 받습니다. 각 논문마다 다음을 한국어로 작성하세요.

1. titleKo: 원문 논문명을 자연스러운 한국어 제목으로 번역 (약물명·질환명은 국내 표준 한글 표기).
2. studyType: 연구유형 한 가지. 예: 무작위 대조시험, 메타분석, 체계적 문헌고찰, 전향적 코호트, 후향적 코호트,
   환자-대조군 연구, 단면연구, 증례보고, 동물실험, 세포실험, 종설, 사설, 독자서신, 저자답변, 진료지침, 기타.
3. abstractKo: {abstract_rule}
   - 대상자 수, 용량, 수치(HR, OR, 95% CI, p값 등)와 약어는 원문 그대로 유지.
   - 원문에 없는 내용은 추가하지 말 것.
   - "abstract" 값에 글이 있으면 소제목이 없더라도 반드시 번역할 것.
     "abstract" 값이 빈 문자열("")일 때만 abstractKo를 "초록 확인 안됨"으로 쓰고, studyType은 제목으로 판단.

용어·표기 원칙:
   - 한국어(한글)로만 작성하고 일본어(가나)·한자는 쓰지 말 것.
   - 국내 의학 용어 사용: placebo=위약, double-blind=이중맹검, open-label=공개, randomized=무작위배정,
     efficacy=유효성, adverse event=이상사례, hazard ratio=위험비, odds ratio=교차비, confidence interval=신뢰구간.
   - 약물명은 식약처 성분명 한글 표기 (예: pioglitazone=피오글리타존, metformin=메트포르민, dapagliflozin=다파글리플로진).

출력은 설명 없이 JSON 배열만 출력합니다:
[{{"id": "<입력 id>", "titleKo": "...", "studyType": "...", "abstractKo": "..."}}]"""

_TRANSLATE_RULE = """초록 전체를 빠짐없이 한국어로 번역 (요약·생략 금지). 의학 논문 문체(~하였다)로 번역.
   - 원문의 소제목(Background, Methods, Results, Conclusions 등)은 "배경:", "방법:", "결과:", "결론:"처럼
     한글 소제목으로 바꾸고 소제목마다 줄바꿈(\\n)."""

_SUMMARY_RULE = """초록을 한국어로 요약.
   - 원저: "목적: ...", "방법: ...", "결과: ...", "결론: ..." 줄로 구분 (줄바꿈 \\n).
   - 종설·사설 등: "주제: ...", "내용: ...", "결론: ..." 줄로 구분."""

SYSTEM_PROMPTS = {
    TRANSLATE: _COMMON.format(abstract_rule=_TRANSLATE_RULE),
    SUMMARY: _COMMON.format(abstract_rule=_SUMMARY_RULE),
}


class SummaryError(Exception):
    pass


def _payload(articles: list[Article]) -> str:
    items = [{"id": a.source_id, "title": a.title, "abstract": a.abstract, "keywords": a.keywords}
             for a in articles]
    return json.dumps(items, ensure_ascii=False)


def _parse(text: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    # 답변이 중간에 잘린 경우: 온전한 {...} 항목만 건진다
    decoder, found, i = json.JSONDecoder(), [], text.find("{")
    while i >= 0:
        try:
            obj, j = decoder.raw_decode(text, i)
            if isinstance(obj, dict) and "id" in obj:
                found.append(obj)
            i = text.find("{", j)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
    if not found:
        raise SummaryError("AI 응답에서 JSON을 찾지 못했습니다.")
    return found


def _call_claude(model, api_key, user, system, **_):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, max_retries=4)
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        raise SummaryError("Claude가 요청을 거절했습니다 (refusal).")
    return "".join(b.text for b in resp.content if b.type == "text")


def _call_openai(model, api_key, user, system, **_):
    from openai import OpenAI
    client = OpenAI(api_key=api_key, max_retries=4)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content or ""


def _call_gemini(model, api_key, user, system, **_):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=100_000))
    resp = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json"),
    )
    return resp.text or ""


_SKIP_WORDS = ("tts", "image", "audio", "live", "embed", "realtime", "transcribe", "search", "robotics",
               "computer-use", "moderation", "dall-e", "whisper", "veo", "imagen", "lyria", "aqa", "banana")


def list_models(provider: str, api_key: str) -> list[str]:
    """API 키로 쓸 수 있는 텍스트 생성 모델 목록 (최신·별칭 먼저)."""
    family = PROVIDERS[provider]["family"]
    if family == "gemini":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30_000))
        names = [m.name.removeprefix("models/") for m in client.models.list()
                 if "generateContent" in (m.supported_actions or [])]
        names = [n for n in names if n.startswith(("gemini", "gemma"))]
    elif family == "claude":
        import anthropic
        names = [m.id for m in anthropic.Anthropic(api_key=api_key).models.list()]
    elif family == "openai":
        from openai import OpenAI
        names = [m.id for m in OpenAI(api_key=api_key).models.list() if m.id.startswith(("gpt-", "o"))]
    else:
        return []
    names = [n for n in names if not any(w in n.lower() for w in _SKIP_WORDS)]

    def order(name: str):
        nums = [int(x) for x in re.findall(r"\d+", name)[:3]]
        return (not name.endswith("latest"), "preview" in name or "exp" in name, [-x for x in nums], name)
    return sorted(dict.fromkeys(names), key=order)


CALLERS = {
    "claude": _call_claude,
    "openai": _call_openai,
    "gemini": _call_gemini,
}


_pace_lock = threading.Lock()
_last_call: dict[str, float] = {}


def _pace(provider: str):
    """무료 한도(분당 요청 수)를 넘지 않도록 요청 간격을 둔다."""
    interval = PROVIDERS[provider].get("interval", 0)
    with _pace_lock:
        wait = _last_call.get(provider, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[provider] = time.monotonic()


def _is_rate_limit(text: str) -> bool:
    return "429" in text or "RESOURCE_EXHAUSTED" in text


def _is_daily_limit(text: str) -> bool:
    low = text.lower()
    return "perday" in low or "per day" in low or "insufficient_quota" in low or "credit" in low


def _is_unavailable(text: str) -> bool:
    """모델 과부하·시간초과·서비스 종료 → 다른 모델로 넘어갈 오류."""
    low = text.lower()
    return any(k in text for k in ("503", "504", "UNAVAILABLE", "DEADLINE_EXCEEDED")) or         "high demand" in low or "overloaded" in low or "no longer available" in low or "timed out" in low


def _retry_delay(text: str) -> float:
    m = re.search(r"retry(?:Delay)?['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)s", text, re.I)
    return min(float(m.group(1)) + 2, 90) if m else 30


def _models_to_try(provider: str, model: str, fallback: bool = True) -> list[str]:
    models = [model] + (PROVIDERS[provider].get("fallbacks", []) if fallback else [])
    return list(dict.fromkeys(m for m in models if m))


def _summarize_batch(provider, model, api_key, batch, endpoint, mode=TRANSLATE, fallback=True) -> tuple[dict, str]:
    """(논문 ID별 결과, 실제로 쓴 모델)을 돌려준다."""
    call = CALLERS[PROVIDERS[provider]["family"]]
    models = _models_to_try(provider, model, fallback)
    last_error = None
    for round_ in range(2):  # 모든 모델이 과부하면 잠시 쉬고 한 바퀴 더
        for m in models:
            format_tries, limit_tries = 0, 0
            while format_tries < 2:  # JSON 형식이 깨지면 한 번 더 요청
                _pace(provider)
                try:
                    results = _parse(call(model=m, api_key=api_key, user=_payload(batch),
                                          system=SYSTEM_PROMPTS[mode], endpoint=endpoint))
                    return {str(r.get("id")): r for r in results}, m
                except (SummaryError, json.JSONDecodeError) as e:
                    last_error = e
                    format_tries += 1
                except Exception as e:  # noqa: BLE001 - 오류 종류에 따라 대기·모델 전환·중단
                    text = str(e)
                    last_error = e
                    if _is_unavailable(text):
                        break  # 다음 모델로
                    if not _is_rate_limit(text) or _is_daily_limit(text) or limit_tries >= 4:
                        raise
                    limit_tries += 1
                    time.sleep(_retry_delay(text))
        if round_ == 0 and last_error is not None and _is_unavailable(str(last_error)):
            time.sleep(20)
        else:
            break
    if isinstance(last_error, Exception) and not isinstance(last_error, (SummaryError, json.JSONDecodeError)):
        raise last_error
    raise SummaryError(str(last_error))


def explain_error(e: Exception) -> str:
    """자주 나오는 API 오류를 한글 안내로 바꾼다."""
    text = str(e)
    low = text.lower()
    if _is_rate_limit(text) and ("perday" in low or "per day" in low):
        hint = "오늘 무료 사용 한도를 다 썼습니다. 내일(한국시간 오후 4~5시 초기화) 다시 시도하거나 다른 AI를 선택하세요."
    elif "insufficient_quota" in low or ("credit" in low and ("balance" in low or "remaining" in low)):
        hint = "API 크레딧(잔액)이 없습니다. 해당 AI 회사의 결제(Billing) 페이지에서 크레딧을 충전하세요. (ChatGPT Plus 구독과 API 요금은 별개)"
    elif "401" in text or "api key not valid" in low or "api_key_invalid" in low or "invalid api key" in low or "incorrect api key" in low or "authentication" in low:
        hint = "API 키가 올바르지 않습니다. 키를 다시 복사해 넣으세요."
    elif "404" in text or "model_not_found" in low or "does not exist" in low or "not found" in low:
        hint = "모델명이 올바르지 않거나 이 계정에서 쓸 수 없는 모델입니다."
    elif _is_unavailable(text):
        hint = "AI 서버가 지금 붐비거나 응답이 늦습니다 (구글·OpenAI 쪽 일시 문제). 몇 분 뒤 다시 시도하세요."
    elif _is_rate_limit(text) or "rate" in low:
        hint = "요청 한도를 초과했습니다. 잠시 후 다시 시도하세요."
    else:
        return text
    return f"{hint}\n\n(원문: {text[:300]})"


def test_connection(provider: str, model: str, api_key: str, endpoint: str = "",
                    fallback: bool = True) -> tuple[bool, str]:
    """짧은 시험 요청으로 모델명·API 키가 맞는지 확인한다."""
    if not model or not api_key:
        return False, "모델과 API 키를 입력하세요."
    sample = Article(source="test", source_id="1", title="Pioglitazone in type 2 diabetes", authors=[],
                     journal="", bib="", abstract="Pioglitazone improved HbA1c in 100 patients.")
    try:
        result, used = _summarize_batch(provider, model, api_key, [sample], endpoint, fallback=fallback)
    except Exception as e:  # noqa: BLE001 - 어떤 오류든 화면에 그대로 보여 준다
        return False, f"연결 실패: {explain_error(e)}"
    title = result.get("1", {}).get("titleKo", "")
    note = "" if used == model else f" — {model}이(가) 붐벼서 {used}(으)로 자동 전환"
    return True, f"연결 성공 ({used}{note}). 예시 번역: {title}"


def summarize(articles: list[Article], provider: str, model: str, api_key: str,
              endpoint: str = "", progress=None, mode: str = TRANSLATE,
              fallback: bool = True) -> tuple[list[str], list[str]]:
    """articles의 title_ko / study_type / abstract_ko를 채운다. (오류·확인 필요 메시지, 실제로 쓴 모델) 반환.

    1차로 묶어서 요청한 뒤, 결과 검사에 걸린 논문(빈칸, 초록이 있는데 '초록 확인 안됨', 일본어·한자 섞임,
    번역이 지나치게 짧음)만 2건씩 다시 요청한다.
    """
    size = PROVIDERS[provider].get("batch", 8)
    errors, used_models = [], set()
    total = len(articles)

    def run(batches, done_offset):
        done = done_offset
        with ThreadPoolExecutor(max_workers=PROVIDERS[provider].get("workers", 4)) as pool:
            futures = {pool.submit(_summarize_batch, provider, model, api_key, b, endpoint, mode, fallback): b
                       for b in batches}
            for fut in as_completed(futures):
                batch = futures[fut]
                try:
                    by_id, used = fut.result()
                    used_models.add(used)
                except Exception as e:  # noqa: BLE001 - 한 묶음 실패가 전체를 멈추지 않게
                    errors.append(f"{batch[0].source_id} 외 {len(batch) - 1}건: {explain_error(e)}")
                    by_id = {}
                _apply(batch, by_id)
                done += len(batch)
                if progress:
                    progress(min(done, total), total)

    run([articles[i:i + size] for i in range(0, total, size)], 0)
    retry = [a for a in articles if problem(a, mode)]
    if retry:
        run([retry[i:i + 2] for i in range(0, len(retry), 2)], total)
    still = [a for a in articles if problem(a, mode)]
    if still:
        errors.append("다시 요청해도 번역이 불완전한 논문 (원문 초록 시트와 대조 필요): " +
                      ", ".join(f"{a.source_id}({problem(a, mode)})" for a in still))
    return errors, sorted(used_models)


_FOREIGN = re.compile(r"[぀-ヿ一-鿿]")  # 일본어 가나·한자


def problem(a: Article, mode: str = TRANSLATE) -> str:
    """AI 결과에 문제가 있으면 그 이유, 없으면 빈 문자열."""
    if not a.title_ko:
        return "제목 번역 없음"
    if a.has_abstract and not a.abstract_ko:
        return "초록 번역 없음"
    if _FOREIGN.search(a.title_ko + a.abstract_ko):
        return "일본어·한자 섞임"
    if mode == TRANSLATE and a.has_abstract and len(a.abstract_ko) < len(a.abstract) * 0.2:
        return "번역이 너무 짧음"
    return ""


def _apply(batch: list[Article], by_id: dict) -> int:
    """AI 결과를 논문에 채우고, 채운 건수를 돌려준다."""
    filled = 0
    for a in batch:
        r = by_id.get(a.source_id)
        if r:
            a.title_ko = (r.get("titleKo") or "").strip()
            a.study_type = (r.get("studyType") or "").strip()
            a.abstract_ko = (r.get("abstractKo") or "").strip()
            filled += 1
        if not a.has_abstract:
            a.abstract_ko = NO_ABSTRACT
        elif a.abstract_ko == NO_ABSTRACT:  # 초록이 있는데 AI가 없다고 답한 경우 → 빈칸(재요청 대상)
            a.abstract_ko = ""
    return filled


# ── 웹 채팅 복사·붙여넣기 모드 (API 없이 ChatGPT·Copilot·Gemini 무료 채팅 사용) ──
# 채팅 입력창 한 번에 넣을 수 있는 글자 수 (여유를 두고 잡은 값)
CHAT_LIMITS = {
    "ChatGPT (chatgpt.com)": 30000,
    "Gemini (gemini.google.com)": 30000,
    "Copilot - 회사 계정 (M365 Copilot Chat)": 14000,
    "Copilot - 개인 계정 (copilot.microsoft.com)": 3800,
}
MAX_PER_BATCH = 8  # 글자 수가 남아도 답변이 너무 길어지지 않게


def manual_prompt(batch: list[Article], mode: str = TRANSLATE) -> str:
    """웹 채팅 창에 그대로 붙여 넣을 질문 문장."""
    return f"{SYSTEM_PROMPTS[mode]}\n\n논문 목록(JSON):\n{_payload(batch)}"


def manual_batches(articles: list[Article], max_chars: int, mode: str = TRANSLATE) -> list[list[Article]]:
    """질문 글자 수가 max_chars를 넘지 않게 논문을 묶는다. (1건만으로도 넘으면 그 1건만 단독 묶음)"""
    batches, current = [], []
    for a in articles:
        trial = current + [a]
        if current and (len(trial) > MAX_PER_BATCH or len(manual_prompt(trial, mode)) > max_chars):
            batches.append(current)
            current = [a]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def apply_manual_answer(batch: list[Article], answer: str) -> tuple[int, list[str]]:
    """웹 채팅 답변(JSON)을 반영한다. (채운 건수, 빠진 논문 ID 목록)"""
    results = _parse(answer)
    by_id = {str(r.get("id")): r for r in results}
    filled = _apply(batch, by_id)
    missing = [a.source_id for a in batch if a.source_id not in by_id]
    return filled, missing
