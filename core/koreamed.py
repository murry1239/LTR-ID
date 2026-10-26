"""KoreaMed(https://koreamed.org) 검색.

검색식: <검색어>[ALL] AND <시작연도>:<종료연도>[DPY]
동의어 여러 개는 OR로 묶는다: ("thioctic acid"[ALL] OR "lipoic acid"[ALL]) AND 2021:2026[DPY]
"""
import re

import requests
from lxml import html

from .models import MONTHS, Article

NAME = "KoreaMed"
FILE_TAG = "Koreamed"
URL = "https://koreamed.org/SearchBasic.php"
PAGE_SIZE = 100
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) munkeomgi/1.0"}
TIMEOUT = 60


def build_query(terms: list[str], year_from: int, year_to: int) -> str:
    parts = [f'"{t}"[ALL]' if " " in t else f"{t}[ALL]" for t in terms]
    q = parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"
    return f"{q} AND {year_from}:{year_to}[DPY]"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def count(query: str, session: requests.Session) -> int:
    res = session.get(URL, params={"QY": query}, timeout=TIMEOUT)
    res.raise_for_status()
    root = html.fromstring(res.content)
    value = root.xpath('//input[@name="s_totalRecord"]/@value')
    return int(value[0]) if value and value[0].strip() else 0


def _fetch_page(query: str, total: int, page: int, session: requests.Session) -> list[Article]:
    body = {
        "search_type": "page_search",
        "sub_query_search": query,
        "s_totalRecord": str(total),
        "s_query_searc_view": query,
        "s_display_type": "abstract",
        "s_num_per_page": str(PAGE_SIZE),
        "s_display_sort": "1",
        "s_direct_page": str(page),
    }
    res = session.post(URL, data=body, timeout=TIMEOUT)
    res.raise_for_status()
    root = html.fromstring(res.content)
    return [_parse_item(item) for item in root.xpath('//dl[@class="resultItem"]')]


def _abstract_text(item) -> str:
    """<b>Purpose</b><br/>본문<br/><b>Results</b>... → 'Purpose: 본문\nResults: ...'"""
    blocks = []
    for dd in item.xpath('.//div[@class="abstract"]//dd'):
        for br in dd.iter("br"):
            br.tail = "\n" + (br.tail or "")
        for b in dd.iter("b"):
            b.text = (b.text or "").strip() + ":"
        text = dd.text_content()
        text = re.sub(r":\s*\n", ": ", text)
        lines = [_clean(line) for line in text.split("\n")]
        blocks.append("\n".join(line for line in lines if line))
    return "\n".join(b for b in blocks if b)


def _parse_item(item) -> Article:
    title_node = item.xpath('.//a[@class="title"]')[0]
    kmid = re.search(r"RID=(\d+)", title_node.get("href")).group(1)
    dt = item.xpath("./dt")[0]

    journal_node = dt.xpath('.//a[starts-with(@href,"/volumes/")]')
    journal = _clean(journal_node[0].text_content()).rstrip(".") if journal_node else ""
    doi_node = dt.xpath('.//a[contains(@href,"doi.org")]')
    doi = _clean(doi_node[0].text_content()).rstrip(".") if doi_node else ""

    # dt 텍스트: "1 J Korean Med Assoc. 2026 Jun;69(6):492-501. 10.5124/jkma.26.0043."
    dt_text = _clean(dt.text_content())
    rest = dt_text.split(journal + ".", 1)[1] if journal and journal + "." in dt_text else dt_text
    if doi:
        rest = rest.replace(doi + ".", "").replace(doi, "")
    bib = _clean(rest)
    m = re.search(r"((?:19|20)\d{2}\b.*)", bib)
    bib = m.group(1) if m else bib
    if bib and not bib.endswith("."):
        bib += "."

    year_m = re.match(r"((?:19|20)\d{2})(?: ([A-Z][a-z]{2}))?", bib)
    year = int(year_m.group(1)) if year_m else None
    month = MONTHS.get(year_m.group(2)) if year_m and year_m.group(2) else None

    authors_node = item.xpath('.//ul[@class="authorsList"]')
    authors_text = _clean(authors_node[0].text_content()) if authors_node else ""
    authors = [a.strip() for a in authors_text.split(",") if a.strip()]

    keywords = ""
    for h2 in item.xpath(".//h2"):
        if _clean(h2.text_content()).lower().startswith("keyword"):
            dd = h2.getparent().xpath(".//dd")
            keywords = _clean(dd[0].text_content()) if dd else ""

    return Article(
        source=NAME,
        source_id=kmid,
        title=_clean(title_node.text_content()),
        authors=authors,
        journal=journal,
        bib=bib,
        year=year,
        month=month,
        doi=doi,
        abstract=_abstract_text(item),
        keywords=keywords,
        url=f"{URL}?RID={kmid}",
    )


def search(terms: list[str], year_from: int, year_to: int, progress=None) -> tuple[str, list[Article]]:
    """검색식과 전체 결과를 돌려준다. progress(done, total)는 진행 표시용."""
    query = build_query(terms, year_from, year_to)
    with requests.Session() as session:
        session.headers.update(HEADERS)
        total = count(query, session)
        articles: dict[str, Article] = {}
        last_page = -(-total // PAGE_SIZE)
        page = 1
        while page <= last_page:
            batch = _fetch_page(query, total, page, session)
            if not batch:
                break
            for a in batch:
                articles.setdefault(a.source_id, a)
            if progress:
                progress(len(articles), total)
            page += 1
    return query, list(articles.values())
