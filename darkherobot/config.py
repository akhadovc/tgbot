"""Diddy 2.0 settings, read once from .env / environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from pathlib import Path

from dotenv import load_dotenv


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


def _float(name: str, default: float, low: float = 0.0) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(low, value)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "ha"}


def _id_set(name: str) -> frozenset[int]:
    ids = set()
    for part in os.getenv(name, "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return frozenset(ids)


def _choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, "").strip().lower()
    return value if value in allowed else default


EFFORTS = {"low", "medium", "high", "xhigh", "max"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    openai_api_key: str
    model: str = "gpt-6.1-sol"
    chat_effort: str = "low"
    research_effort: str = "high"
    max_output_tokens: int = 1500
    research_max_output_tokens: int = 4000
    web_search: bool = True
    owner_ids: frozenset[int] = field(default_factory=frozenset)
    allowed_group_ids: frozenset[int] = field(default_factory=frozenset)
    private_access: str = "all"          # all | owner
    trigger_words: tuple[str, ...] = ("diddy",)
    daily_user_limit: int = 40
    monthly_budget_usd: float = 20.0
    price_input: float = 2.0             # USD per 1M input tokens
    price_cached_input: float = 0.10
    price_output: float = 10.0
    price_web_search_call: float = 0.01  # USD per search call (check OpenAI pricing page)
    history_hours: int = 24
    history_context_messages: int = 60
    history_context_chars: int = 9000
    private_memory_turns: int = 6
    db_path: str = "diddy.db"
    business_mode: str = "trigger"       # trigger | all | off  (Telegram Business personal chats)
    business_tone: str = "yumshoq"       # yumshoq | shaddot
    business_notify: bool = True         # DM the owner each time Diddy answers someone as him
    business_debounce_seconds: float = 6.0  # wait for a burst of messages, then answer once
    video_enabled: bool = True
    video_interval_seconds: int = 600     # one video every 10 minutes (whole bot)
    video_queue_max: int = 5
    video_max_mb: int = 20                # Telegram cloud Bot API download limit
    video_frames: int = 8
    video_max_audio_seconds: int = 600
    transcribe_model: str = "gpt-transcribe"
    price_transcribe_per_min: float = 0.0045


def load_settings() -> Settings:
    # .env holds the secret keys; sozlamalar.env holds ordinary settings (owner ID, limits...).
    # Values already set in .env win over sozlamalar.env.
    here = Path(__file__).resolve().parent
    load_dotenv(here / ".env")
    load_dotenv(here / "sozlamalar.env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    key = os.getenv("OPENAI_API_KEY", "").strip()
    missing = [name for name, value in (("TELEGRAM_BOT_TOKEN", token), ("OPENAI_API_KEY", key)) if not value]
    if missing:
        raise SystemExit(f".env faylida yo'q: {', '.join(missing)}")
    words = tuple(w.strip().lower() for w in os.getenv("TRIGGER_WORDS", "diddy").split(",") if w.strip())
    return Settings(
        telegram_bot_token=token,
        openai_api_key=key,
        model=os.getenv("OPENAI_MODEL", "gpt-6.1-sol").strip() or "gpt-6.1-sol",
        chat_effort=_choice("CHAT_EFFORT", "low", EFFORTS),
        research_effort=_choice("RESEARCH_EFFORT", "high", EFFORTS),
        max_output_tokens=_int("MAX_OUTPUT_TOKENS", 1500, 200, 8000),
        research_max_output_tokens=_int("RESEARCH_MAX_OUTPUT_TOKENS", 4000, 500, 16000),
        web_search=_bool("WEB_SEARCH", True),
        owner_ids=_id_set("OWNER_IDS"),
        allowed_group_ids=_id_set("ALLOWED_GROUP_IDS"),
        private_access=_choice("PRIVATE_ACCESS", "all", {"all", "owner"}),
        trigger_words=words or ("diddy",),
        daily_user_limit=_int("DAILY_USER_LIMIT", 40, 0, 10000),
        monthly_budget_usd=_float("MONTHLY_BUDGET_USD", 20.0),
        price_input=_float("PRICE_INPUT_PER_M", 2.0),
        price_cached_input=_float("PRICE_CACHED_INPUT_PER_M", 0.10),
        price_output=_float("PRICE_OUTPUT_PER_M", 10.0),
        price_web_search_call=_float("PRICE_WEB_SEARCH_CALL", 0.01),
        history_hours=_int("HISTORY_HOURS", 24, 1, 168),
        history_context_messages=_int("HISTORY_CONTEXT_MESSAGES", 60, 0, 400),
        history_context_chars=_int("HISTORY_CONTEXT_CHARS", 9000, 0, 60000),
        private_memory_turns=_int("PRIVATE_MEMORY_TURNS", 6, 0, 30),
        db_path=os.getenv("DB_PATH", "diddy.db").strip() or "diddy.db",
        business_mode=_choice("BUSINESS_MODE", "trigger", {"trigger", "all", "off"}),
        business_tone=_choice("BUSINESS_TONE", "yumshoq", {"yumshoq", "shaddot"}),
        business_notify=_bool("BUSINESS_NOTIFY", True),
        business_debounce_seconds=_float("BUSINESS_DEBOUNCE_SECONDS", 6.0),
        video_enabled=_bool("VIDEO_ENABLED", True),
        video_interval_seconds=_int("VIDEO_INTERVAL_SECONDS", 600, 0, 86400),
        video_queue_max=_int("VIDEO_QUEUE_MAX", 5, 1, 50),
        video_max_mb=_int("VIDEO_MAX_MB", 20, 1, 20),
        video_frames=_int("VIDEO_FRAMES", 8, 2, 16),
        video_max_audio_seconds=_int("VIDEO_MAX_AUDIO_SECONDS", 600, 30, 1800),
        transcribe_model=os.getenv("TRANSCRIBE_MODEL", "gpt-transcribe").strip() or "gpt-transcribe",
        price_transcribe_per_min=_float("PRICE_TRANSCRIBE_PER_MIN", 0.0045),
    )
