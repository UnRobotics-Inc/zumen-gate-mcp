"""Settings. Secrets come from the environment, never from command-line arguments."""

from __future__ import annotations

import locale as _locale
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_API_URL = "https://api.zumen-gate.com"
# Where people get a free API key and read the price list
DEVELOPERS_URL = "https://zumen-gate.com/developers"
KEYS_URL = "https://zumen-gate.com/account#api-keys"

WORK_DIR_NAME = ".zumen-gate"
OUT_DIR_NAME = "zumen-gate-out"
# A conversion that takes longer than this returns a job ID; the client keeps waiting
LONG_WAIT_SEC = 40.0


def detect_locale(env: Mapping[str, str]) -> str:
    """'ja' for a Japanese environment, otherwise 'en'. ZUMEN_GATE_LOCALE wins."""
    for name in ("ZUMEN_GATE_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(name, "").strip()
        if value:
            return "ja" if value.lower().startswith("ja") else "en"
    try:
        current = _locale.getlocale()[0] or ""
    except ValueError:
        current = ""
    # Windows reports names such as "Japanese_Japan"
    return "ja" if current.lower().startswith(("ja", "japanese")) else "en"


@dataclass(frozen=True)
class Config:
    api_key: str | None
    api_url: str
    root: Path
    locale: str = "en"
    only_text_feedback: bool = False
    long_wait_sec: float = LONG_WAIT_SEC

    @property
    def work_dir(self) -> Path:
        return self.root / WORK_DIR_NAME

    @property
    def out_dir(self) -> Path:
        return self.root / OUT_DIR_NAME


def load_config(
    root: str | os.PathLike[str] | None = None,
    *,
    only_text_feedback: bool = False,
    env: Mapping[str, str] | None = None,
) -> Config:
    env = os.environ if env is None else env
    key = env.get("ZUMEN_GATE_API_KEY", "").strip() or None
    url = (env.get("ZUMEN_GATE_API_URL", "").strip() or DEFAULT_API_URL).rstrip("/")
    base = Path(root if root is not None else os.getcwd()).expanduser().resolve()
    return Config(
        api_key=key,
        api_url=url,
        root=base,
        locale=detect_locale(env),
        only_text_feedback=only_text_feedback,
    )
