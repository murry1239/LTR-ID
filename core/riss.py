"""RISS(https://www.riss.kr) 국내학술논문 검색.

상세검색 → 국내학술논문(colName=re_a_kor), 분류 [전체](znAll), 발행연도 범위.
동의어 여러 개는 각각 검색한 뒤 합친다 (중복 제거).
목록에는 출판 월·쪽수·DOI가 없어서 상세 페이지와 PubMed·Crossref(DOI)로 보완한다.
"""
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from .enrich import clean_doi, crossref_by_doi, pubmed_by_doi
from .log import get_logger
from .models import Article

log = get_logger()

NAME = "RISS"
FILE_TAG = "RISS"
# 화면에 두 번째 검색어 칸(영문)을 보여 주고 결과를 따로 엑셀로 만든다
SECOND_QUERY_LABEL = "영문 검색어 (선택)"
FIRST_QUERY_LABEL = "한글 검색어 (제품명·성분명)"

SEARCH_URL = "https://www.riss.kr/search/Search.do"
BASE = "https://www.riss.kr"
PAGE_SIZE = 100
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) munkeomgi/1.0"}
TIMEOUT = 45
WORKERS = 4
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _plain(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _one_line(text: str) -> str:
    return " ".join(_plain(text).split())


def _params(term: str, year_from: int, year_to: int, page: int) -> dict:
    return {
        "isDetailSearch": "Y", "searchGubun": "true", "colName": "re_a_kor",
        "queryText": f"znAll,{term}", "p_year1": str(year_from), "p_year2": str(year_to),
        "pageScale": str(PAGE_SIZE), "iStartCount": str((page - 1) * PAGE_SIZE), "pageNumber": str(page),
    }


def build_query(terms: list[str], year_from: int, year_to: int) -> str:
    return " OR ".join(f"[전체] {t}" for t in terms) + f" · 국내학술논문 · 발행연도 {year_from}–{year_to}"


def _parse_list(page_html: str) -> tuple[int, list[dict]]:
    m = re.search(r'검색결과\s*<span class="num">\s*([\d,]+)', page_html)
    total = int(m.group(1).replace(",", "")) if m else 0
    items = []
    for part in page_html.split('<div class="cont ml60">')[1:]:
        part = part.split('<div class="simple"', 1)[0]
        t = re.search(r'<p class="title"><a href="([^"]+)"[^>]*>(.*?)</a></p>', part, re.S)
        if not t:
            continue
        url = urljoin(BASE, html.unescape(t.group(1)))
        control_no = parse_qs(urlparse(url).query).get("control_no", [""])[0]
        etc = re.search(r'<p class="etc">(.*?)</p>', part, re.S)
        spans = [_one_line(s) for s in re.findall(r"<span[^>]*>(.*?)</span>", etc.group(1), re.S)] if etc else []
        year = next((int(s) for s in spans if re.fullmatch(r"(19|20)\d{2}", s)), None)
        abstract = re.search(r'<p class="preAbstract">(.*?)</p>', part, re.S)
        items.append({
            "control_no": control_no, "url": url, "title": _one_line(t.group(2)), "year": year,
            "abstract": _plain(abstract.group(1)) if abstract else "",
        })
    return total, items


def _search_term(term, year_from, year_to, session) -> list[dict]:
    for attempt in range(2):  # 0건이면 일시적인 빈 응답일 수 있어 한 번 더 확인
        res = session.get(SEARCH_URL, params=_params(term, year_from, year_to, 1), timeout=TIMEOUT)
        res.raise_for_status()
        total, items = _parse_list(res.text)
        if total or "검색결과" in res.text:
            break
        log.warning("RISS 응답에 검색 결과 영역이 없음 — 다시 요청: %s", term)
        time.sleep(2)
    for page in range(2, -(-total // PAGE_SIZE) + 1):
        res = session.get(SEARCH_URL, params=_params(term, year_from, year_to, page), timeout=TIMEOUT)
        res.raise_for_status()
        items += _parse_list(res.text)[1]
    return items


def _detail(item: dict, session) -> dict:
    """상세 페이지: 저자, 학술지, 권호, 쪽수, DOI, 주제어, 고유 링크."""
    res = session.get(item["url"], timeout=TIMEOUT)
    res.raise_for_status()
    page = res.text
    info = page.split('class="infoDetailL"', 1)[-1].split('class="infoDetailR"', 1)[0]
    fields, authors = {}, []
    for li in re.findall(r"<li\b[^>]*>(.*?)</li>", info, re.S):
        label = re.search(r'<span class="strong">(.*?)</span>', li, re.S)
        if not label:
            continue
        key, value = _one_line(label.group(1)), li[label.end():]
        fields[key] = _one_line(value)
        if key == "저자":
            authors = [_one_line(a) for a in re.findall(r"<a\b[^>]*>(.*?)</a>", value, re.S) if _one_line(a)]
    link = re.search(r"https://www\.riss\.kr/link\?id=(A\d+)", page)
    abstracts = [_plain(a) for a in re.findall(r'<div class="textWrap" id="abs\d+">(.*?)</div>', page, re.S)]
    return {"fields": fields, "authors": authors, "abstracts": [a for a in abstracts if a],
            "riss_id": link.group(1) if link else item["control_no"],
            "link": link.group(0) if link else item["url"]}


def _riss_name(name: str) -> str:
    """소속 괄호만 지운 RISS 원문 이름. (영문 이름의 성·이름 순서가 논문마다 달라 약어로 바꾸지 않는다)"""
    return " ".join(re.sub(r"\(.*?\)", "", name).split())


def _build(item: dict, detail: dict, meta: dict) -> Article:
    """RISS 정보에 PubMed/Crossref(meta) 정보를 덧입힌다."""
    f = detail["fields"]
    journal = re.sub(r"\s*\(.*\)\s*$", "", f.get("학술지명", "")).strip()
    volume_issue = f.get("권호사항", "")
    vol = re.search(r"Vol\.?\s*([\w\-]+)", volume_issue) or re.search(r"제?\s*(\d+)\s*권", volume_issue)
    no = re.search(r"No\.?\s*([\w\-]+)", volume_issue) or re.search(r"제?\s*(\d+)\s*호", volume_issue)
    vol_s, no_s = (vol.group(1) if vol else ""), (no.group(1) if no else "")
    pages = re.sub(r"\(.*?\)", "", f.get("수록면", "")).strip()
    year = item["year"] or (int(f["발행연도"]) if f.get("발행연도", "").isdigit() else None)
    authors = [n for n in (_riss_name(a) for a in detail["authors"]) if n]
    abstract = item["abstract"]
    # 목록의 초록은 2,000자에서 잘리므로, 잘렸거나 비어 있으면 상세 페이지의 전체 초록을 쓴다
    if len(abstract) >= 1990 or not abstract:
        full = detail.get("abstracts") or []
        same = [a for a in full if abstract and a[:60] == abstract[:60]]
        abstract = (same or full or [abstract])[0] if (same or full) else abstract
    month = None

    if meta:
        if meta.get("month") and meta.get("year") in (year, None):
            month = meta["month"]
        from_pubmed = bool(meta.get("pmid"))
        if meta.get("journal") and (from_pubmed or len(meta["journal"]) >= 5):  # Crossref 약어는 가끔 엉뚱함
            journal = meta["journal"]
        vol_s, no_s = meta.get("volume") or vol_s, meta.get("issue") or no_s
        if meta.get("pages") and ("-" in meta["pages"] or not pages):
            pages = meta["pages"]
        if from_pubmed and meta.get("authors"):  # Crossref 저자는 성·이름이 뒤바뀐 경우가 있어 쓰지 않음
            authors = meta["authors"]

    bib = f"{year}{' ' + MONTH_ABBR[month - 1] if month else ''}"
    if vol_s:
        bib += f";{vol_s}{f'({no_s})' if no_s else ''}"
    if pages:
        bib += f":{pages}"
    return Article(
        source=NAME, source_id=detail["riss_id"], title=item["title"], authors=authors, journal=journal,
        bib=bib + ".", year=year, month=month, doi=clean_doi(f.get("DOI식별코드", "")),
        abstract=abstract, keywords=f.get("주제어", ""), url=detail["link"],
        extra={"publisher": f.get("발행기관", ""), "language": f.get("작성언어", ""),
               "indexing": f.get("등재정보", ""), "pmid": (meta or {}).get("pmid", "")},
    )


def search(terms: list[str], year_from: int, year_to: int, progress=None) -> tuple[str, list[Article]]:
    """검색식 설명과 전체 결과를 돌려준다. progress(done, total)는 진행 표시용."""
    with requests.Session() as session:
        session.headers.update(HEADERS)
        items: dict[str, dict] = {}
        for term in terms:
            for it in _search_term(term, year_from, year_to, session):
                items.setdefault(it["control_no"], it)
        items_list = list(items.values())
        total = len(items_list) + 1
        done = 0

        def step(n=1):  # 화면(Streamlit) 갱신은 메인 스레드에서만 해야 한다
            nonlocal done
            done += n
            if progress:
                progress(min(done, total), total)

        details = []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for d in pool.map(lambda it: _detail(it, session), items_list):
                details.append(d)
                step()

        # 출판 월·학술지 약어 보완: PubMed 먼저, 없으면 Crossref
        dois = [clean_doi(d["fields"].get("DOI식별코드", "")) for d in details]
        meta = pubmed_by_doi([d for d in dois if d], session)
        missing = [d for d in dois if d and d not in meta]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for d, cr in zip(missing, pool.map(lambda x: crossref_by_doi(x, session), missing)):
                if cr:
                    meta[d] = cr
        step()

    articles = [_build(it, d, meta.get(doi, {})) for it, d, doi in zip(items_list, details, dois)]
    return build_query(terms, year_from, year_to), articles

