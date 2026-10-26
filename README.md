# 문검기 (문헌검색기) v0.3

검색어와 검색기간을 입력 → 검색 결과 확인 → AI 한글 번역 → 엑셀 저장.

## 실행
`run.bat` 더블클릭 → 브라우저에 문검기 화면이 열립니다. (처음 실행 시 필요한 프로그램을 자동 설치)

## AI 한글 번역 방법 (왼쪽 설정 → AI 선택)
기본값은 **🆓 Gemini 무료**입니다.

| 선택 | 비용 | 방식 |
|---|---|---|
| **🆓 무료 · Gemini API** (기본) | 무료 (Google 계정) | 자동. 무료 한도(분당 약 10회) 때문에 천천히 진행 |
| **🆓 무료 · 웹 채팅 복사·붙여넣기** | 무료 (ChatGPT·Gemini 웹, 회사/개인 Copilot) | 문검기가 만든 질문을 채팅에 붙여 넣고, 답변을 다시 붙여 넣음 |
| **💳 유료 · ChatGPT (OpenAI API)** | 사용량만큼 과금 | 자동, 빠름 |
| **💳 유료 · Claude (Anthropic API)** | 사용량만큼 과금 | 자동, 빠름 |
| **💳 유료 · Gemini (Google API)** | 사용량만큼 과금 (결제 설정한 키) | 자동, 빠름. Pro 모델 사용 가능 |

### 구글 Gemini API 키 받는 방법 (무료)
1. https://aistudio.google.com/api-keys 접속 → Google 계정으로 로그인
2. 처음이면 약관 동의 후 **API 키 만들기(Create API key)** 클릭
3. 만들어진 키 오른쪽의 **복사** 버튼으로 키 복사 (`AIza...`로 시작)
4. 문검기 왼쪽 설정 → AI 선택 **🆓 무료 · Gemini API** → **API 키** 칸에 붙여 넣고 **Enter** (✅ 표시 확인)
5. **연결 테스트**를 눌러 ‘연결 성공’ 확인

> 키는 이 PC의 `settings.json`에만 저장되며 깃허브에는 올라가지 않습니다. 키가 외부에 노출되면 같은 페이지에서 삭제 후 새로 만드세요.

### 유료 API 키 (이미 유료로 쓰는 분)
채팅 구독(ChatGPT Plus·Claude Pro·Gemini Advanced 등)과 **API 요금은 별개**입니다. 각 회사에서 API 결제를 설정한 키가 필요합니다.
- ChatGPT: https://platform.openai.com/api-keys (Billing에서 크레딧 충전)
- Claude: https://console.anthropic.com/settings/keys (Billing 설정)
- Gemini 유료: https://aistudio.google.com/api-keys 에서 결제(Billing)를 설정한 프로젝트의 키 — 무료 키에 결제를 붙였다면 같은 키 사용

### 번역 모델 선택
- API 키 입력 후 **🔄 사용 가능한 모델 불러오기** → 그 키로 쓸 수 있는 모델이 **번역 모델** 목록에 나옵니다.
- 목록에 없으면 **✏️ 직접 입력**으로 모델 이름을 적을 수 있습니다.
- Gemini는 **선택한 모델이 붐비면 다른 모델로 자동 전환** 옵션이 있습니다 (끄면 고른 모델만 사용).

### 참고
- Copilot은 무료 API가 없어 복사·붙여넣기로만 사용. 개인 Copilot은 입력 한도(약 4,000자)가 작아 1건씩 보내야 하므로 회사 계정 Copilot·ChatGPT·Gemini 권장.
- AI 작업: **초록 한글 번역(전체)** 기본, 필요 시 **초록 한글 요약** 선택.
- AI 결과는 자동 검사합니다 (빈칸, 초록이 있는데 '초록 확인 안됨', 일본어·한자 섞임, 너무 짧은 번역) → 해당 논문만 다시 요청.

## 사용 순서
1. 왼쪽 **설정**에서 AI 작업·AI·API 키·번역 모델·저장 폴더 입력 — 입력하면 자동 저장, **연결 테스트**로 확인
2. 사이트 탭(현재 KoreaMed)에서 검색어·시작연도·종료연도 입력 → **검색**
   - 동의어는 쉼표로 구분 (예: `thioctic acid, lipoic acid, alpha-lipoic acid`) → OR 검색
3. 결과 표 확인 후 **엑셀 만들기**
   - 파일명: `검색어명(시작-종료)_Koreamed_검색일자.xlsx`
   - 하위 폴더(예: `그리타존정(피오글리타존)`)를 적으면 그 폴더에 저장

## 엑셀 구성
- **문헌정리**: 번호, 출판연도, 출판월, 논문명(원문), 논문명(한글), 서지정보
  (`Lee JH, et al. J Korean Med Assoc. 2026 Jun;69(6):492-501.`), 초록(한글 번역), 연구유형, DOI, ID, 출처 링크
- **원문 초록**: 저자 전체, 초록 원문, 키워드

## 구조 (사이트 추가 시)
- `core/models.py` 공통 논문 형식 · `core/excel.py` 엑셀 · `core/summarizer.py` AI 번역·요약
- `core/koreamed.py` KoreaMed 검색 — RISS·국회도서관·PubMed도 같은 형태(`search()`, `NAME`, `FILE_TAG`)로 추가 후
  `app.py`의 `SOURCES`에 등록하면 탭이 활성화됩니다.
- `settings.json`(API 키 포함)은 이 PC에만 저장되며 깃허브에 올라가지 않습니다 (`.gitignore`).

## 변경 이력
- **v0.3** — AI 선택을 무료(Gemini API·웹 채팅)/유료(ChatGPT·Claude·Gemini API)로 구분, 유료 Gemini 추가, 기본값 Gemini 무료, 키 발급 링크 표시, README에 구글 API 키 받는 방법 추가 (Azure OpenAI 항목 제거)
- **v0.2** — 번역 AI 모델 선택 (API 키로 사용 가능한 모델 목록 불러오기, 직접 입력), Gemini 자동 전환 켜기/끄기,
  화면·엑셀에 버전 표시
- **v0.1** — KoreaMed 검색, AI 한글 번역(Gemini 무료·웹 채팅 복사·붙여넣기·유료 API), 결과 자동 검사·재요청, 엑셀 저장
