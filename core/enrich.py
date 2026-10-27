"""DOI로 서지정보 보완 (출판 월, 학술지 약어, 권·호·쪽, 저자).

1순위 PubMed(E-utilities, 무료·키 불필요), 2순위 Crossref.
RISS·국회도서관처럼 출판 월이나 학술지 약어가 없는 사이트에서 쓴다.
"""
import re
import time
from urllib.parse import quote

import requests

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "munkeomgi"
TIMEOUT = 45
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def clean_doi(doi: str) -> str:
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", (doi or "").strip(), flags=re.I).strip().lower()


def _get_json(session, url, params):
    for attempt in range(3):  # NCBI는 초당 3회 제한 → 429면 잠시 쉬고 재시도
        res = session.get(url, params=params, timeout=TIMEOUT)
        if res.status_code == 429:
            time.sleep(1.5 * (attempt + 1))
            continue
        res.raise_for_status()
        return res.json()
    res.raise_for_status()


def pubmed_by_doi(dois: list[str], session: requests.Session) -> dict[str, dict]:
    """{doi: {year, month, journal, volume, issue, pages, authors, pmid}} — PubMed에 있는 것만."""
    dois = sorted({clean_doi(d) for d in dois if clean_doi(d)})
    found: dict[str, dict] = {}
    for i in range(0, len(dois), 40):
        chunk = dois[i:i + 40]
        try:
            term = " OR ".join(f'"{d}"[doi]' for d in chunk)
            ids = _get_json(session, f"{EUTILS}/esearch.fcgi", {
                "db": "pubmed", "retmode": "json", "retmax": 200, "tool": TOOL, "term": term,
            })["esearchresult"]["idlist"]
            if not ids:
                continue
            time.sleep(0.35)
            result = _get_json(session, f"{EUTILS}/esummary.fcgi", {
                "db": "pubmed", "retmode": "json", "tool": TOOL, "id": ",".join(ids),
            })["result"]
            time.sleep(0.35)
        except (requests.RequestException, ValueError, KeyError):
            continue
        for pmid in result.get("uids", []):
            r = result[pmid]
            doi = next((clean_doi(x["value"]) for x in r.get("articleids", []) if x.get("idtype") == "doi"), "")
            if doi not in chunk:
                continue
            m = re.match(r"(\d{4})(?:\s+([A-Z][a-z]{2}))?", r.get("pubdate", ""))
            found[doi] = {
                "pmid": pmid,
                "year": int(m.group(1)) if m else None,
                "month": MONTHS.get(m.group(2)) if m and m.group(2) else None,
                "journal": r.get("source", ""),
                "volume": r.get("volume", ""), "issue": r.get("issue", ""), "pages": r.get("pages", ""),
                "authors": [a["name"] for a in r.get("authors", []) if a.get("authtype") == "Author"],
            }
    return found


def _vancouver(family: str, given: str) -> str:
    initials = "".join(p[0].upper() for p in re.split(r"[\s\-.]+", given or "") if p)
    return f"{family} {initials}".strip()


def crossref_by_doi(doi: str, session: requests.Session) -> dict:
    """같은 형태의 dict (없으면 빈 dict). 한국 학술지는 월이 비어 있는 경우가 많다."""
    doi = clean_doi(doi)
    if not doi:
        return {}
    try:
        res = session.get(f"https://api.crossref.org/works/{quote(doi, safe='')}",
                          headers={"User-Agent": f"{TOOL}/1.0 (literature search tool)"}, timeout=TIMEOUT)
        if not res.ok:
            return {}
        m = res.json()["message"]
    except (requests.RequestException, ValueError, KeyError):
        return {}
    parts = []
    for key in ("published-print", "issued", "published-online"):
        parts = (m.get(key) or {}).get("date-parts", [[]])[0] or []
        if len(parts) > 1:
            break
    short = m.get("short-container-title") or []
    return {
        "year": parts[0] if parts else None,
        "month": parts[1] if len(parts) > 1 else None,
        "journal": short[0] if short else "",
        "volume": m.get("volume", ""), "issue": m.get("issue", ""), "pages": m.get("page", ""),
        "authors": [_vancouver(a.get("family", ""), a.get("given", "")) for a in m.get("author", [])
                    if a.get("family")],
    }


# ── 제목으로 찾기 (국회도서관 결과 파일처럼 DOI가 없을 때) ──
import difflib  # noqa: E402
import threading  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

_ncbi_lock = threading.Lock()
_ncbi_last = [0.0]


def _ncbi_get(session, url, params):
    """NCBI는 키 없이 초당 3회까지 → 여러 스레드에서 불러도 간격을 지킨다."""
    for attempt in range(3):
        with _ncbi_lock:
            wait = _ncbi_last[0] + 0.4 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _ncbi_last[0] = time.monotonic()
        res = session.get(url, params=params, timeout=TIMEOUT)
        if res.status_code == 429:
            time.sleep(1.5 * (attempt + 1))
            continue
        res.raise_for_status()
        return res
    res.raise_for_status()


def norm_title(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def title_similarity(a: str, b: str) -> float:
    """'한글 제목 = English title'처럼 병기된 제목은 각 부분끼리도 비교해 가장 높은 값."""
    parts_a = [p for p in re.split(r"\s+=\s+", a or "") if p] or [a]
    parts_b = [p for p in re.split(r"\s+=\s+", b or "") if p] or [b]
    best = 0.0
    for x in parts_a + [a]:
        for y in parts_b + [b]:
            nx, ny = norm_title(x), norm_title(y)
            if nx and ny:
                best = max(best, difflib.SequenceMatcher(None, nx, ny).ratio())
    return best


def english_part(title: str) -> str:
    """병기 제목에서 영문 부분 (없으면 영문 비율이 높은 경우 제목 전체)."""
    parts = [p.strip() for p in re.split(r"\s+=\s+", title or "") if p.strip()]
    def latin_ratio(s):
        return sum("a" <= c.lower() <= "z" for c in s) / max(1, len(s))
    best = max(parts, key=latin_ratio, default="")
    return best if latin_ratio(best) > 0.5 else ""


def pubmed_by_title(title: str, year: int | None, session: requests.Session, min_score: float = 0.9) -> dict:
    """영문 제목으로 PubMed에서 찾아 초록·서지를 돌려준다 (못 찾으면 빈 dict)."""
    eng = english_part(title)
    if not eng:
        return {}
    words = re.sub(r"[^\w\s\-]", " ", eng).split()[:15]
    term = " ".join(words) + "[ti]" if words else ""
    if not term:
        return {}
    if year:
        term = f"({' AND '.join(w + '[ti]' for w in words)}) AND {year - 1}:{year + 1}[dp]"
    try:
        ids = _ncbi_get(session, f"{EUTILS}/esearch.fcgi", {
            "db": "pubmed", "retmode": "json", "retmax": 5, "tool": TOOL, "term": term,
        }).json()["esearchresult"]["idlist"]
        if not ids:
            return {}
        xml = _ncbi_get(session, f"{EUTILS}/efetch.fcgi", {
            "db": "pubmed", "retmode": "xml", "tool": TOOL, "id": ",".join(ids),
        }).text
    except (requests.RequestException, ValueError, KeyError):
        return {}
    best, best_score = {}, 0.0
    for art in ET.fromstring(xml).iter("PubmedArticle"):
        rec = _parse_pubmed_xml(art)
        score = title_similarity(eng, rec["title"])
        if score > best_score:
            best, best_score = rec, score
    return best if best_score >= min_score else {}


def _parse_pubmed_xml(art) -> dict:
    def text(el):
        return "".join(el.itertext()).strip() if el is not None else ""
    a = art.find(".//Article")
    journal = a.find("Journal")
    issue = journal.find("JournalIssue") if journal is not None else None
    pub = issue.find("PubDate") if issue is not None else None
    year_txt = text(pub.find("Year")) if pub is not None else ""
    if not year_txt and pub is not None:
        year_txt = text(pub.find("MedlineDate"))[:4]
    month_txt = text(pub.find("Month")) if pub is not None else ""
    abstract_parts = []
    for ab in a.findall(".//Abstract/AbstractText"):
        label = ab.get("Label")
        body = text(ab)
        abstract_parts.append(f"{label.capitalize()}: {body}" if label else body)
    authors = []
    for au in a.findall(".//AuthorList/Author"):
        last, initials = text(au.find("LastName")), text(au.find("Initials"))
        if last:
            authors.append(f"{last} {initials}".strip())
        elif au.find("CollectiveName") is not None:
            authors.append(text(au.find("CollectiveName")))
    doi = next((text(e) for e in a.findall("ELocationID") if e.get("EIdType") == "doi"), "")
    if not doi:
        doi = next((text(e) for e in art.findall(".//ArticleIdList/ArticleId") if e.get("IdType") == "doi"), "")
    month = MONTHS.get(month_txt[:3]) if month_txt[:3] in MONTHS else (int(month_txt) if month_txt.isdigit() else None)
    return {
        "pmid": text(art.find(".//PMID")),
        "title": text(a.find("ArticleTitle")),
        "abstract": "\n".join(p for p in abstract_parts if p),
        "journal": text(journal.find("ISOAbbreviation")) if journal is not None else "",
        "volume": text(issue.find("Volume")) if issue is not None else "",
        "issue": text(issue.find("Issue")) if issue is not None else "",
        "pages": text(a.find(".//Pagination/MedlinePgn")),
        "year": int(year_txt) if year_txt.isdigit() else None,
        "month": month,
        "authors": authors,
        "doi": clean_doi(doi),
    }
