"""모든 검색 사이트가 공통으로 쓰는 논문 데이터 형식."""
from dataclasses import dataclass, field

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


@dataclass
class Article:
    source: str            # 검색 사이트 이름 (예: "KoreaMed")
    source_id: str         # 사이트 고유번호 (KoreaMed ID 등)
    title: str             # 원문 논문명
    authors: list[str]
    journal: str           # 학술지 약어 (예: "J Korean Med Assoc")
    bib: str               # 발행정보 (예: "2026 Jun;69(6):492-501.")
    year: int | None = None
    month: int | None = None
    doi: str = ""
    abstract: str = ""
    keywords: str = ""
    url: str = ""
    # AI 요약 결과
    title_ko: str = ""
    abstract_ko: str = ""
    study_type: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        """[제1 저자, et al. 학술지명. 발행정보] 형식. 단독 저자는 et al. 생략."""
        first = self.authors[0] if self.authors else ""
        if len(self.authors) > 1:
            lead = f"{first}, et al."
        else:
            lead = f"{first}." if first else ""
        journal = self.journal.rstrip(".") + "." if self.journal else ""
        return " ".join(p for p in (lead, journal, self.bib) if p)

    @property
    def has_abstract(self) -> bool:
        return bool(self.abstract.strip())
