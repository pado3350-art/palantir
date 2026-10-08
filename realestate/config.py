"""환경변수와 `.env` 파일에서 설정을 읽는다."""

import os
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """`KEY=VALUE` 형식의 .env 파일을 읽어 아직 설정되지 않은 환경변수만 채운다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def get_api_key() -> str:
    load_dotenv()
    key = os.environ.get("REB_API_KEY", "")
    if not key:
        raise SystemExit("REB_API_KEY가 설정되지 않았습니다. .env.example을 참고해 .env를 만드세요.")
    return key
