"""국회도서관(dl.nanet.go.kr) — 검색결과 파일(‘전체자료 내보내기’ .xls) 불러오기.

국회도서관은 프로그램의 직접 검색 요청을 막고 있어, 사용자가 사이트에서 검색한 뒤
‘전체자료 내보내기’로 받은 파일을 올리면 문검기가 정리한다.
결과 파일에는 제어번호·자료명·발행자·발행년만 있으므로, 같은 논문을 제목으로
RISS → PubMed 순서로 찾아 저자·학술지·권호·초록을 채운다 (국회도서관에는 자동 접속하지 않음).
"""
import io
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from . import riss
from .enrich import pubmed_by_title
from .log import get_logger
from .models import MONTHS, Article

log = get_logger()

NAME = "국회도서관"
FILE_TAG = "NAL"
UPLOAD = True  # 화면에서 검색어 대신 결과 파일을 올린다
FIRST_QUERY_LABEL = "한글 검색어"
SECOND_QUERY_LABEL = "영문 검색어 (선택)"
DETAIL_URL = "https://dl.nanet.go.kr/search/searchInnerDetail.do?controlNo={}"
WORKERS = 4
MONTH_ABBR = {v: k for k, v in MONTHS.items()}

GUIDE = """**국회도서관 검색결과 파일 받는 방법**
1. [국회도서관 dl.nanet.go.kr](https://dl.nanet.go.kr) → **고급검색** → **상세검색** → **연속간행물·학술기사** → **‘전체’**
2. 검색분류 **‘전체항목’**에 검색어 입력, **발행연도** 범위 지정 → 검색
3. 결과 화면에서 **‘전체자료 내보내기’** → 받은 `.xls` 파일을 아래에 올리기
4. 영문 검색어도 같은 방법으로 받아 두 번째 칸에 올리기 (선택)"""


def read_export(data: bytes, filename: str = "") -> list[dict]:
    """‘전체자료 내보내기’ 파일 → [{control_no, title, publisher, year, kind}]"""
    rows = []
    if filename.lower().endswith(".xlsx"):
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(data), read_only=True).active
        values = [[("" if v is None else str(v)) for v in r] for r in ws.iter_rows(values_only=True)]
    else:
        import xlrd
        sh = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
        values = [[str(v) for v in sh.row_values(i)] for i in range(sh.nrows)]
    if not values:
        return rows
    header = [h.strip() for h in values[0]]

    def col(name):
        return next((i for i, h in enumerate(header) if name in h), None)
    i_id, i_title, i_pub, i_year = col("제어번호"), col("자료명"), col("발행자"), col("발행년")
    if i_id is None or i_title is None:
        raise ValueError("국회도서관 ‘전체자료 내보내기’ 파일이 아닙니다 (제어번호·자료명 열이 없음).")
    for r in values[1:]:
        if not r[i_id].strip():
            continue
        year = re.search(r"(19|20)\d{2}", r[i_year] if i_year is not None else "")
        control = r[i_id].strip()
        rows.append({
            "control_no": control,
            "title": " ".join(re.sub(r"\s*/\s*[^=]*$", "", r[i_title]).split()) if " / " in r[i_title]
            else " ".join(r[i_title].split()),
            "publisher": r[i_pub].strip() if i_pub is not None else "",
            "year": int(year.group(0)) if year else None,
            "kind": {"KINX": "국내기사", "FINX": "국외기사", "SERL": "학술지·잡지"}.get(control[:4], ""),
        })
    return rows


def _match(row: dict, session: requests.Session) -> Article:
    """RISS → PubMed 순서로 같은 논문을 찾아 정보를 채운다."""
    note, art = "", None
    if row["kind"] != "학술지·잡지":
        try:
            art = riss.find_by_title(row["title"], row["year"], session)
            if art:
                note = "초록·서지: RISS에서 확인"
        except requests.RequestException as e:
            log.warning("RISS 제목 대조 실패 (%s): %s", row["control_no"], e)
        if art is None or not art.has_abstract:
            pm = pubmed_by_title(row["title"], row["year"], session)
            if pm:
                if art is None:
                    month = pm.get("month")
                    bib = f"{pm.get('year') or row['year']}{' ' + MONTH_ABBR[month] if month else ''}"
                    if pm.get("volume"):
                        bib += f";{pm['volume']}{'(' + pm['issue'] + ')' if pm.get('issue') else ''}"
                    if pm.get("pages"):
                        bib += f":{pm['pages']}"
                    art = Article(source=NAME, source_id=row["control_no"], title=row["title"],
                                  authors=pm["authors"], journal=pm["journal"], bib=bib + ".",
                                  year=pm.get("year") or row["year"], month=month, doi=pm["doi"],
                                  abstract=pm["abstract"], url=f"https://pubmed.ncbi.nlm.nih.gov/{pm['pmid']}/")
                    art.source = "PubMed"
                    note = "초록·서지: PubMed에서 확인"
                elif pm.get("abstract"):
                    art.abstract = pm["abstract"]
                    note = "서지: RISS, 초록: PubMed에서 확인"
    if art is None:
        art = Article(source=NAME, source_id=row["control_no"], title=row["title"], authors=[],
                      journal=row["publisher"], bib=f"{row['year']}." if row["year"] else "", year=row["year"])
        note = ("학술지 단위 자료 (개별 논문 아님)" if row["kind"] == "학술지·잡지"
                else "RISS·PubMed에서 같은 논문을 찾지 못함 — 국회도서관 원문 확인 필요")
    # 국회도서관 기준 정보로 맞추기 (제어번호·제목·링크)
    other_url = art.url if art.source != NAME else ""
    art.source, art.source_id, art.title = NAME, row["control_no"], row["title"]
    art.url = DETAIL_URL.format(row["control_no"])
    if row["year"] and art.year != row["year"]:
        note += f" (발행년: 국회도서관 {row['year']} / 대조 자료 {art.year})"
        art.year = row["year"]
    art.extra.update({"note": note, "kind": row["kind"], "matched_url": other_url, "publisher": row["publisher"]})
    return art


def load(data: bytes, filename: str, year_from: int, year_to: int, progress=None) -> tuple[str, list[Article]]:
    """결과 파일을 읽고 대조·보완한 논문 목록을 돌려준다."""
    rows = read_export(data, filename)
    in_range = [r for r in rows if r["year"] is None or year_from <= r["year"] <= year_to]
    if len(in_range) < len(rows):
        log.info("[국회도서관] 발행연도 %d–%d 밖 %d건 제외", year_from, year_to, len(rows) - len(in_range))
    articles = []
    with requests.Session() as session, ThreadPoolExecutor(max_workers=WORKERS) as pool:
        session.headers.update(riss.HEADERS)
        for n, art in enumerate(pool.map(lambda r: _match(r, session), in_range), 1):
            articles.append(art)
            if progress:  # 화면 갱신은 메인 스레드에서만
                progress(n, len(in_range))
    found = sum(not a.extra["note"].startswith(("RISS·PubMed에서", "학술지 단위")) for a in articles)
    log.info("[국회도서관] %s: %d건 중 %d건 대조 성공, 초록 %d건", filename, len(articles), found,
             sum(a.has_abstract for a in articles))
    desc = (f"국회도서관 연속간행물·학술기사 검색결과 파일 ‘{filename}’ {len(rows)}건"
            f"{f' (발행연도 범위 밖 {len(rows) - len(in_range)}건 제외)' if len(in_range) < len(rows) else ''}")
    return desc, articles
