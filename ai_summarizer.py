import asyncio
import importlib
import logging
from typing import List, Dict, Any, Optional

from config import Settings


# Lazy import so the bot can run without google-genai installed.
try:
    _genai_mod = importlib.util.find_spec("google.genai") if importlib.util.find_spec("google") else None
except ModuleNotFoundError:
    _genai_mod = None

_client = None
logger = logging.getLogger("summary_bot.ai")


def init_gemini(settings: Settings) -> None:
    """Configure the Gemini client once at startup using google-genai."""
    global _client
    if not settings.gemini_api_key:
        return
    if _genai_mod is None:
        logger.warning("GEMINI_API_KEY provided but google-genai is not installed; skipping Gemini init.")
        return
    if _client is None:
        genai = importlib.import_module("google.genai")
        _client = genai.Client(api_key=settings.gemini_api_key)


def _build_prompt(compact_data: str) -> str:
    """
    Build a compact prompt from pre-parsed channel data.
    """
    return (
        "You are analyzing pre-parsed Instagram automation results.\n\n"
        "Each line below has the format:\n"
        "device_name|status|account1:scheduled_status:scheduled_follows:"
        "actual_follows+actual_requests:blocked(y/n),...\n\n"
        "Status values:\n"
        "- ok:Method X → phone completed task with method X\n"
        "- no_task → schedule exists but no final update found\n"
        "- error:... → error occurred\n\n"
        "OUTPUT FORMAT (follow EXACTLY):\n\n"
        "For each phone, output:\n"
        "1) Phone line (NO indentation):\n"
        '   "<device_name> – completed daily task (Method X)" if status=ok:Method X\n'
        '   "<device_name> – no daily task made" if status=no_task\n'
        '   "<device_name> – Error: <message>" if status=error:...\n\n'
        "2) Account lines (EXACTLY 3 spaces then asterisk for ALL accounts):\n"
        '   "   * <username> – off" if scheduled_status=Off\n'
        '   "   * <username> – blocked" if blocked=y\n'
        '   "   * <username>" (no stats) if scheduled_status=Method 9\n'
        '   Otherwise calculate total = actual_follows + actual_requests:\n'
        '   "   * <username> - total # of follows made: <total> (met the daily max which is <scheduled>)" if total >= scheduled\n'
        '   "   * <username> - total # of follows made: <total> (didn\'t met the daily max which is <scheduled>)" if total < scheduled\n\n'
        "CRITICAL RULES:\n"
        "- ALL account lines use exactly \"   * \" (3 spaces + asterisk + space)\n"
        "- Do NOT nest bullets or increase indentation\n"
        "- Do NOT add headers, dates, or commentary\n"
        "- Output ONLY phone lines and account bullets\n\n"
        "DATA:\n"
        f"{compact_data}\n"
    )


async def summarize_with_gemini(
    settings: Settings,
    compact_data: str,
) -> Optional[str]:
    """Call Gemini with compact pre-parsed data."""

    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    if _genai_mod is None:
        raise RuntimeError("google-genai is not installed; install it to use Gemini.")

    if not compact_data:
        return ""

    prompt = _build_prompt(compact_data)
    logger.info("Sending prompt to Gemini (%d characters):\n%s", len(prompt), prompt)

    loop = asyncio.get_running_loop()

    def _call_gemini() -> str:
        global _client
        if _client is None:
            genai = importlib.import_module("google.genai")
            _client = genai.Client(api_key=settings.gemini_api_key)

        response = _client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
        )
        return (getattr(response, "text", "") or "").strip()

    try:
        response_text = await loop.run_in_executor(None, _call_gemini)
        return response_text.strip()
    except Exception as exc:
        logger.error("Gemini API error: %s", exc)
        return None
