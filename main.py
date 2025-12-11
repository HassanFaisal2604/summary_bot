import asyncio
import datetime
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands, tasks
import pytz

from config import get_settings, Settings
from ai_summarizer import init_gemini, summarize_with_gemini


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("summary_bot")

settings: Settings = get_settings()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_day_argument(day_arg: str, timezone: str = "Asia/Karachi") -> Optional[str]:
    """
    Parse day argument into a date string (YYYY-MM-DD).
    
    Accepts: "monday", "tue", "today", "yesterday", "dec 02", "2025-12-02", "12/02"
    """
    tz = pytz.timezone(timezone)
    now = datetime.datetime.now(tz)
    today = now.date()
    
    day_arg_lower = day_arg.lower().strip()
    
    # Handle relative days
    if day_arg_lower == "today":
        return today.strftime("%Y-%m-%d")
    if day_arg_lower == "yesterday":
        return (today - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Handle day names
    day_names = {
        "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
        "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
        "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
    }
    
    if day_arg_lower in day_names:
        target_weekday = day_names[day_arg_lower]
        current_weekday = today.weekday()
        days_ago = (current_weekday - target_weekday) % 7
        if days_ago == 0:
            days_ago = 7  # If today is the target day, get last week's
        target_date = today - datetime.timedelta(days=days_ago)
        return target_date.strftime("%Y-%m-%d")
    
    # Handle "dec 02", "december 2"
    month_names = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    
    month_day_match = re.match(r"(\w+)\s+(\d{1,2})", day_arg_lower)
    if month_day_match:
        month_str, day_str = month_day_match.groups()
        if month_str in month_names:
            month, day = month_names[month_str], int(day_str)
            year = today.year
            try:
                target_date = datetime.date(year, month, day)
                if target_date > today:
                    target_date = datetime.date(year - 1, month, day)
                return target_date.strftime("%Y-%m-%d")
            except ValueError:
                return None
    
    # Handle "2025-12-02"
    if re.match(r"\d{4}-\d{1,2}-\d{1,2}", day_arg):
        return day_arg
    
    # Handle "12/02" or "12-02"
    short_match = re.match(r"(\d{1,2})[/-](\d{1,2})", day_arg)
    if short_match:
        month, day = map(int, short_match.groups())
        try:
            target_date = datetime.date(today.year, month, day)
            if target_date > today:
                target_date = datetime.date(today.year - 1, month, day)
            return target_date.strftime("%Y-%m-%d")
        except ValueError:
            return None
    
    return None


def find_final_update_for_date(
    messages: List[discord.Message], target_date: str
) -> tuple[Optional[str], Optional[int]]:
    """Find Final Update message for a specific date."""
    for idx, msg in enumerate(messages):
        text = extract_message_text(msg)
        if "task final update" not in text.lower():
            continue
        run_date = _extract_run_date_from_text(text)
        if run_date and run_date == target_date:
            return text, idx
    return None, None


def extract_message_text(msg: discord.Message) -> str:
    """Extract text from message - handles both embeds and plain text."""
    if msg.embeds:
        parts: List[str] = []
        for embed in msg.embeds:
            if embed.title:
                parts.append(embed.title)
            if embed.description:
                parts.append(embed.description)
            for field in embed.fields:
                parts.append(f"{field.name}\n{field.value}")
        return "\n".join(parts)
    return msg.content or ""


def find_and_combine_messages(
    messages: List[discord.Message],
    start_keyword: str,
) -> str | None:
    """
    Find the MOST RECENT single message block for the keyword.

    For Schedule: may need to combine Part 1 + Part 2.
    For Final Update: ONLY get the most recent one (don't sum multiple days).
    Messages list must be newest-first.
    """
    # Deprecated helper – kept for backwards compatibility if needed.
    # New code should use extract_schedule_and_final_update instead.
    return None


def _extract_run_date_from_text(text: str) -> Optional[str]:
    """
    Extract the run date from a Final Update message.

    Prefer the explicit Start Time date, but fall back to End Time when
    Start Time is missing (e.g., Method 9 runs that report `Start Time: null`).
    """
    # Primary: Start Time
    start_match = re.search(
        r"start\s*time:\s*(\d{4}-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    if start_match:
        return start_match.group(1)

    # Fallback: End Time (may include time component)
    end_match = re.search(
        r"end\s*time:\s*(\d{4}-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    if end_match:
        return end_match.group(1)

    return None


def combine_final_update_messages(
    messages: List[discord.Message],
    primary_index: Optional[int],
    primary_text: str,
) -> str:
    """
    Combine split Final Update messages (Discord may split large messages).
    messages list must be newest-first. We walk older messages and append
    any continuation that looks like Final Update/Method/Popup content.
    """
    if primary_index is None or primary_index < 0:
        return primary_text

    primary_msg = messages[primary_index]
    primary_ts = getattr(primary_msg, "created_at", None)
    run_date = _extract_run_date_from_text(primary_text)
    window_sec = 300  # 5 minutes; split parts share the same timestamp
    parts: List[tuple[datetime.datetime, str]] = []

    def is_final_like_text(text: str) -> bool:
        tl = text.lower()
        return (
            "task final update" in tl
            or ("automation type" in tl and ("account username" in tl or "account:" in tl))
            or "request pending" in tl
            or "popup detected" in tl
            or ("automation type" in tl and "method 9" in tl)
        )

    def consider(idx: int):
        msg = messages[idx]
        text = extract_message_text(msg)
        if not text or not text.strip():
            return
        if not is_final_like_text(text):
            return
        msg_ts = getattr(msg, "created_at", None)
        if primary_ts and msg_ts:
            if abs((primary_ts - msg_ts).total_seconds()) > window_sec:
                return
        msg_date = _extract_run_date_from_text(text)
        if run_date and msg_date and msg_date != run_date:
            return
        parts.append(
            (msg_ts or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), text)
        )

    # Include primary
    consider(primary_index)
    # Older messages (index > primary_index)
    for i in range(primary_index + 1, len(messages)):
        consider(i)
    # Newer messages (index < primary_index)
    for i in range(0, primary_index):
        consider(i)

    if len(parts) == 1:
        return primary_text

    parts_sorted = sorted(parts, key=lambda p: p[0])
    return "\n".join(p[1] for p in parts_sorted)


def split_schedule_and_final_update(text: str) -> tuple[str | None, str | None]:
    """Split combined text into schedule and final update portions when both exist."""

    text_lower = text.lower()

    final_update_pos = text_lower.find("task final update")
    if final_update_pos == -1:
        # No final update marker; treat as schedule only if it looks like schedule.
        if "schedule" in text_lower or "weekly plan scheduled" in text_lower:
            return text, None
        return None, None

    schedule_part = text[:final_update_pos].strip()
    final_update_part = text[final_update_pos:].strip()

    if "schedule" not in schedule_part.lower() and "weekly plan scheduled" not in schedule_part.lower():
        schedule_part = None

    return schedule_part, final_update_part


def extract_schedule_and_final_update(
    messages: List[discord.Message],
    target_date: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Extract schedule and final update from messages.
    Searches for them as separate messages first, then handles combined case.
    Messages should be newest-first.
    """
    schedule_text: Optional[str] = None
    final_update_text: Optional[str] = None
    reminder_text: Optional[str] = None

    # STEP 1: Find the Final Update message
    final_update_index: Optional[int] = None

    if target_date:
        # Find Final Update for specific date
        final_update_text, final_update_index = find_final_update_for_date(messages, target_date)
        if final_update_text:
            logger.debug("Found final update for target date %s", target_date)
    else:
        # Find most recent Final Update (existing code)
        for idx, msg in enumerate(messages):  # newest first
            text = extract_message_text(msg)
            if not text or not text.strip():
                continue
            text_lower = text.lower()

            has_final_marker = "task final update" in text_lower
            has_account = "account username:" in text_lower or "account:" in text_lower
            has_method = "automation type" in text_lower or "method 9" in text_lower
            has_popup = "request pending" in text_lower or "popup detected" in text_lower

            if has_final_marker and (has_account or has_method or has_popup):
                final_update_text = text
                final_update_index = idx
                logger.debug("Found final update message: %d characters", len(final_update_text))
                break

        # Fallback: capture popup/method messages even if "Task Final Update" is missing
        if not final_update_text:
            for idx, msg in enumerate(messages):
                text = extract_message_text(msg)
                if not text or not text.strip():
                    continue
                text_lower = text.lower()
                has_popup = "request pending" in text_lower or "popup detected" in text_lower
                has_method9 = "method 9" in text_lower
                has_stats = "stats" in text_lower or "automation type" in text_lower or "device name" in text_lower
                has_account = "account username:" in text_lower or "account:" in text_lower
                if (has_popup or has_method9) and has_stats and has_account:
                    final_update_text = text
                    final_update_index = idx
                    logger.debug("Captured fallback final update (popup/method9) message: %d characters", len(final_update_text))
                    break

    # STEP 1b: If found, append any continuation parts (Discord splits long messages)
    if final_update_text and final_update_index is not None:
        final_update_text = combine_final_update_messages(
            messages,
            primary_index=final_update_index,
            primary_text=final_update_text,
        )

    # STEP 2: Find the most recent Schedule message
    for msg in messages:  # newest first
        text = extract_message_text(msg)
        if not text or not text.strip():
            continue
        text_lower = text.lower()

        is_daily_reminder = (
            "daily schedule reminder" in text_lower
            or ("start time:" in text_lower and "accounts:" in text_lower and "task:" in text_lower)
        )
        if is_daily_reminder and reminder_text is None:
            reminder_text = text
            logger.debug("Captured daily reminder message: %d characters", len(reminder_text))

        # Must look like a schedule (has day markers or Accounts: section)
        has_schedule_markers = (
            ("schedule" in text_lower and ("📅" in text or "accounts:" in text_lower))
            or "weekly plan scheduled" in text_lower
        )

        if has_schedule_markers:
            # If this message also contains the final update we found, split it
            if final_update_text and text == final_update_text:
                # Combined message - split at "Task Final Update"
                match = re.search(r"task\s+final\s+update", text, re.IGNORECASE)
                if match:
                    schedule_text = text[:match.start()].strip()
                    # final_update_text already set correctly above
                    logger.debug("Split combined message: schedule=%d, final=%d", len(schedule_text), len(final_update_text))
                    break
            else:
                # Separate message
                schedule_text = text
                logger.debug("Found separate schedule message: %d characters", len(schedule_text))
                break

    # STEP 3: Handle Part 2 continuation for schedule
    if schedule_text:
        # Look for Part 2 message that continues the schedule
        for msg in messages:
            text = extract_message_text(msg)
            if not text or not text.strip():
                continue
            text_lower = text.lower()
            if "(part 2)" in text_lower and "task final update" not in text_lower:
                # Append Part 2 to schedule
                schedule_text = schedule_text + "\n" + text
                logger.debug("Appended Part 2 to schedule, total length now %d", len(schedule_text))
                break

    # Prefer the single-day daily reminder over the multi-day schedule when present
    if reminder_text:
        schedule_text = reminder_text
        logger.debug("Using daily reminder as primary schedule source")

    return schedule_text, final_update_text


@dataclass
class AccountData:
    username: str
    scheduled_follows: int  # 0 if Off
    scheduled_status: str   # "Method 1" or "Off" or "Unknown"
    actual_follows: int     # follows OR unfollows, depending on is_unfollow
    actual_requests: int
    is_blocked: bool
    is_unfollow: bool


@dataclass
class ChannelData:
    channel_name: str
    device_name: str
    method: str  # "Method 1", "Method 9", etc.
    accounts: List[AccountData]
    has_schedule: bool
    has_final_update: bool
    error_message: Optional[str]


def parse_schedule_for_date(
    schedule_text: str,
    run_date: Optional[str] = None,
) -> tuple[str, str, Dict[str, tuple[str, int]]]:
    """
    Parse schedule and match to specific run_date from Final Update.

    Returns: (device_name, method, {username: (status, planned_follows)})
    """
    device_name = "Unknown"
    method = "Method 1"
    accounts: Dict[str, tuple[str, int]] = {}

    lowered = schedule_text.lower()

    # Daily reminder block (single-day schedule) – prefer this over full weekly schedule
    reminder_date_match = re.search(
        r"start\s*time:\s*(\d{4}-\d{2}-\d{2})", schedule_text, re.IGNORECASE
    )
    reminder_task_match = re.search(r"task:\s*(.+?)(?:\n|$)", schedule_text, re.IGNORECASE)
    if reminder_task_match:
        device_name = normalize_device_name(reminder_task_match.group(1).strip())

    section_to_parse = ""
    matched_day = None

    if "daily schedule reminder" in lowered or reminder_date_match:
        if reminder_date_match:
            matched_day = reminder_date_match.group(1)
            if run_date and matched_day != run_date:
                logger.debug(
                    "Reminder start date %s differs from run_date %s; still using reminder",
                    matched_day,
                    run_date,
                )
        logger.debug("Using Daily Schedule Reminder section for schedule parsing")
        section_to_parse = schedule_text

    # Extract device name
    device_match = re.search(
        r"(?:weekly plan scheduled|task):\s*(.+?)(?:\n|$|\s{2,}|\s*📅)",
        schedule_text,
        re.IGNORECASE,
    )
    if device_match:
        device_name = device_match.group(1).strip()

    # Convert run_date "2025-12-02" to day pattern like "Tue Dec 02" and "Dec 02"
    target_patterns = []
    target_day_num = None
    if run_date:
        try:
            dt = datetime.datetime.strptime(run_date, "%Y-%m-%d")
            target_day_num = dt.day
            target_patterns = [
                dt.strftime("%a %b %d"),      # "Tue Dec 02"
                dt.strftime("%a %b") + f" {dt.day}",  # "Tue Dec 2" (no leading zero)
                dt.strftime("%b %d"),         # "Dec 02"
                dt.strftime("%b") + f" {dt.day}",     # "Dec 2"
            ]
            logger.debug("Looking for date patterns: %s (day=%d)", target_patterns, target_day_num)
        except Exception as e:
            logger.warning("Could not parse run_date %s: %s", run_date, e)

    # Only look for multi-day schedule sections if we didn't already pick the reminder
    if not section_to_parse:
        # Find day sections - handle multiple formats:
        # Format 1: "📅 Tue Dec 02 18:22:" or "📅\nTue Dec 02 18:22:"
        # Format 2: "Tue Dec 02 18:22:" (no emoji, from text extraction)
        # Pattern captures: (day string like "Tue Dec 02"), (content until next day or end)
        day_pattern = r"(?:📅\s*\n?\s*)?(\w{3}\s+\w{3}\s+\d{1,2})\s+[\d:]+\s*:(.*?)(?=(?:📅\s*\n?\s*)?\w{3}\s+\w{3}\s+\d{1,2}\s+[\d:]+\s*:|$)"
        
        day_matches = re.findall(day_pattern, schedule_text, re.DOTALL | re.IGNORECASE)
        
        logger.debug("Found %d day sections in schedule", len(day_matches))
        for date_str, _ in day_matches:
            logger.debug("  Day section: %s", date_str.strip())

        if day_matches and target_patterns:
            for date_str, section in day_matches:
                date_str_clean = ' '.join(date_str.split()).strip()  # Normalize whitespace
                
                # Check if any target pattern matches
                for pattern in target_patterns:
                    if pattern.lower() in date_str_clean.lower():
                        section_to_parse = section
                        matched_day = date_str_clean
                        logger.debug("Matched schedule day: '%s' with pattern '%s'", date_str_clean, pattern)
                        break
                
                if section_to_parse:
                    break
            
            if not section_to_parse:
                logger.warning("No schedule day matched for run_date %s, patterns tried: %s", run_date, target_patterns)

    # Fallback: "Accounts:" section (daily reminder format)
    if not section_to_parse:
        accounts_match = re.search(
            r"Accounts:\s*(.*?)(?:$)", schedule_text, re.DOTALL | re.IGNORECASE
        )
        if accounts_match:
            section_to_parse = accounts_match.group(1)
            logger.debug("Using 'Accounts:' section fallback")

    # Last fallback: first day found (with warning)
    if not section_to_parse and day_matches:
        section_to_parse = day_matches[0][1]
        matched_day = day_matches[0][0]
        logger.warning("Using first day as fallback: %s", matched_day)

    # Parse account lines from the matched section
    account_pattern = r"(\w+(?:\.\w+)?)\s*:\s*(Method\s*\d+|Off)(?:,\s*(\d+)\s*follows)?"

    for match in re.finditer(account_pattern, section_to_parse, re.IGNORECASE):
        username = match.group(1)
        
        # Skip garbage
        if username.lower() in ["type", "method", "automation", "stats", "device", "notification"]:
            continue
            
        status = match.group(2)
        follows = int(match.group(3)) if match.group(3) else 0
        accounts[username] = (status, follows)
        
        if "method" in status.lower():
            method = status

    logger.debug("Parsed schedule: device=%s, matched_day=%s, accounts=%s", device_name, matched_day, list(accounts.keys()))
    
    return device_name, method, accounts


def parse_final_update(
    final_update_text: str,
) -> tuple[str, str, Dict[str, tuple[int, int, bool, bool]], Optional[str]]:
    """
    Parse Task Final Update text - keeps LAST occurrence of each account.
    
    Returns: (device_name, run_date, {username: (follows, requests, is_blocked)}, error_message)
    """
    device_name = "Unknown"
    run_date = ""
    accounts: Dict[str, tuple[int, int, bool, bool]] = {}
    error_message: Optional[str] = None

    lowered = final_update_text.lower()

    # Hard error cases – return early with error only
    if "force stopped" in lowered:
        return device_name, run_date, accounts, "Automation force stopped"
    if "disconnected" in lowered:
        return device_name, run_date, accounts, "Devices disconnected"

    popup_detected = None
    if "request pending" in lowered or "popup detected" in lowered:
        popup_detected = "Request pending popup detected"

    # Extract device name (first occurrence)
    device_match = re.search(
        r"device name:\s*(.+?)(?:\n|$)", final_update_text, re.IGNORECASE
    )
    if device_match:
        device_name = device_match.group(1).strip()

    # Extract run date (Start Time preferred, End Time fallback)
    run_date = _extract_run_date_from_text(final_update_text) or ""

    # DON'T split by "Task Final Update" - just parse ALL account sections
    # Split by "---" separators across the ENTIRE text
    sections = re.split(r"-{5,}", final_update_text)

    for section in sections:
        user_match = re.search(
            r"account username:\s*(\w+(?:\.\w+)?)", section, re.IGNORECASE
        )
        if not user_match:
            # Fallback for popup/error messages that use "Account:" instead of "Account Username:"
            user_match = re.search(
                r"account:\s*(\w+(?:\.\w+)?)", section, re.IGNORECASE
            )
        if not user_match:
            continue

        username = user_match.group(1)
        
        # Skip garbage usernames
        if username.lower() in ["type", "method", "automation", "stats", "device", "name", "notification"]:
            logger.debug("Skipping garbage username '%s' from final update", username)
            continue

        follows_match = re.search(
            r"no\.\s*of\s*follow\s*made:\s*(\d+)", section, re.IGNORECASE
        )
        unfollow_match = re.search(
            r"no\.\s*of\s*unfollowed\s*accounts:\s*(\d+)", section, re.IGNORECASE
        )
        follows = 0
        is_unfollow_run = False
        if unfollow_match:
            follows = int(unfollow_match.group(1).replace(",", ""))
            is_unfollow_run = True
        elif follows_match:
            follows = int(follows_match.group(1).replace(",", ""))

        if is_unfollow_run:
            # Unfollow runs should not add follow request counts; force zero
            requests = 0
        else:
            requests_match = re.search(
                r"no\.\s*of\s*follow\s*requests\s*made:\s*(\d+)", section, re.IGNORECASE
            )
            if requests_match:
                requests = int(requests_match.group(1).replace(",", ""))
            else:
                # Handle "no. of follow requests: <text + N others>" by capturing the trailing number
                requests_fallback = re.search(
                    r"no\.\s*of\s*follow\s*requests[^0-9]*(\d+)",
                    section,
                    re.IGNORECASE,
                )
                requests = (
                    int(requests_fallback.group(1).replace(",", ""))
                    if requests_fallback
                    else 0
                )

        blocked_match = re.search(
            r"account actions blocked:\s*(true|false)", section, re.IGNORECASE
        )
        is_blocked = blocked_match and blocked_match.group(1).lower() == "true"

        # FIX: ALWAYS overwrite - keep LAST value (from Task Final Update, not Task Update)
        if username in accounts:
            old_follows, old_requests, _, _ = accounts[username]
            logger.info(
                f"Overwriting account {username}: was {old_follows}+{old_requests}, now {follows}+{requests}"
            )
        
        accounts[username] = (follows, requests, is_blocked, is_unfollow_run)
        
        logger.info(
            f"Parsed account {username}: follows={follows}, requests={requests}, "
            f"blocked={is_blocked} (total={follows + requests})"
        )

    # If we detected the popup and didn't capture an account (or any data), set an error message.
    if popup_detected:
        error_message = popup_detected
        if not accounts:
            popup_account_match = re.search(
                r"account:\s*(\w+(?:\.\w+)?)", final_update_text, re.IGNORECASE
            )
            popup_account = popup_account_match.group(1) if popup_account_match else "unknown"
            accounts[popup_account] = (0, 0, False, False)

    return device_name, run_date, accounts, error_message


def normalize_device_name(name: str) -> str:
    """Normalize device name to 'Phone X' format."""
    if not name or name == "Unknown":
        return name
    match = re.search(r"phone\s*(\d+)", name, re.IGNORECASE)
    if match:
        return f"Phone {match.group(1)}"
    return name


def build_channel_data(
    channel_name: str,
    schedule_text: Optional[str],
    final_update_text: Optional[str],
    target_date: Optional[str] = None,
) -> ChannelData:
    """Combine schedule and final update – matching by date."""

    accounts: List[AccountData] = []
    device_name = "Unknown"
    method = "Method 1"
    error_message: Optional[str] = None

    # Extract expected phone number from channel name
    channel_phone_match = re.search(r"phone-?(\d+)", channel_name, re.IGNORECASE)
    expected_phone_num = channel_phone_match.group(1) if channel_phone_match else None

    # Track method from Final Update so schedule parsing cannot override it
    method_from_final: Optional[str] = None

    # Parse Final Update FIRST to get run_date
    run_date: Optional[str] = None
    actual_accounts: Dict[str, tuple[int, int, bool, bool]] = {}
    if final_update_text:
        fu_device, run_date, actual_accounts, error_message = parse_final_update(
            final_update_text
        )
        if fu_device != "Unknown":
            device_name = normalize_device_name(fu_device)
        logger.debug("Channel %s: Final update run_date=%s, accounts=%s", channel_name, run_date, list(actual_accounts.keys()))
        # Prefer method from Final Update when present
        method_match = re.search(r"automation type:\s*(method\s*\d+)", final_update_text, re.IGNORECASE)
        if method_match:
            method_from_final = f"Method {method_match.group(1).strip().split()[-1]}"
        elif "method 9" in final_update_text.lower():
            method_from_final = "Method 9"

    # Parse Schedule using run_date to match the correct day
    scheduled_accounts: Dict[str, tuple[str, int]] = {}
    if schedule_text:
        sched_device, method, scheduled_accounts = parse_schedule_for_date(
            schedule_text, run_date
        )
        if device_name == "Unknown" and sched_device != "Unknown":
            device_name = normalize_device_name(sched_device)
        logger.debug("Channel %s: Schedule matched accounts=%s", channel_name, list(scheduled_accounts.keys()))

    # If Final Update provided a method, keep it authoritative over schedule
    if method_from_final:
        method = method_from_final

    # VALIDATION: Check for mismatches (schedule says "Off" but account actually ran)
    for username, (actual_follows, actual_requests, _, _) in actual_accounts.items():
        if username in scheduled_accounts:
            sched_status, sched_follows = scheduled_accounts[username]
            total_actual = actual_follows + actual_requests
            if sched_status.lower() == "off" and total_actual > 0:
                logger.error(
                    "DATE MISMATCH for %s/%s: Schedule says 'Off' but actual=%d. "
                    "run_date=%s, schedule may have matched wrong day!",
                    channel_name, username, total_actual, run_date
                )

    # If Final Update device doesn't match channel, prefer channel name
    if expected_phone_num and device_name != "Unknown":
        device_num_match = re.search(r"(\d+)", device_name)
        if device_num_match and device_num_match.group(1) != expected_phone_num:
            logger.warning(
                "Device mismatch: channel=%s but Final Update says %s. Using channel-based name.",
                channel_name, device_name
            )
            device_name = f"Phone {expected_phone_num}"
        else:
            # Normalize device name even if it matches
            device_name = normalize_device_name(device_name)

    # FALLBACK: Use channel name if device still unknown
    if device_name == "Unknown":
        if expected_phone_num:
            device_name = f"Phone {expected_phone_num}"
            logger.debug("Extracted device name from channel name: %s -> %s", channel_name, device_name)
        else:
            # Normalize whatever we have
            device_name = normalize_device_name(device_name)

    # Merge account data, preferring actual_accounts where available
    all_usernames = set(scheduled_accounts.keys()) | set(actual_accounts.keys())

    for username in all_usernames:
        sched_status, sched_follows = scheduled_accounts.get(
            username, ("Unknown", 0)
        )
        actual_follows, actual_requests, is_blocked, is_unfollow = actual_accounts.get(
            username, (0, 0, False, False)
        )

        # Debug logging for schedule merging
        if username in actual_accounts and username in scheduled_accounts:
            logger.debug(
                "Merged %s: schedule=%s/%d, actual=%d+%d",
                username, sched_status, sched_follows, actual_follows, actual_requests
            )

        accounts.append(
            AccountData(
                username=username,
                scheduled_follows=sched_follows,
                scheduled_status=sched_status,
                actual_follows=actual_follows,
                actual_requests=actual_requests,
                is_blocked=is_blocked,
                is_unfollow=is_unfollow,
            )
        )

    return ChannelData(
        channel_name=channel_name,
        device_name=device_name,
        method=method,
        accounts=accounts,
        has_schedule=bool(schedule_text) and bool(scheduled_accounts),
        has_final_update=bool(final_update_text),
        error_message=error_message,
    )


def format_channels_compact(channels: List[ChannelData]) -> str:
    """
    Format channel data into minimal text for AI.

    Per line:
    device_name|status|account1:scheduled_status:scheduled:actual+requests:blocked,account2:...
    """
    lines: List[str] = []

    for ch in channels:
        if ch.error_message:
            status = f"error:{ch.error_message[:30]}"
        elif not ch.has_final_update:
            if ch.method == "Method 9" and ch.accounts:
                status = f"ok:{ch.method}"
            else:
                status = "no_task"
        else:
            status = f"ok:{ch.method}"

        acc_parts: List[str] = []
        for a in ch.accounts:
            blocked_flag = "y" if a.is_blocked else "n"
            # FIX: Send actual_follows+actual_requests, NOT total+requests
            acc_parts.append(
                f"{a.username}:{a.scheduled_status}:{a.scheduled_follows}:"
                f"{a.actual_follows}+{a.actual_requests}:{blocked_flag}"
            )

        accounts_str = ",".join(acc_parts)
        lines.append(f"{ch.device_name}|{status}|{accounts_str}")

    return "\n".join(lines)


def format_output_directly(channels: List[ChannelData]) -> str:
    """Format output - prioritize actual results over schedule."""
    lines: List[str] = []
    
    for ch in channels:
        # Phone line
        if ch.error_message:
            lines.append(f"{ch.device_name} – Error: {ch.error_message}")
        elif not ch.has_final_update:
            if ch.method == "Method 9" and ch.accounts:
                lines.append(f"{ch.device_name} – completed daily task ({ch.method})")
            else:
                lines.append(f"{ch.device_name} – no daily task made")
        else:
            lines.append(f"{ch.device_name} – completed daily task ({ch.method})")
        
        # Account lines - filter out empty/invalid accounts and ensure consistent formatting
        for acc in ch.accounts:
            # Skip accounts with empty or invalid usernames
            if not acc.username or not acc.username.strip():
                continue
            
            # Method 9: never show follow stats; only show blocked or username
            if ch.method == "Method 9":
                if acc.is_blocked:
                    lines.append(f"   * {acc.username} – blocked")
                else:
                    lines.append(f"   * {acc.username}")
                continue
            
            if acc.is_unfollow:
                total = acc.actual_follows  # unfollows count
                action_word = "unfollows"
            else:
                total = acc.actual_follows + acc.actual_requests
                action_word = "follows"
            
            # FIX: Check actual data FIRST, not schedule status
            # If account actually ran (has follows/requests), show results regardless of schedule
            if total > 0 or acc.is_blocked:
                # Account actually ran - show results regardless of schedule
                if acc.is_blocked:
                    lines.append(f"   * {acc.username} – blocked")
                elif acc.scheduled_follows == 0:
                    # No schedule info, just show total
                    lines.append(f"   * {acc.username} - total # of {action_word} made: {total}")
                elif total >= acc.scheduled_follows:
                    lines.append(
                        f"   * {acc.username} - total # of {action_word} made: {total} "
                        f"(met the daily max which is {acc.scheduled_follows})"
                    )
                else:
                    lines.append(
                        f"   * {acc.username} - total # of {action_word} made: {total} "
                        f"(didn't met the daily max which is {acc.scheduled_follows})"
                    )
            elif acc.scheduled_status == "Off":
                # Schedule says off AND no actual data
                lines.append(f"   * {acc.username} – off")
            elif acc.scheduled_status == "Method 9":
                # Method 9 - no stats shown
                lines.append(f"   * {acc.username}")
            else:
                # Schedule says should run but didn't (0 follows)
                if acc.scheduled_follows > 0:
                    lines.append(
                        f"   * {acc.username} - total # of follows made: 0 "
                        f"(didn't met the daily max which is {acc.scheduled_follows})"
                    )
                else:
                    lines.append(f"   * {acc.username} - total # of follows made: 0")
    
    # Filter out any empty lines and join
    filtered_lines = [line for line in lines if line.strip()]
    return "\n".join(filtered_lines)


@bot.event
async def on_ready():
    logger.info("Summary bot logged in as %s (ID: %s)", bot.user, bot.user.id)
    logger.info(
        "Using server_id=%s owner_user_id=%s timezone=%s keywords=%s",
        settings.server_id,
        settings.owner_user_id,
        settings.timezone,
        settings.keywords,
    )
    init_gemini(settings)
    if not daily_summary.is_running():
        logger.info("Starting daily_summary loop")
        daily_summary.start()


async def run_daily_summary(target_date: Optional[str] = None):
    """Run summary for a specific date or most recent."""
    guild: Optional[discord.Guild] = bot.get_guild(settings.server_id)
    if guild is None:
        logger.error("Guild with ID %s not found", settings.server_id)
        return

    me = guild.me
    logger.info("Running daily summary for guild '%s' (%s), target_date=%s", guild.name, guild.id, target_date)

    channel_structs: List[ChannelData] = []

    for channel in guild.text_channels:
        logger.info("Checking channel #%s (%s)", channel.name, channel.id)

        # Skip test channels
        if "test" in channel.name.lower():
            logger.info("Skipping test channel #%s", channel.name)
            continue

        # Only consider phone channels like #phone-1, #phone-6, etc.
        if not channel.name.startswith("phone-"):
            continue

        perms = channel.permissions_for(me)
        if not (perms.read_messages and perms.read_message_history):
            logger.info(
                "Skipping #%s (%s): missing read permissions",
                channel.name,
                channel.id,
            )
            continue

        history_messages: List[discord.Message] = []
        try:
            async for message in channel.history(limit=200, oldest_first=False):
                history_messages.append(message)
        except (discord.Forbidden, discord.HTTPException):
            logger.info(
                "Skipping #%s (%s): failed to fetch message history",
                channel.name,
                channel.id,
            )
            continue

        if not history_messages:
            continue

        # Use combined extraction to handle schedule and final update in same or separate messages
        schedule_text, final_update_text = extract_schedule_and_final_update(
            history_messages,
            target_date=target_date,
        )

        if not schedule_text and not final_update_text:
            # Not a phone schedule/final pair
            logger.info(
                "Skipping #%s (%s): no schedule/final update content found",
                channel.name,
                channel.id,
            )
            continue

        logger.info(
            "Channel #%s schedule length=%s, final length=%s",
            channel.name,
            len(schedule_text) if schedule_text else 0,
            len(final_update_text) if final_update_text else 0,
        )

        channel_data = build_channel_data(
            channel_name=channel.name,
            schedule_text=schedule_text,
            final_update_text=final_update_text,
            target_date=target_date,
        )

        logger.info(
            "Parsed channel %s -> device=%s, accounts=%d",
            channel.name,
            channel_data.device_name,
            len(channel_data.accounts),
        )

        channel_structs.append(channel_data)

    logger.info("Parsed %d channels into structured data", len(channel_structs))

    if not channel_structs:
        logger.info("No channels with schedule/final data found; skipping summary.")
        return

    # Use direct Python formatting (no AI - guaranteed correct formatting)
    output_text = format_output_directly(channel_structs)
    logger.info("Formatted output directly (no AI) - %d lines", len(output_text.split('\n')))
    logger.debug("First 500 chars of output:\n%s", output_text[:500])
    
    # Alternative: Use Gemini (currently disabled - uncomment to enable)
    # compact_data = format_channels_compact(channel_structs)
    # logger.info("Compact data size: %d characters", len(compact_data))
    # try:
    #     output_text = await summarize_with_gemini(settings=settings, compact_data=compact_data)
    # except Exception as exc:
    #     logger.exception("Gemini failed: %s", exc)
    #     output_text = format_output_directly(channel_structs)  # Fallback

    if target_date:
        dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        today_str = dt.strftime("%B %d, %Y")
    else:
        today_str = datetime.datetime.now().strftime("%B %d, %Y")
    
    final_report = f"📊 **Daily Summary - {today_str}**\n\n{output_text.strip()}"

    try:
        owner = await bot.fetch_user(settings.owner_user_id)
        chunks = [final_report[i:i + 1900] for i in range(0, len(final_report), 1900)]
        for chunk in chunks:
            await owner.send(chunk)
    except Exception as exc:
        logger.exception("Failed to DM owner %s with summary: %s", settings.owner_user_id, exc)


@tasks.loop(hours=24)
async def daily_summary():
    """Main daily summary task."""
    await bot.wait_until_ready()
    await run_daily_summary(target_date=None)


@daily_summary.before_loop
async def before_daily_summary():
    """Align the first run to 1 PM PKT, then run every 24 hours."""
    await bot.wait_until_ready()

    tz = pytz.timezone(settings.timezone)
    now_local = datetime.datetime.now(tz)

    target_time = now_local.replace(hour=13, minute=0, second=0, microsecond=0)
    if target_time <= now_local:
        target_time += datetime.timedelta(days=1)

    now_utc = _now_utc()
    target_utc = target_time.astimezone(datetime.timezone.utc)
    delay = (target_utc - now_utc).total_seconds()

    if delay > 0:
        await asyncio.sleep(delay)


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.reply("Pong from summary bot!", mention_author=False)


def _is_owner(user_id: int) -> bool:
    return user_id == settings.owner_user_id


@bot.command(name="summary")
async def summary_command(ctx: commands.Context, *, day_arg: str = None):
    """
    Get summary for a specific day.
    
    Usage:
        !summary              - Most recent summary
        !summary today        - Today's summary
        !summary yesterday    - Yesterday's summary
        !summary tuesday      - Last Tuesday's report
        !summary dec 02       - December 2nd report
    """
    if not _is_owner(ctx.author.id):
        logger.warning(
            "Unauthorized !summary attempt by user %s (%s)",
            ctx.author,
            ctx.author.id,
        )
        await ctx.reply("You are not authorized to run this command.", mention_author=False)
        return

    target_date = None
    
    if day_arg:
        target_date = parse_day_argument(day_arg, settings.timezone)
        if target_date is None:
            await ctx.send(
                f"❌ Could not parse: `{day_arg}`\n\n"
                f"**Valid formats:**\n"
                f"• `monday`, `tue`, `wednesday`\n"
                f"• `today`, `yesterday`\n"
                f"• `dec 02`, `december 2`\n"
                f"• `2025-12-02`, `12/02`"
            )
            return
        
        dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        readable = dt.strftime("%A, %B %d, %Y")
        await ctx.send(f"📅 Getting summary for **{readable}**...")
    else:
        await ctx.send("📊 Getting most recent summary...")
    
    logger.info("!summary triggered by %s for date=%s", ctx.author, target_date)
    await run_daily_summary(target_date=target_date)


def main():
    if not settings.discord_token:
        raise RuntimeError("SUMMARY_DISCORD_TOKEN is not configured.")
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()


