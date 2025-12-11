import os
from dataclasses import dataclass
from typing import List

from dotenv import load_dotenv


load_dotenv()


@dataclass
class Settings:
    """Configuration for the summary Discord bot."""

    discord_token: str
    server_id: int
    owner_user_id: int
    keywords: List[str]
    timezone: str = "Asia/Karachi"

    gemini_api_key: str = ""
    # Default to the newer google-genai client model you provided
    gemini_model: str = "gemini-2.5-pro"


def _parse_keywords(raw: str | None) -> List[str]:
    if not raw:
        return []
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def get_settings() -> Settings:
    token = os.getenv("SUMMARY_DISCORD_TOKEN", "").strip()
    server_id = int(os.getenv("SUMMARY_SERVER_ID", "0") or 0)
    owner_user_id = int(os.getenv("SUMMARY_OWNER_USER_ID", "0") or 0)
    keywords_raw = os.getenv("SUMMARY_KEYWORDS", "")
    tz = os.getenv("SUMMARY_TIMEZONE", "Asia/Karachi")

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")

    return Settings(
        discord_token=token,
        server_id=server_id,
        owner_user_id=owner_user_id,
        keywords=_parse_keywords(keywords_raw),
        timezone=tz,
        gemini_api_key=gemini_key,
        gemini_model=gemini_model,
    )


