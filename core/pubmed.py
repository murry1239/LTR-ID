"""PubMed(https://pubmed.ncbi.nlm.nih.gov) 검색 — NCBI E-utilities (무료·키 불필요).

고급검색(Advanced)에서 [All Fields]로 검색어를 넣으면 필드 태그 없이 들어가므로 그대로 따른다:
  (검색어) AND ("YYYY/MM/DD"[Date - Publication] : "YYYY/MM/DD"[Date - Publication]) AND "free full text"[Filter]
동의어 여러 개는 OR로 묶는다.
"""
import re
import xml.etree.ElementTree as ET
from datetime import date

import requests

from .enrich import EUTILS, TOOL, _ncbi_get
from .log import get_logger
from .models import MONTHS, Article

log = get_logger()

NAME = "PubMed"
FILE_TAG = "Pubmed"
FIRST_QUERY_LABEL = "검색어 (All Fields)"
DATE_RANGE = True  # 화면에서 연도 대신 발행일(년/월/일) 범위를 받는다
OPTIONS = {"free_full_text": ("Free Full Text만 (무료 원문이 있는 논문만)", True)}
BATCH = 200
MONTH_ABBR = {v: k for k, v in MONTHS.items()}


def build_query(terms: list[str], date_from: date, date_to: date, free_full_text: bool = True) -> str:
    words = f"({terms[0]})" if len(terms) == 1 else "(" + " OR ".join(f"({t})" for t in terms) + ")"
    q = (f'{words} AND ("{date_from:%Y/%m/%d}"[Date - Publication] : '
         f'"{date_to:%Y/%m/%d}"[Date - Publication])')
    return q + (' AND "free full text"[Filter]' if free_full_text else "")


def make_filename(name: str, date_from: date, date_to: date, today: date, count: int) -> str:
    """[검색어명(검색기간)_Pubmed_검색일자_갯수]  예: Vitamin B-complex with Vitamin C(22.03.23-26.09.23)_Pubmed_261019_150.xlsx"""
    safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return f"{safe}({date_from:%y.%m.%d}-{date_to:%y.%m.%d})_{FILE_TAG}_{today:%y%m%d}_{count}.xlsx"


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _month(value: str):
    value = (value or "").strip()
    if value[:3] in MONTHS:
        return MONTHS[value[:3]]
    return int(value) if value.isdigit() and 1 <= int(value) <= 12 else None


def _authors(nodes) -> list[str]:
    out = []
    for au in nodes:
        name = f"{_text(au.find('LastName'))} {_text(au.find('Initials'))}".strip()
        out.append(name or _text(au.find("CollectiveName")))
    return [a for a in out if a]


def _abstract(nodes) -> str:
    parts = []
    for ab in nodes:
        body = _text(ab)
        if body:
            label = ab.get("Label")
            parts.append(f"{label.capitalize()}: {body}" if label else body)
    return "\n".join(parts)


def _article(node) -> Article:
    med = node.find("MedlineCitation")
    art = med.find("Article")
    pmid = _text(med.find("PMID"))
    journal = art.find("Journal")
    issue = journal.find("JournalIssue")
    pub = issue.find("PubDate")
    year_s, month_s, day_s = (_text(pub.find(t)) for t in ("Year", "Month", "Day"))
    if not year_s:  # 'MedlineDate' 형식 (예: 2023 Jan-Feb)
        medline = _text(pub.find("MedlineDate"))
        year_s, month_s = medline[:4], medline[5:8]
    month = _month(month_s)
    day_s = day_s.lstrip("0")  # PubMed 표기: 2026 Jul 3
    date_text = " ".join(x for x in (year_s, MONTH_ABBR.get(month, month_s) if month_s else "", day_s) if x)
    vol, iss = _text(issue.find("Volume")), _text(issue.find("Issue"))
    pages = _text(art.find("./Pagination/MedlinePgn")) or _text(art.find("./Pagination/StartPage"))
    if not pages:  # 쪽 대신 논문번호(e로 시작 등)만 있는 학술지
        pages = next((_text(e) for e in art.findall("ELocationID") if e.get("EIdType") == "pii"), "")
    bib = date_text
    if vol or iss or pages:
        bib += f";{vol}{f'({iss})' if iss else ''}{f':{pages}' if pages else ''}"
    ids = {a.get("IdType"): _text(a) for a in node.findall("./PubmedData/ArticleIdList/ArticleId")}
    pmc = ids.get("pmc", "")
    keywords = "; ".join(_text(k) for k in med.findall("./KeywordList/Keyword"))
    pub_types = [_text(t) for t in art.findall("./PublicationTypeList/PublicationType")]
    note = f"무료 원문(PMC): https://pmc.ncbi.nlm.nih.gov/articles/{pmc}/" if pmc else ""
    return Article(
        source=NAME, source_id=pmid, title=_text(art.find("ArticleTitle")), authors=_authors(art.findall("./AuthorList/Author")),
        journal=(_text(journal.find("ISOAbbreviation")) or _text(journal.find("Title"))).rstrip("."),
        bib=bib + ".", year=int(year_s) if year_s.isdigit() else None, month=month, doi=ids.get("doi", ""),
        abstract=_abstract(art.findall("./Abstract/AbstractText")), keywords=keywords,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        extra={"note": note, "pmcid": pmc, "pub_types": pub_types},
    )


def _book(node) -> Article:
    """PubMed의 책 장(Book chapter) — 예: StatPearls."""
    doc = node.find("BookDocument")
    book = doc.find("Book")
    pmid = _text(doc.find("PMID"))
    year_s = _text(book.find("./PubDate/Year"))
    updated = "-".join(_text(doc.find(f"./ContributionDate/{t}")).zfill(4 if t == "Year" else 2)
                       for t in ("Year", "Month", "Day")) if doc.find("./ContributionDate/Year") is not None else ""
    place = f"{_text(book.find('./Publisher/PublisherLocation'))}: {_text(book.find('./Publisher/PublisherName'))}"
    accession = _text(doc.find("./ArticleIdList/ArticleId[@IdType='bookaccession']"))
    return Article(
        source=NAME, source_id=pmid, title=_text(doc.find("ArticleTitle")), authors=_authors(doc.findall("./AuthorList/Author")),
        journal=f"In: {_text(book.find('BookTitle'))} [Internet]. {place}",
        bib=f"{year_s}{f'. Updated {updated}' if updated else ''}.", year=int(year_s) if year_s.isdigit() else None,
        abstract=_abstract(doc.findall("./Abstract/AbstractText")), url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        extra={"note": f"책 장(Book chapter): https://www.ncbi.nlm.nih.gov/books/{accession}/" if accession else "책 장(Book chapter)",
               "pub_types": ["Book chapter"]},
    )


def search(terms: list[str], date_from: date, date_to: date, progress=None,
           free_full_text: bool = True) -> tuple[str, list[Article]]:
    query = build_query(terms, date_from, date_to, free_full_text)
    with requests.Session() as session:
        found = _ncbi_get(session, f"{EUTILS}/esearch.fcgi", {
            "db": "pubmed", "term": query, "retmode": "json", "retmax": 0, "usehistory": "y", "tool": TOOL,
        }).json()["esearchresult"]
        total = int(found.get("count", 0))
        log.info("[PubMed] %s → %d건 · 해석: %s", query, total, found.get("querytranslation", "")[:300])
        articles: dict[str, Article] = {}
        for start in range(0, total, BATCH):
            xml = _ncbi_get(session, f"{EUTILS}/efetch.fcgi", {
                "db": "pubmed", "query_key": found["querykey"], "WebEnv": found["webenv"], "retstart": start,
                "retmax": BATCH, "retmode": "xml", "tool": TOOL,
            }).content
            root = ET.fromstring(xml)
            for node in root.findall("PubmedArticle"):
                a = _article(node)
                articles.setdefault(a.source_id, a)
            for node in root.findall("PubmedBookArticle"):
                a = _book(node)
                articles.setdefault(a.source_id, a)
            if progress:
                progress(min(start + BATCH, total), total)
    if len(articles) < total:
        log.warning("[PubMed] 일부만 받음: %d/%d건", len(articles), total)
    return query, list(articles.values())
