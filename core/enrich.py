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
