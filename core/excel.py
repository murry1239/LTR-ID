"""검색 결과를 엑셀 파일로 저장."""
import re
from collections import Counter
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import Article

FONT = "Malgun Gothic"
NAVY = "214E76"
HEADER_FILL = PatternFill("solid", fgColor=NAVY)
THIN = Side(style="thin", color="CBD5E1")
WRAP_TOP = Alignment(wrap_text=True, vertical="top")


def make_filename(name: str, year_from: int, year_to: int, file_tag: str, today: date) -> str:
    """[검색어명(검색기간)_사이트_검색일자].xlsx  예: pioglitazone(2021-2026)_Koreamed_20261019.xlsx"""
    safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return f"{safe}({year_from}-{year_to})_{file_tag}_{today:%Y%m%d}.xlsx"


def unique_path(folder: Path, filename: str) -> Path:
    path = folder / filename
    n = 2
    while path.exists():
        path = folder / f"{Path(filename).stem}_{n}{Path(filename).suffix}"
        n += 1
    return path


def sort_articles(articles: list[Article]) -> list[Article]:
    return sorted(articles, key=lambda a: (-(a.year or 0), -(a.month or 0)))


def _header(ws, row, titles, widths):
    for col, (title, width) in enumerate(zip(titles, widths), 1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF", size=10)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col)].width = width


def _body_cell(cell):
    cell.font = Font(name=FONT, size=10)
    cell.alignment = WRAP_TOP
    cell.border = Border(bottom=THIN)


def write_workbook(path: Path, articles: list[Article], *, search_name: str, site: str, query: str,
                   year_from: int, year_to: int, today: date, ai_note: str,
                   abstract_header: str = "초록 (한글 번역)") -> Path:
    articles = sort_articles(articles)
    wb = Workbook()

    # 1) 문헌정리
    ws = wb.active
    ws.title = "문헌정리"
    ws.sheet_view.showGridLines = False
    years = Counter(a.year for a in articles)
    year_text = " · ".join(f"{y}년 {years.get(y, 0)}건" for y in range(year_from, year_to + 1))
    info = [
        (f"{search_name} 문헌정리", Font(name=FONT, size=14, bold=True, color=NAVY)),
        (f"{site} 검색. 발행연도 {year_from}–{year_to}년. 총 {len(articles)}건. ({year_text})", None),
        (f"검색일: {today:%Y년 %m월 %d일}    검색식: {query}", None),
        (ai_note, None),
    ]
    for i, (text, font) in enumerate(info, 1):
        cell = ws.cell(row=i, column=1, value=text)
        cell.font = font or Font(name=FONT, size=10, color="475569")

    titles = ["번호", "출판연도", "출판월", "논문명 (원문)", "논문명 (한글)", "서지정보",
              abstract_header, "연구유형", "DOI", f"{site} ID", "출처", "비고"]
    widths = [6, 9, 7, 50, 36, 42, 80, 14, 26, 16, 11, 36]
    head = 6
    _header(ws, head, titles, widths)
    for n, a in enumerate(articles, 1):
        r = head + n
        values = [n, a.year, a.month, a.title, a.title_ko, a.citation, a.abstract_ko, a.study_type,
                  a.doi, a.source_id, site,
                  " ".join(x for x in (a.extra.get("note", ""), a.extra.get("matched_url", "")) if x)]
        for col, v in enumerate(values, 1):
            cell = ws.cell(row=r, column=col, value=v)
            _body_cell(cell)
        for col in (1, 2, 3, 10):
            ws.cell(row=r, column=col).alignment = Alignment(horizontal="center", vertical="top")
        if a.url:
            link = ws.cell(row=r, column=11)
            link.hyperlink = a.url
            link.font = Font(name=FONT, size=10, color="0563C1", underline="single")
        if a.doi:
            ws.cell(row=r, column=9).hyperlink = f"https://doi.org/{a.doi}"
    ws.freeze_panes = ws.cell(row=head + 1, column=5)
    ws.auto_filter.ref = f"A{head}:{get_column_letter(len(titles))}{head + len(articles)}"

    # 2) 원문 초록
    ws2 = wb.create_sheet("원문 초록")
    titles2 = ["번호", f"{site} ID", "논문명 (원문)", "저자", "서지정보", "초록 원문", "키워드"]
    _header(ws2, 1, titles2, [6, 12, 50, 30, 42, 100, 30])
    for n, a in enumerate(articles, 1):
        values = [n, a.source_id, a.title, ", ".join(a.authors), a.citation, a.abstract or "(초록 없음)", a.keywords]
        for col, v in enumerate(values, 1):
            _body_cell(ws2.cell(row=n + 1, column=col, value=v))
    ws2.freeze_panes = "C2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
