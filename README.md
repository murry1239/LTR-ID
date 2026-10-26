# 문검기 (문헌검색기)

검색어와 검색기간을 입력 → 검색 결과 확인 → AI 한글 요약 → 엑셀 저장.

## 실행
`run.bat` 더블클릭 → 브라우저에 문검기 화면이 열립니다. (처음 실행 시 필요한 프로그램을 자동 설치)

## AI 한글 번역 방법 (왼쪽 설정)
| 선택 | 비용 | 방식 |
|---|---|---|
| **Gemini (Google) - 무료** | 무료 (Google 계정, https://aistudio.google.com/apikey 에서 키 발급) | 자동. 무료 한도 때문에 천천히 진행 |
| **웹 채팅 복사·붙여넣기** | 무료 (ChatGPT·Gemini 웹, 회사/개인 Copilot) | 문검기가 만든 질문을 채팅에 붙여 넣고, 답변을 다시 붙여 넣음 |
| Claude / ChatGPT / Azure OpenAI API | 유료 (API 크레딧 필요 — ChatGPT Plus 구독과 별개) | 자동 |

- Copilot은 무료 API가 없어 복사·붙여넣기로만 사용. 개인 Copilot은 입력 한도(약 4,000자)가 작아 1건씩 보내야 하므로 회사 계정 Copilot·ChatGPT·Gemini 권장.
- AI 작업: **초록 한글 번역(전체)** 기본, 필요 시 **초록 한글 요약** 선택.

## 사용 순서
1. 왼쪽 **설정**에서 AI 작업·AI·(API 방식이면) 모델·API 키, 저장 폴더 입력 — 입력하면 자동 저장, **연결 테스트**로 확인
2. 사이트 탭(현재 KoreaMed)에서 검색어·시작연도·종료연도 입력 → **검색**
   - 동의어는 쉼표로 구분 (예: `thioctic acid, lipoic acid, alpha-lipoic acid`) → OR 검색
3. 결과 표 확인 후 **엑셀 만들기**
   - 파일명: `검색어명(시작-종료)_Koreamed_검색일자.xlsx`
   - 하위 폴더(예: `그리타존정(피오글리타존)`)를 적으면 그 폴더에 저장

## 엑셀 구성
- **문헌정리**: 번호, 출판연도, 출판월, 논문명(원문), 논문명(한글 요약), 서지정보
  (`Lee JH, et al. J Korean Med Assoc. 2026 Jun;69(6):492-501.`), 초록 요약(한글), 연구유형, DOI, ID, 출처 링크
- **원문 초록**: 저자 전체, 초록 원문, 키워드

## 구조 (사이트 추가 시)
- `core/models.py` 공통 논문 형식 · `core/excel.py` 엑셀 · `core/summarizer.py` AI 요약
- `core/koreamed.py` KoreaMed 검색 — RISS·국회도서관·PubMed도 같은 형태(`search()`, `NAME`, `FILE_TAG`)로 추가 후
  `app.py`의 `SOURCES`에 등록하면 탭이 활성화됩니다.
