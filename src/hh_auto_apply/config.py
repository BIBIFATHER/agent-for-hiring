from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "y", "on"}


def int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str
    user_agent: str
    resume_id: str
    cover_letter: str
    dry_run: bool
    max_applications_per_run: int
    request_delay_seconds: float
    skip_if_letter_required: bool
    skip_if_no_response_url: bool
    browser_profile_dir: Path
    browser_headless: bool
    browser_search_url: str
    search_profiles_file: Path
    cover_letter_rules_file: Path
    llm_enabled: bool
    openai_api_key: str
    openai_model: str
    llm_timeout_seconds: float
    token_file: Path
    db_file: Path
    search_params: dict[str, str]

    @classmethod
    def from_env(cls, cwd: Path | None = None) -> "Settings":
        base = cwd or Path.cwd()
        load_dotenv(base / ".env")

        search_params: dict[str, str] = {
            "text": os.environ.get("HH_SEARCH_TEXT", ""),
            "area": os.environ.get("HH_SEARCH_AREA", ""),
            "schedule": os.environ.get("HH_SEARCH_SCHEDULE", ""),
            "employment": os.environ.get("HH_SEARCH_EMPLOYMENT", ""),
            "experience": os.environ.get("HH_SEARCH_EXPERIENCE", ""),
            "period": os.environ.get("HH_SEARCH_PERIOD", "1"),
            "per_page": os.environ.get("HH_SEARCH_PER_PAGE", "50"),
        }
        search_params = {k: v for k, v in search_params.items() if v}

        token_file = Path(os.environ.get("HH_TOKEN_FILE", "data/tokens.json"))
        db_file = Path(os.environ.get("HH_DB_FILE", "data/hh_auto_apply.sqlite3"))
        if not token_file.is_absolute():
            token_file = base / token_file
        if not db_file.is_absolute():
            db_file = base / db_file
        browser_profile_dir = Path(os.environ.get("HH_BROWSER_PROFILE_DIR", "data/browser-profile"))
        if not browser_profile_dir.is_absolute():
            browser_profile_dir = base / browser_profile_dir
        search_profiles_file = Path(os.environ.get("HH_SEARCH_PROFILES_FILE", "search_profiles.json"))
        if not search_profiles_file.is_absolute():
            search_profiles_file = base / search_profiles_file
        cover_letter_rules_file = Path(os.environ.get("HH_COVER_LETTER_RULES_FILE", "cover_letter_rules.json"))
        if not cover_letter_rules_file.is_absolute():
            cover_letter_rules_file = base / cover_letter_rules_file

        return cls(
            client_id=os.environ.get("HH_CLIENT_ID", ""),
            client_secret=os.environ.get("HH_CLIENT_SECRET", ""),
            redirect_uri=os.environ.get("HH_REDIRECT_URI", "http://127.0.0.1:8765/callback"),
            user_agent=os.environ.get("HH_USER_AGENT", "hh-auto-apply/0.1"),
            resume_id=os.environ.get("HH_RESUME_ID", ""),
            cover_letter=os.environ.get("HH_COVER_LETTER", ""),
            dry_run=bool_env("HH_DRY_RUN", True),
            max_applications_per_run=int_env("HH_MAX_APPLICATIONS_PER_RUN", 20),
            request_delay_seconds=float_env("HH_REQUEST_DELAY_SECONDS", 1.0),
            skip_if_letter_required=bool_env("HH_SKIP_IF_LETTER_REQUIRED", True),
            skip_if_no_response_url=bool_env("HH_SKIP_IF_NO_RESPONSE_URL", True),
            browser_profile_dir=browser_profile_dir,
            browser_headless=bool_env("HH_BROWSER_HEADLESS", True),
            browser_search_url=os.environ.get("HH_BROWSER_SEARCH_URL", ""),
            search_profiles_file=search_profiles_file,
            cover_letter_rules_file=cover_letter_rules_file,
            llm_enabled=bool_env("HH_LLM_ENABLED", False),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            openai_model=os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
            llm_timeout_seconds=float_env("HH_LLM_TIMEOUT_SECONDS", 6.0),
            token_file=token_file,
            db_file=db_file,
            search_params=search_params,
        )

    @property
    def search_pages(self) -> int:
        return int_env("HH_SEARCH_PAGES", 2)

    def require_oauth_config(self) -> None:
        missing = [
            name
            for name, value in {
                "HH_CLIENT_ID": self.client_id,
                "HH_CLIENT_SECRET": self.client_secret,
                "HH_REDIRECT_URI": self.redirect_uri,
            }.items()
            if not value
        ]
        if missing:
            raise SystemExit(f"Missing required env values: {', '.join(missing)}")
