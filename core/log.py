"""작업 기록(로그) — logs/munkeomgi.log 에 남기고, 화면의 ‘로그 보기’에서 확인한다.

API 키처럼 보이는 문자열은 기록하기 전에 가린다.
"""
import logging
import logging.handlers
import re
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "munkeomgi.log"
_SECRET = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9_\-]{10,})")


class _MaskSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _SECRET.sub(lambda m: m.group(0)[:6] + "…(가림)", record.getMessage())
        record.args = ()
        return True


def get_logger() -> logging.Logger:
    logger = logging.getLogger("munkeomgi")
    if not logger.handlers:  # Streamlit은 화면을 다시 그릴 때마다 코드를 다시 실행하므로 한 번만 설정
        LOG_DIR.mkdir(exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
        handler.addFilter(_MaskSecrets())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def tail(lines: int = 300) -> str:
    """최근 로그 lines줄."""
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "(아직 기록이 없습니다)"
    return "\n".join(text.splitlines()[-lines:])
