import os
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv


load_dotenv()


@dataclass
class Settings:
    """Configuration for the summary Discord bot."""

    discord_token: str
    server_id: int
    owner_user_ids: List[int]
    keywords: List[str]
    timezone: str = "Asia/Karachi"

    gemini_api_key: str = ""
    # Default to the newer google-genai client model you provided
    gemini_model: str = "gemini-2.5-pro"


def _parse_keywords(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _parse_owner_ids(raw: Optional[str], fallback_single: int) -> List[int]:
    """Parse comma-separated owner IDs; include single-owner fallback when set."""
    owner_ids: List[int] = []
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                owner_ids.append(int(part))
            except ValueError:
                continue
    # Preserve backward compatibility with single owner env var
    if fallback_single and fallback_single not in owner_ids:
        owner_ids.append(fallback_single)
    return owner_ids


def get_settings() -> Settings:
    token = os.getenv("SUMMARY_DISCORD_TOKEN", "").strip()
    server_id = int(os.getenv("SUMMARY_SERVER_ID", "0") or 0)
    owner_user_id = int(os.getenv("SUMMARY_OWNER_USER_ID", "0") or 0)
    owner_user_ids = _parse_owner_ids(
        os.getenv("SUMMARY_OWNER_USER_IDS", ""),
        fallback_single=owner_user_id,
    )
    keywords_raw = os.getenv("SUMMARY_KEYWORDS", "")
    tz = os.getenv("SUMMARY_TIMEZONE", "Asia/Karachi")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")

    return Settings(
        discord_token=token,
        server_id=server_id,
        owner_user_ids=owner_user_ids,
        keywords=_parse_keywords(keywords_raw),
        timezone=tz,
        gemini_api_key=gemini_key,
        gemini_model=gemini_model,
    )


