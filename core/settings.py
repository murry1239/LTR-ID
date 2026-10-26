"""사용자 설정 저장 (settings.json, 이 PC에만 저장)."""
import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "settings.json"

DEFAULTS = {
    "output_dir": r"D:\04 갱신_치즈브라우니\26년 4분기\문헌정리",
    "provider": "Gemini (Google) - 무료",
    "models": {},
    "api_keys": {},
    "azure_endpoint": "",
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return data


def save(data: dict) -> None:
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
