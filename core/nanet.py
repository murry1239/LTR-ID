"""국회도서관(dl.nanet.go.kr) 연속간행물·학술기사 검색 — 브라우저 자동 조작.

국회도서관은 검색 화면을 거치지 않은 직접 요청에는 결과를 주지 않으므로,
PC에 설치된 Microsoft Edge(없으면 Chrome)를 Playwright로 띄워 사람이 하는 순서대로 검색한다.
  홈 → 검색어 입력 → [연속간행물·학술기사 > 전체] → 발행연도 범위 → 한 쪽 50건씩 넘기며 목록 수집
  → 각 논문의 ‘초록’ 버튼과 같은 기능으로 초록 수집
영문 제목은 PubMed에서 같은 논문을 찾아 저자 약어·학술지 약어·출판 월을 보완한다.
"""
import re
import time

import requests

from .enrich import pubmed_by_title
from .log import get_logger
from .models import MONTHS, Article

log = get_logger()

NAME = "국회도서관"
FILE_TAG = "NAL"
FIRST_QUERY_LABEL = "한글 검색어 (제품명·성분명)"
SECOND_QUERY_LABEL = "영문 검색어 (선택)"
NOTE = ("국회도서관은 PC의 Edge(또는 Chrome) 브라우저를 화면 없이 자동으로 조작해 검색합니다. "
        "목록·초록을 차례로 불러오므로 100건에 2~3분 걸립니다.")
HOME = "https://dl.nanet.go.kr/"
DETAIL_URL = "https://dl.nanet.go.kr/search/searchInnerDetail.do?controlNo={}"
PAGE_SIZE = 50          # 국회도서관 화면에서 고를 수 있는 최대값
PAUSE = 0.6             # 요청 사이 쉬는 시간(초) — 사이트에 부담을 주지 않도록
NAV_TIMEOUT = 60_000
MONTH_ABBR = {v: k for k, v in MONTHS.items()}
KINDS = {"KINX": "국내기사", "FINX": "국외기사", "SERL": "학술지·잡지"}

# 결과 화면에서 다른 쪽으로 이동 (사이트 자체 함수 changeHanjaYnByList 등과 같은 방식)
_GO_PAGE_JS = """([n, size]) => {
    $("#searchMehtod").val("F");
    $("#zone").val(zone); $("#topMainMenuCode").val(topMainMenuCode); $("#topSubMenuCode").val(topSubMenuCode);
    $("#pageNum").val(n); $("#pageSize").val(size); $("#orderBy").val(orderBy); $("#hanjaYn").val(hanjaYn);
    $("#fieldText").val(fieldText); $("#dpBranch").val(dpBranch); $("#journalKind").val(journalKind);
    searchInnerList(null, ddcPopSearchYn);
}"""

# 결과 목록에서 논문 정보 읽기
_PARSE_JS = """() => [...document.querySelectorAll('input.chk[name=chk]')].map(cb => {
    let li = cb.closest('li');
    const a = li.querySelector('.top a');
    const authors = a && a.querySelector('span') ? a.querySelector('span').innerText.trim() : '';
    let title = a ? a.innerText.trim() : '';
    if (authors && title.endsWith(authors)) title = title.slice(0, -authors.length).trim();
    const info = {};
    li.querySelectorAll('.middle li').forEach(x => {
        const label = x.querySelector('span') ? x.querySelector('span').innerText.replace(':', '').trim() : '';
        info[label] = x.innerText.replace(/^[^:]*:/, '').trim();
    });
    const links = [...li.querySelectorAll('button.outside-doc, a.outside-doc')].map(b => {
        const m = (b.getAttribute('onclick') || '').match(/window\\.open\\('([^']+)'/);
        return m ? m[1] : '';
    }).filter(Boolean);
    return {control_no: cb.value, kind: (li.querySelector('.top .btn') || {}).innerText || '',
            title, authors, journal_info: info['수록정보'] || '', publish: info['발행사항'] || '',
            has_abs: !!li.querySelector('[onclick*="viewAbs"]'), links};
})"""

_ABS_JS = """async (controlNo) => {
    try {
        const d = await $.ajax({type: 'POST', url: '/search/getFileinfoContents.do',
                                data: {controlNo: controlNo, fileType: 'ABS'}, dataType: 'json'});
        return d ? String(d) : '';
    } catch (e) { return ''; }
}"""


def build_query(terms, year_from, year_to) -> str:
    return " OR ".join(f"[전체항목] {t}" for t in terms) + f" · 연속간행물·학술기사 전체 · 발행연도 {year_from}–{year_to}"


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _plain_abstract(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text or "")  # 눈에 안 보이는 제어 문자
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _parse_journal_info(info: str) -> dict:
    """'Diabetes & metabolism journal = dmj. v.47 n.5 (Sep. 2023), p. 668-681'
       '대한내과학회지. 제98권 제3호 (2023년 6월), pp. 101-110' → 학술지·권·호·월·쪽"""
    journal = re.split(r"\.\s*(?:v\.|제\s*\d+\s*권|vol\.|n\.|no\.)", info, maxsplit=1, flags=re.I)[0]
    journal = re.split(r"\s+=\s+", journal)[0].strip(" .")
    vol = re.search(r"\b(?:v|vol)\.\s*([\w\-]+)", info, re.I) or re.search(r"제\s*(\d+)\s*권", info)
    no = re.search(r"\b(?:n|no)\.\s*([\w\-]+)", info, re.I) or re.search(r"제\s*(\d+)\s*호", info)
    month = None
    m = re.search(r"\(([A-Z][a-z]{2})\.?\s*(?:\d{1,2},?\s*)?(\d{4})\)", info)
    if m and m.group(1) in MONTHS:
        month = MONTHS[m.group(1)]
    else:
        m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월", info) or re.search(r"\((\d{4})\.\s*(\d{1,2})\)", info)
        if m:
            month = int(m.group(2))
    pages = re.search(r"(?<![A-Za-z])pp?\.\s*(\d[\w]*(?:\s*-\s*\w+)?)", info)  # 'Sep.'의 p. 와 구분
    return {"journal": journal, "volume": vol.group(1) if vol else "", "issue": no.group(1) if no else "",
            "month": month if month and 1 <= month <= 12 else None,
            "pages": pages.group(1).replace(" ", "") if pages else ""}


def _year(publish: str, info: str):
    m = re.search(r"((?:19|20)\d{2})\.\s*\d{1,2}\.", publish) or re.search(r"((?:19|20)\d{2})", publish + " " + info)
    return int(m.group(1)) if m else None


def _first_author_list(authors: str) -> list[str]:
    return [a.strip() for a in re.split(r"\s*[,;]\s*", authors or "") if a.strip()]


def _build(row: dict, session: requests.Session) -> Article:
    info = _parse_journal_info(row["journal_info"])
    year = _year(row["publish"], row["journal_info"])
    kind = KINDS.get(row["control_no"][:4], _clean(row["kind"]))
    authors = _first_author_list(row["authors"])
    journal, vol, no, pages, month = info["journal"], info["volume"], info["issue"], info["pages"], info["month"]
    abstract = row.get("abstract", "")
    note = f"{kind} · 국회도서관 초록" if abstract else f"{kind}"

    # 영문 제목이면 PubMed에서 같은 논문을 찾아 저자 약어·학술지 약어·월 보완
    pm = pubmed_by_title(row["title"], year, session) if kind != "학술지·잡지" else {}
    if pm:
        authors = pm["authors"] or authors
        journal = pm["journal"] or journal
        vol, no, pages = pm["volume"] or vol, pm["issue"] or no, pm["pages"] or pages
        if pm.get("month") and pm.get("year") in (year, None):
            month = pm["month"]
        if not abstract and pm.get("abstract"):
            abstract = pm["abstract"]
            note = f"{kind} · 초록: PubMed"
        note += f" · 서지 보완: PubMed {pm['pmid']}"
    if row["links"]:
        note += " · 외부기관 원문: " + row["links"][0]

    bib = f"{year or ''}{' ' + MONTH_ABBR[month] if month else ''}"
    if vol:
        bib += f";{vol}{f'({no})' if no else ''}"
    if pages:
        bib += f":{pages}"
    return Article(
        source=NAME, source_id=row["control_no"], title=_clean(row["title"]), authors=authors,
        journal=journal, bib=(bib + ".") if bib else "", year=year, month=month,
        doi=(pm or {}).get("doi", ""), abstract=abstract, url=DETAIL_URL.format(row["control_no"]),
        extra={"note": note, "kind": kind, "journal_info": row["journal_info"]},
    )


def _launch(playwright):
    """PC에 설치된 Edge → Chrome 순서로 사용 (브라우저를 따로 받지 않음)."""
    last = None
    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch(channel=channel, headless=True)
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"Edge 또는 Chrome 브라우저를 찾지 못했습니다: {last}")


def _search_term(page, term: str, year_from: int, year_to: int) -> list[dict]:
    page.goto(HOME, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
    box = page.locator("input[type=search][placeholder*='검색어']").first
    box.fill(term)
    with page.expect_navigation(timeout=NAV_TIMEOUT, wait_until="domcontentloaded"):
        box.press("Enter")
    time.sleep(PAUSE)
    with page.expect_navigation(timeout=NAV_TIMEOUT, wait_until="domcontentloaded"):  # 연속간행물·학술기사 > 전체
        page.evaluate("changeDbDiv('KINX_ALL', 'KINX_ALL')")
    time.sleep(PAUSE)
    with page.expect_navigation(timeout=NAV_TIMEOUT, wait_until="domcontentloaded"):  # 발행연도 범위
        page.evaluate(f"$('#startPubYear').val('{year_from}'); $('#endPubYear').val('{year_to}'); changePubYear();")
    time.sleep(PAUSE)
    page.wait_for_function("typeof totalSizeByMenu !== 'undefined'", timeout=NAV_TIMEOUT)
    total = int(page.evaluate("totalSizeByMenu") or 0)
    log.info("[국회도서관] '%s' 연속간행물·학술기사 %d–%d: %d건", term, year_from, year_to, total)
    rows: dict[str, dict] = {}
    for n in range(1, -(-total // PAGE_SIZE) + 1):
        with page.expect_navigation(timeout=NAV_TIMEOUT, wait_until="domcontentloaded"):
            page.evaluate(_GO_PAGE_JS, [n, PAGE_SIZE])
        time.sleep(PAUSE)
        page.wait_for_function("typeof totalSizeByMenu !== 'undefined'", timeout=NAV_TIMEOUT)
        for r in page.evaluate(_PARSE_JS):
            rows.setdefault(r["control_no"], r)
    if len(rows) < total:
        log.warning("[국회도서관] '%s' 목록 일부만 수집: %d/%d", term, len(rows), total)
    for r in rows.values():
        r["abstract"] = _plain_abstract(page.evaluate(_ABS_JS, r["control_no"])) if r["has_abs"] else ""
        time.sleep(PAUSE / 3)
    return list(rows.values())


def search(terms: list[str], year_from: int, year_to: int, progress=None) -> tuple[str, list[Article]]:
    from playwright.sync_api import sync_playwright
    rows: dict[str, dict] = {}
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_context(locale="ko-KR").new_page()
            for i, term in enumerate(terms, 1):
                for attempt in range(3):  # 사이트가 느리거나 일시적으로 응답이 없으면 처음부터 다시
                    try:
                        found = _search_term(page, term, year_from, year_to)
                        break
                    except Exception as e:  # noqa: BLE001
                        if attempt == 2:
                            raise RuntimeError(f"국회도서관 검색 실패 ('{term}'): {e}") from e
                        log.warning("[국회도서관] '%s' 검색 재시도 (%d/3): %s", term, attempt + 1, str(e)[:200])
                        time.sleep(3)
                for r in found:
                    rows.setdefault(r["control_no"], r)
                if progress:  # 1단계: 검색어별 목록·초록 수집
                    progress(i, len(terms))
        finally:
            browser.close()
    articles = []
    with requests.Session() as session:
        for n, r in enumerate(rows.values(), 1):
            articles.append(_build(r, session))
            if progress:  # 2단계: PubMed로 서지 보완
                progress(n, len(rows))
    log.info("[국회도서관] %s → %d건, 초록 %d건", terms, len(articles), sum(a.has_abstract for a in articles))
    return build_query(terms, year_from, year_to), articles
