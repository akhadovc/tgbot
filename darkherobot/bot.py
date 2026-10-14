"""Diddy 2.0 — DarkHero Telegram AI bot (gpt-6.1-sol).

Run:  python bot.py
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import logging.handlers
import os
import time
import random
import re

from telegram import LinkPreviewOptions, Message, Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.error import BadRequest, Conflict, Forbidden, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from pathlib import Path

from ai import AIError, DiddyAI, ImageInput, build_input, build_video_input, format_history
from config import Settings, load_settings
from formatting import format_sources, split_message, to_telegram_html
from storage import Storage
from video import VideoError, sample_video_async, temp_workdir
from video_queue import AlreadyQueued, QueueFull, VideoJob, VideoQueue

logger = logging.getLogger("diddy")

DIDDY_REPLIES = [
    "Kim kim? 👀",
    "Nima gap bro?",
    "Hov 😭",
    "Dam olish kuni ham ishmi????",
    "Tinchlikmi?",
    "Meni chaqirdingmi? 🤨",
    "Ha, nima? Yana FPS tushdimi? 💀",
    "Chaqirding-u, savolni unutdingmi? 🗿",
    "Diddy band. Navbatga tur 😏",
    "Gapir, gapir... eshityapman, afsuski.",
    "Ismimni bekorga tilga olma, uka.",
    "Yana nima buzding? 😭",
    "Men Lola emasman, \"sizga qanday yordam bera olaman 🌸\" demayman. Gapir.",
    "Uyg'ondim. Arziydigan gap bo'lsin 🤡",
]

MAX_IMAGE_BYTES = 10 * 1024 * 1024
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


# ---------------------------------------------------------------- helpers

def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def _store(context: ContextTypes.DEFAULT_TYPE) -> Storage:
    return context.application.bot_data["storage"]


def _ai(context: ContextTypes.DEFAULT_TYPE) -> DiddyAI:
    return context.application.bot_data["ai"]


def is_owner(settings: Settings, user_id: int | None) -> bool:
    return user_id is not None and user_id in settings.owner_ids


def author_name(message: Message) -> str:
    user = message.from_user
    if user is None:
        return "Noma'lum"
    return (user.first_name or user.username or str(user.id)).strip()[:40]


def trigger_pattern(words: tuple[str, ...]) -> re.Pattern:
    alternatives = "|".join(re.escape(w) for w in words)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


def classify_group_message(text: str, *, words: tuple[str, ...], bot_username: str | None,
                           reply_to_bot: bool) -> str:
    """Return 'ignore', 'casual' (random short reply) or 'ask' (AI answer)."""
    pattern = trigger_pattern(words)
    mention = re.compile(rf"@{re.escape(bot_username)}\b", re.IGNORECASE) if bot_username else None
    called = bool(pattern.search(text)) or bool(mention and mention.search(text))
    if not called and not reply_to_bot:
        return "ignore"
    rest = pattern.sub(" ", text)
    if mention:
        rest = mention.sub(" ", rest)
    rest_words = re.findall(r"\w+", rest)
    if called and not reply_to_bot and len(rest_words) <= 2:
        return "casual"
    return "ask"


def group_question(text: str, settings: Settings, bot_username: str | None, message: Message) -> str:
    """The user's real question; a bare "Diddy" on a reply or photo gets a sensible default."""
    rest = trigger_pattern(settings.trigger_words).sub(" ", text)
    if bot_username:
        rest = re.sub(rf"@{re.escape(bot_username)}\b", " ", rest, flags=re.IGNORECASE)
    if re.findall(r"\w+", rest) and len(re.findall(r"\w+", rest)) > 2:
        return text
    if message.photo or (message.reply_to_message and message.reply_to_message.photo):
        return (text + " — shu rasmni ko'rib, fikringni ayt.").strip(" —")
    if video_of(message.reply_to_message):
        return (text + " — shu videoga reaksiya bildir.").strip(" —")
    if message.reply_to_message:
        return (text + " — shu xabarga nima deysan?").strip(" —")
    return text


def group_allowed(settings: Settings, chat_id: int) -> bool:
    return not settings.allowed_group_ids or chat_id in settings.allowed_group_ids


@contextlib.asynccontextmanager
async def typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int, business_connection_id: str | None = None):
    async def loop():
        while True:
            with contextlib.suppress(TelegramError):
                if business_connection_id:
                    await context.bot.send_chat_action(chat_id, ChatAction.TYPING,
                                                       business_connection_id=business_connection_id)
                else:
                    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4.5)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def send_reply(message: Message, markdown: str) -> list[Message]:
    sent: list[Message] = []
    for chunk in split_message(markdown):
        try:
            sent.append(await message.reply_text(to_telegram_html(chunk), parse_mode=ParseMode.HTML,
                                                 link_preview_options=NO_PREVIEW))
        except BadRequest:
            # Formatting Telegram did not accept: send the same chunk as plain text.
            sent.append(await message.reply_text(chunk, link_preview_options=NO_PREVIEW))
    return sent


async def download_photo(context: ContextTypes.DEFAULT_TYPE, message: Message | None) -> ImageInput | None:
    if message is None or not message.photo:
        return None
    photo = message.photo[-1]  # largest size
    if photo.file_size and photo.file_size > MAX_IMAGE_BYTES:
        return None
    file = await context.bot.get_file(photo.file_id)
    data = bytes(await file.download_as_bytearray())
    return ImageInput(data=data, mime="image/jpeg") if data else None


def remember_bot_messages(store: Storage, chat_id: int, sent: list[Message], text: str) -> None:
    if sent:
        store.add_group_message(chat_id, sent[0].message_id, "Diddy", text, is_bot=True)


# ---------------------------------------------------------------- gatekeeping

async def check_limits(message: Message, context: ContextTypes.DEFAULT_TYPE, quiet: bool = False) -> bool:
    """Budget and per-user daily limit. Counts one request when allowed.
    quiet=True: stay silent when blocked (used in the owner's personal chats)."""
    settings, store = _settings(context), _store(context)
    user_id = message.from_user.id
    if is_owner(settings, user_id):
        return True
    if settings.monthly_budget_usd > 0:
        spent = await asyncio.to_thread(lambda: store.month_spend()["usd"])
        if spent >= settings.monthly_budget_usd:
            if not quiet:
                await message.reply_text("Oylik byudjet tugadi. Hammang savol berib meni kambag'al qildinglar 💀 Keyingi oy qaytaman.")
            return False
    if settings.daily_user_limit > 0:
        ok = await asyncio.to_thread(store.take_daily_request, user_id, settings.daily_user_limit)
        if not ok:
            if quiet:
                return False
            await message.reply_text(
                f"Bugungi limiting tugadi ({settings.daily_user_limit} ta). Bir kunda shuncha savol? Google'ni eshitganmisan? 🗿 Ertaga kel.")
            return False
    return True


def refund(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    settings = _settings(context)
    if settings.daily_user_limit > 0 and not is_owner(settings, user_id):
        _store(context).refund_daily_request(user_id)


# ---------------------------------------------------------------- core answer

async def answer(message: Message, context: ContextTypes.DEFAULT_TYPE, question: str, *,
                 research: bool = False, include_history: bool = True) -> str | None:
    """Answer with the AI. Returns the reply text, or None when nothing was answered."""
    settings, store, ai = _settings(context), _store(context), _ai(context)
    chat, user = message.chat, message.from_user
    group = chat.type in GROUP_TYPES
    business_id = getattr(message, "business_connection_id", None)
    # Conversation memory: DM with the bot keyed by user id; a Business chat keyed by -chat id
    # (its own thread, never mixed with that person's DM with the bot); groups use history instead.
    memory_key = None if group else (-chat.id if business_id else user.id)
    private_memory = memory_key is not None

    if not await check_limits(message, context, quiet=bool(business_id)):
        return None

    history_block, turns = "", []
    if group and include_history and settings.history_context_messages:
        lines = await asyncio.to_thread(store.group_history, chat.id, settings.history_hours,
                                        settings.history_context_messages + 1)
        lines = [line for line in lines if line.message_id != message.message_id]
        history_block = format_history(lines, settings.history_context_chars)
    if private_memory:
        turns = await asyncio.to_thread(store.private_turns, memory_key, settings.private_memory_turns)

    quoted, images = "", []
    replied = message.reply_to_message
    if replied is not None:
        quoted_text = (replied.text or replied.caption or "").strip()
        if quoted_text:
            who = "Diddy" if replied.from_user and replied.from_user.id == context.bot.id else author_name(replied)
            quoted = f"{who}: {quoted_text[:3000]}"
    try:
        for source in (message, replied):
            image = await download_photo(context, source)
            if image:
                images.append(image)
    except TelegramError:
        logger.warning("Photo download failed chat=%s", chat.id)

    items = build_input(question, history_block=history_block, quoted=quoted, private_turns=turns,
                        images=images, author=author_name(message) if group or business_id else "")

    try:
        async with typing(context, chat.id, business_id):
            result = await ai.ask(items, user_id=user.id, chat_id=chat.id, research=research,
                                  instructions_extra=business_instructions(settings) if business_id else "")
    except AIError as exc:
        refund(context, user.id)
        if business_id:
            logger.warning("Business answer failed: %s", exc)  # don't show tech errors to friends/customers
        else:
            await message.reply_text(str(exc))
        return None

    await asyncio.to_thread(store.add_spend, result.usd, result.input_tokens, result.cached_tokens,
                            result.output_tokens, result.searches)
    logger.info("answer chat=%s research=%s in=%s cached=%s out=%s search=%s usd=%.4f",
                chat.id, research, result.input_tokens, result.cached_tokens,
                result.output_tokens, result.searches, result.usd)

    sent = await send_reply(message, result.text + format_sources(result.sources))
    if group:
        await asyncio.to_thread(remember_bot_messages, store, chat.id, sent, result.text)
    elif private_memory:
        await asyncio.to_thread(store.add_private_turn, memory_key, "user", question)
        await asyncio.to_thread(store.add_private_turn, memory_key, "assistant", result.text)
    return result.text


# ---------------------------------------------------------------- video

def video_of(message: Message | None):
    """(file_id, file_unique_id, file_size, kind) for a video-like message, else None."""
    if message is None:
        return None
    if message.video:
        v = message.video
        return v.file_id, v.file_unique_id, v.file_size or 0, "video"
    if message.video_note:
        v = message.video_note
        return v.file_id, v.file_unique_id, v.file_size or 0, "krujok"
    if message.animation:
        v = message.animation
        return v.file_id, v.file_unique_id, v.file_size or 0, "gif"
    document = message.document
    if document and (document.mime_type or "").startswith("video/"):
        return document.file_id, document.file_unique_id, document.file_size or 0, "video"
    return None


def minutes_text(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes} daqiqa"


async def enqueue_video(message: Message, context: ContextTypes.DEFAULT_TYPE, source: Message,
                        question: str, *, notify_owner: int | None = None) -> None:
    settings = _settings(context)
    info = video_of(source)
    if info is None:
        return
    file_id, unique_id, size, kind = info
    if not settings.video_enabled:
        await message.reply_text("Video ko'rish hozircha o'chirilgan.")
        return
    if size and size > settings.video_max_mb * 1024 * 1024:
        await message.reply_text(
            f"Bu video {size / 1024 / 1024:.0f} MB. Telegram menga {settings.video_max_mb} MB dan kattasini "
            "bermaydi. Qisqartirib yoki siqib tashla 🗿")
        return
    queue: VideoQueue = context.application.bot_data["video_queue"]
    if queue.pending_count() >= queue.max_pending:
        await message.reply_text("Video navbati to'la. Birozdan keyin qayta tashla, hammang kino ko'rsatma 😭")
        return
    if not await check_limits(message, context):
        return
    job = VideoJob(file_id=file_id, file_unique_id=unique_id, user_id=message.from_user.id,
                   chat_id=message.chat.id,
                   payload={"message": message, "question": question, "kind": kind,
                            "caption": (source.caption or "").strip(), "notify_owner": notify_owner})
    try:
        ahead, eta = queue.submit(job)
    except AlreadyQueued:
        refund(context, message.from_user.id)
        await message.reply_text("Bu video allaqachon navbatda. Sabr qil 🗿")
        return
    except QueueFull:
        refund(context, message.from_user.id)
        await message.reply_text("Video navbati to'la. Birozdan keyin qayta tashla.")
        return
    if ahead == 0 and eta < 5:
        await message.reply_text("Ko'ryapman 👀")
    elif ahead == 0:
        await message.reply_text(f"Navbatdasan, birinchisan. Taxminan {minutes_text(eta)}dan keyin ko'raman ⏳")
    else:
        await message.reply_text(
            f"Navbatdasan: oldingda {ahead} ta video. Taxminan {minutes_text(eta)}dan keyin ko'raman ⏳")


async def process_video(app: Application, job: VideoJob) -> None:
    settings: Settings = app.bot_data["settings"]
    store: Storage = app.bot_data["storage"]
    ai: DiddyAI = app.bot_data["ai"]
    message: Message = job.payload["message"]
    chat = message.chat
    group = chat.type in GROUP_TYPES
    context = SimpleContext(app)

    def fail_refund():
        if settings.daily_user_limit > 0 and not is_owner(settings, job.user_id):
            store.refund_daily_request(job.user_id)

    try:
        business_id = getattr(message, "business_connection_id", None)
        async with typing(context, chat.id, business_id):
            with temp_workdir() as tmp:
                workdir = Path(tmp)
                path = workdir / "video.bin"
                try:
                    file = await app.bot.get_file(job.file_id)
                    await file.download_to_drive(path)
                except BadRequest as exc:
                    if "too big" in str(exc).lower():
                        raise VideoError(f"Video juda katta, Telegram {settings.video_max_mb} MB dan "
                                         "kattasini bermaydi.") from None
                    raise VideoError("Videoni Telegram'dan yuklab ololmadim.") from None
                sample = await sample_video_async(
                    path, workdir, frames=settings.video_frames,
                    max_audio_seconds=settings.video_max_audio_seconds,
                    with_audio=job.payload["kind"] != "gif")
                transcript = None
                if sample.audio_path is not None:
                    transcript = await ai.transcribe(sample.audio_path)
                history_block = ""
                if group and settings.history_context_messages:
                    lines = await asyncio.to_thread(store.group_history, chat.id, settings.history_hours, 30)
                    history_block = format_history([l for l in lines if l.message_id != message.message_id],
                                                   min(settings.history_context_chars, 4000))
                items = build_video_input(job.payload["question"], sample.frames, duration=sample.duration,
                                          transcript=transcript, caption=job.payload["caption"],
                                          author=author_name(message) if group else "",
                                          history_block=history_block)
                result = await ai.ask(items, user_id=job.user_id, chat_id=chat.id,
                                      instructions_extra=business_instructions(settings) if business_id else "")
    except (VideoError, AIError) as exc:
        await asyncio.to_thread(fail_refund)
        with contextlib.suppress(TelegramError):
            await message.reply_text(str(exc))
        return
    except Exception:
        logger.exception("Video processing crashed chat=%s", chat.id)
        await asyncio.to_thread(fail_refund)
        with contextlib.suppress(TelegramError):
            await message.reply_text("Videoni ko'rayotganda nimadir buzildi 😵 Keyinroq qayta urinib ko'r.")
        return

    transcribe_usd = (sample.audio_seconds / 60 * settings.price_transcribe_per_min
                      if transcript is not None else 0.0)
    await asyncio.to_thread(store.add_spend, result.usd + transcribe_usd, result.input_tokens,
                            result.cached_tokens, result.output_tokens, result.searches)
    logger.info("video chat=%s dur=%.0fs frames=%d speech=%s in=%s out=%s usd=%.4f", chat.id,
                sample.duration, len(sample.frames), transcript is not None, result.input_tokens,
                result.output_tokens, result.usd + transcribe_usd)
    sent = await send_reply(message, result.text + format_sources(result.sources))
    if group:
        await asyncio.to_thread(remember_bot_messages, store, chat.id, sent, result.text)
    await notify_business_owner(app.bot, settings, job.payload.get("notify_owner"), message, result.text)


class SimpleContext:
    """The small slice of CallbackContext that typing() needs, for background jobs."""

    def __init__(self, app: Application) -> None:
        self.application = app
        self.bot = app.bot


# ---------------------------------------------------------------- handlers

# ---------------------------------------------------------------- Telegram Business

BUSINESS_SOFT = (
    "TELEGRAM BUSINESS REJIMI: sen hozir egangning SHAXSIY chatida, uning nomidan javob beryapsan. "
    "Suhbatdosh uning tanishi, qarindoshi yoki magazin mijozi bo'lishi mumkin. Diddy xarakteri qoladi "
    "(qisqa, hazilkash), lekin roastni yumshat: odamni masxara qilma, qo'pol so'z ishlatma, mijozga "
    "hurmat bilan gapir. Narx, buyurtma yoki va'da (yetkazib berish, kafolat, chegirma) haqida egang "
    "nomidan qaror qilma — 'egam o'zi javob beradi' de."
)
BUSINESS_HARD = (
    "TELEGRAM BUSINESS REJIMI: sen egangning shaxsiy chatida uning nomidan javob beryapsan. "
    "Xarakter odatdagidek shaddot, lekin narx, buyurtma yoki va'da haqida egang nomidan qaror qilma."
)


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def notify_business_owner(bot, settings: Settings, owner_id: int | None, message: Message,
                                reply_text: str) -> None:
    """Tell the account owner, in the bot's private chat, that Diddy answered someone as him."""
    if not owner_id or not settings.business_notify or not reply_text:
        return
    user = message.from_user
    if user is None or user.id == owner_id:
        return  # the owner called Diddy himself — he already sees it
    name = html.escape(getattr(user, "full_name", None) or user.first_name or str(user.id))
    username = f" (@{html.escape(user.username)})" if user.username else ""
    asked = (message.text or message.caption or "").strip()
    media = video_of(message)
    if media:
        asked = f"[{media[3]}] {asked}".strip()
    elif message.photo:
        asked = f"[rasm] {asked}".strip()
    text = (
        "🔔 <b>Diddy sizning nomingizdan javob berdi</b>\n"
        f"👤 <a href=\"tg://user?id={user.id}\">{name}</a>{username}\n"
        f"💬 U yozdi: <i>{html.escape(_short(asked, 300))}</i>\n"
        f"🤖 Javob: {html.escape(_short(reply_text, 500))}"
    )
    try:
        await bot.send_message(owner_id, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
    except TelegramError as exc:
        # Usually: the owner never pressed /start in the bot's own chat.
        logger.warning("Owner notification failed: %s", type(exc).__name__)


def business_instructions(settings: Settings) -> str:
    return BUSINESS_HARD if settings.business_tone == "shaddot" else BUSINESS_SOFT


async def business_owner(context: ContextTypes.DEFAULT_TYPE, connection_id: str) -> int | None:
    """Telegram user id of the account that connected the bot, if it may reply. Cached 10 min."""
    cache: dict = context.application.bot_data.setdefault("business_owners", {})
    hit = cache.get(connection_id)
    if hit and time.monotonic() - hit[1] < 600:
        return hit[0]
    try:
        connection = await context.bot.get_business_connection(connection_id)
    except TelegramError:
        logger.warning("Business connection lookup failed")
        return None
    rights = getattr(connection, "rights", None)
    can_reply = getattr(rights, "can_reply", None) if rights is not None else getattr(connection, "can_reply", None)
    owner = connection.user.id if getattr(connection, "is_enabled", True) and can_reply is not False else None
    cache[connection_id] = (owner, time.monotonic())
    return owner


PAUSE_FLAG = "business_paused"


def business_paused(store: Storage) -> bool:
    return store.get_flag(PAUSE_FLAG, "0") == "1"


async def on_business_message(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The owner's personal chats (Telegram Business chatbot).

    BUSINESS_MODE=all: answer every message other people send, until the owner sends /pauza.
    BUSINESS_MODE=trigger: answer only when someone writes "Diddy".
    The owner's own messages are never answered unless he writes "Diddy" himself.
    """
    settings, store = _settings(context), _store(context)
    if message.sender_business_bot is not None:
        return  # sent by a bot on the owner's behalf (our own replies) — never answer ourselves
    if settings.business_mode == "off":
        return
    owner_id = await business_owner(context, message.business_connection_id)
    if owner_id is None:
        return
    chat_id = message.chat.id
    text = (message.text or message.caption or "").strip()
    from_owner = message.from_user.id == owner_id
    called = bool(trigger_pattern(settings.trigger_words).search(text))

    if from_owner:
        # The owner is typing in this chat himself: drop any reply we were about to send,
        # and remember what he said so later answers stay consistent with him.
        cancel_pending_business(context, chat_id)
        if text and not called:
            await asyncio.to_thread(store.add_private_turn, -chat_id, "assistant", "(egam o'zi yozdi) " + text)
        if not called:
            return
    else:
        if await asyncio.to_thread(business_paused, store):
            return
        if settings.business_mode == "trigger" and not called:
            return

    replied = message.reply_to_message
    # Videos are watched only when someone asks Diddy (they cost more and go through the 10-min queue).
    if called and video_of(replied):
        await enqueue_video(message, context, replied, group_question(text, settings, None, message),
                            notify_owner=owner_id)
        return
    own_video = video_of(message)
    if own_video and called:
        await enqueue_video(message, context, message, text or "Shu videoga reaksiya bildir.",
                            notify_owner=owner_id)
        return
    if own_video:
        if not from_owner:
            schedule_business_answer(message, context, owner_id,
                                     f"[{own_video[3]} yubordi — sen uni ko'rmading] {text}".strip())
        return
    kind = classify_group_message(text, words=settings.trigger_words, bot_username=None, reply_to_bot=False)
    if called and kind == "casual" and not message.photo and not (replied and (replied.text or replied.photo)):
        reply = random.choice(DIDDY_REPLIES)
        await message.reply_text(reply)
        await notify_business_owner(context.bot, settings, owner_id, message, reply)
        return
    if from_owner:
        reply = await answer(message, context, group_question(text, settings, None, message))
        return
    question = group_question(text, settings, None, message) if called else (text or "[rasm yubordi]")
    schedule_business_answer(message, context, owner_id, question)


def _pending(context) -> dict:
    return context.application.bot_data.setdefault("business_pending", {})


def cancel_pending_business(context, chat_id: int) -> None:
    entry = _pending(context).pop(chat_id, None)
    if entry and not entry["started"]:
        entry["task"].cancel()


def schedule_business_answer(message: Message, context, owner_id: int, text: str) -> None:
    """Wait a few seconds so several quick messages get ONE reply (people often split a thought)."""
    settings = _settings(context)
    pending = _pending(context)
    chat_id = message.chat.id
    texts = [text]
    old = pending.get(chat_id)
    if old and not old["started"]:
        old["task"].cancel()
        texts = old["texts"] + texts
    entry = {"texts": texts, "message": message, "started": False}

    async def run():
        await asyncio.sleep(settings.business_debounce_seconds)
        entry["started"] = True
        try:
            reply = await answer(entry["message"], context, "\n".join(entry["texts"]))
            if reply:
                await notify_business_owner(context.bot, settings, owner_id, entry["message"], reply)
        except Exception:
            logger.exception("Business auto-reply failed chat=%s", chat_id)
        finally:
            if pending.get(chat_id) is entry:
                pending.pop(chat_id, None)

    entry["task"] = asyncio.create_task(run())
    pending[chat_id] = entry


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.from_user or not is_owner(_settings(context), message.from_user.id):
        return
    await asyncio.to_thread(_store(context).set_flag, PAUSE_FLAG, "1")
    for chat_id in list(_pending(context)):
        cancel_pending_business(context, chat_id)
    await message.reply_text("⏸ Shaxsiy chatlarda avtomatik javob to'xtatildi. Qayta yoqish: /davom\n"
                             "(\"Diddy\" deb chaqirsangiz va guruhlar ishlashda davom etadi.)")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.from_user or not is_owner(_settings(context), message.from_user.id):
        return
    await asyncio.to_thread(_store(context).set_flag, PAUSE_FLAG, "0")
    mode = _settings(context).business_mode
    hint = "" if mode == "all" else f"\n⚠️ Lekin BUSINESS_MODE={mode} — hammaga javob uchun sozlamalar.env da all qiling."
    await message.reply_text("▶️ Shaxsiy chatlarda avtomatik javob yoqildi. To'xtatish: /pauza" + hint)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.from_user or not is_owner(_settings(context), message.from_user.id):
        return
    settings = _settings(context)
    paused = await asyncio.to_thread(business_paused, _store(context))
    queue = context.application.bot_data.get("video_queue")
    await message.reply_text(
        f"Shaxsiy chatlar: {'⏸ pauzada' if paused else '▶️ ishlayapti'} (rejim: {settings.business_mode}, "
        f"ohang: {settings.business_tone})\n"
        f"Video navbati: {queue.pending_count() if queue else 0} ta kutyapti\n"
        f"Model: {settings.model}")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.from_user is None or message.from_user.is_bot:
        return
    if getattr(message, "business_connection_id", None):
        await on_business_message(message, context)
        return
    settings, store = _settings(context), _store(context)
    chat, user = message.chat, message.from_user
    text = (message.text or message.caption or "").strip()

    if chat.type in GROUP_TYPES:
        if not group_allowed(settings, chat.id):
            return
        own_video = video_of(message)
        label = f"[{own_video[3]}] " if own_video else "[rasm] " if message.photo else ""
        await asyncio.to_thread(store.add_group_message, chat.id, message.message_id,
                                author_name(message), (label + text).strip())
        replied = message.reply_to_message
        reply_to_bot = bool(replied and replied.from_user and replied.from_user.id == context.bot.id)
        kind = classify_group_message(text, words=settings.trigger_words,
                                      bot_username=context.bot.username, reply_to_bot=reply_to_bot)
        if own_video:
            # Videos are only watched through a reply: a video sent as a reply to Diddy,
            # or "Diddy ..." written as a reply to a video.
            if reply_to_bot:
                await enqueue_video(message, context, message, text or "Shu videoga reaksiya bildir.")
            elif kind != "ignore":
                await message.reply_text("Videoni ko'rishim uchun unga reply qilib \"Diddy\" deb yoz 🗿")
            return
        if kind == "ignore":
            return
        if video_of(replied):
            await enqueue_video(message, context, replied,
                                group_question(text, settings, context.bot.username, message))
            return
        if kind == "casual" and not message.photo and not (replied and (replied.text or replied.photo)):
            reply = random.choice(DIDDY_REPLIES)
            sent = await message.reply_text(reply)
            await asyncio.to_thread(remember_bot_messages, store, chat.id, [sent], reply)
            return
        await answer(message, context, group_question(text, settings, context.bot.username, message))
        return

    if chat.type == ChatType.PRIVATE:
        if settings.private_access == "owner" and not is_owner(settings, user.id):
            await message.reply_text("Bu bot faqat guruhda ishlaydi, bro. Guruhda \"Diddy\" deb chaqiring 😉")
            return
        if video_of(message):
            await enqueue_video(message, context, message, text or "Shu videoga reaksiya bildir.")
            return
        if video_of(message.reply_to_message):
            await enqueue_video(message, context, message.reply_to_message, text or "Shu videoga reaksiya bildir.")
            return
        await answer(message, context, text or "Shu rasmni tahlil qil.")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Diddy 2.0 keldi. Lola emas, muloyim ham emas 😏\n\n"
        "• Guruhda: \"Diddy\" deb yozing yoki mening xabarimga reply qiling\n"
        "• Rasm yuborsangiz ham ko'rib beraman\n"
        "• Videoga reply qilib \"Diddy\" desangiz — ko'rib, reaksiya beraman (10 daqiqada bitta)\n"
        "• /qidir <savol> — internetdan chuqur qidiruv\n"
        "• /xulosa [soat] — guruhda nima gaplashilganini qisqacha aytib beraman\n"
        "• /reset — shaxsiy suhbat xotirasini tozalash\n"
        "• /limit — bugungi limitingiz"
    )


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.from_user is None:
        return
    settings = _settings(context)
    if message.chat.type in GROUP_TYPES and not group_allowed(settings, message.chat.id):
        return
    if (message.chat.type == ChatType.PRIVATE and settings.private_access == "owner"
            and not is_owner(settings, message.from_user.id)):
        return
    question = " ".join(context.args or []).strip()
    if not question and message.reply_to_message:
        question = (message.reply_to_message.text or message.reply_to_message.caption or "").strip()
    if not question:
        await message.reply_text("Nimani qidiray? Masalan: /qidir RTX 5070 Ti narxi Toshkentda")
        return
    if not settings.web_search:
        await message.reply_text("Web qidiruv o'chirilgan (WEB_SEARCH=false).")
        return
    await answer(message, context, question, research=True)


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.from_user is None:
        return
    settings, store = _settings(context), _store(context)
    if message.chat.type not in GROUP_TYPES:
        await message.reply_text("/xulosa faqat guruhda ishlaydi.")
        return
    if not group_allowed(settings, message.chat.id):
        return
    hours = settings.history_hours
    if context.args and context.args[0].isdigit():
        hours = max(1, min(settings.history_hours, int(context.args[0])))
    lines = await asyncio.to_thread(store.group_history, message.chat.id, hours, 2000)
    if len(lines) < 3:
        await message.reply_text("Xulosa qilishga hali yetarli xabar yo'q 🤷")
        return
    if not await check_limits(message, context):
        return
    block = format_history(lines, max(settings.history_context_chars * 4, 20000))
    items = build_input(f"Oxirgi {hours} soatda guruhda nima gaplashilganini qisqacha xulosa qil.",
                        history_block=block)
    try:
        async with typing(context, message.chat.id):
            result = await _ai(context).ask(
                items, user_id=message.from_user.id, chat_id=message.chat.id, allow_search=False,
                instructions_extra="Xulosa: asosiy mavzular ro'yxat bilan, kim nima taklif qilgani, "
                                   "hal bo'lmagan savollar. 10 punktdan oshma.")
    except AIError as exc:
        refund(context, message.from_user.id)
        await message.reply_text(str(exc))
        return
    await asyncio.to_thread(store.add_spend, result.usd, result.input_tokens, result.cached_tokens,
                            result.output_tokens, result.searches)
    await send_reply(message, result.text)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message and message.from_user:
        await asyncio.to_thread(_store(context).clear_private, message.from_user.id)
        await message.reply_text("Shaxsiy suhbat xotirasi tozalandi 🧹")


async def cmd_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.from_user:
        return
    settings = _settings(context)
    if is_owner(settings, message.from_user.id) or settings.daily_user_limit == 0:
        await message.reply_text("Sizda limit yo'q 😎")
        return
    used = await asyncio.to_thread(_store(context).daily_used, message.from_user.id)
    await message.reply_text(f"Bugun: {used}/{settings.daily_user_limit} ta savol ishlatildi.")


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.from_user or not is_owner(_settings(context), message.from_user.id):
        return
    settings = _settings(context)
    s = await asyncio.to_thread(_store(context).month_spend)
    budget = f"${settings.monthly_budget_usd:.2f}" if settings.monthly_budget_usd > 0 else "cheksiz"
    await message.reply_text(
        f"📊 {s['month']} oy\n"
        f"Sarf (taxminiy): ${s['usd']:.3f} / {budget}\n"
        f"So'rovlar: {s['requests']}\n"
        f"Tokenlar: kirish {s['input_tokens']:,} (kesh {s['cached_tokens']:,}), chiqish {s['output_tokens']:,}\n"
        f"Web qidiruvlar: {s['searches']}\n"
        f"Model: {settings.model}"
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message and message.from_user:
        await message.reply_text(f"Chat ID: {message.chat.id}\nSizning ID: {message.from_user.id}")


CONFLICT_WARNING = (
    "DIQQAT: shu bot tokeni bilan BOSHQA joyda ham bot ishlayapti (eski versiya yoki boshqa kompyuter/server). "
    "Xabarlar ikkalasiga bo'linib ketadi. Boshqasini o'chiring yoki @BotFather'da tokenni yangilang."
)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if isinstance(error, Conflict):
        data = context.application.bot_data
        now = time.monotonic()
        if now - data.get("conflict_warned_at", -1e9) > 600:
            data["conflict_warned_at"] = now
            logger.error(CONFLICT_WARNING)
            print("\n!!! " + CONFLICT_WARNING + "\n", flush=True)
            for owner_id in _settings(context).owner_ids:
                with contextlib.suppress(TelegramError):
                    await context.bot.send_message(owner_id, "⚠️ " + CONFLICT_WARNING)
        return
    if isinstance(error, Forbidden):
        logger.info("Bot was blocked or removed from a chat")
        return
    logger.error("Unhandled error: %s", type(error).__name__, exc_info=error)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(TelegramError):
            await update.effective_message.reply_text("Nimadir buzildi, bro 😵 Qaytadan urinib ko'ring.")


# ---------------------------------------------------------------- startup

async def _prune_forever(store: Storage, settings: Settings) -> None:
    while True:
        try:
            await asyncio.to_thread(store.prune, settings.history_hours, settings.private_memory_turns)
        except Exception:
            logger.exception("Prune failed")
        await asyncio.sleep(3600)


async def watch_usb(marker: Path, interval: float = 2.0, on_lost=None) -> None:
    """Portable mode: the moment the USB stick is pulled out, stop the bot.

    Python may keep running from memory after the drive disappears, so we check that
    our own file is still readable and exit hard when it is not.
    """
    on_lost = on_lost or (lambda: os._exit(0))
    while True:
        await asyncio.sleep(interval)
        try:
            alive = marker.exists() and marker.stat().st_size > 0
        except OSError:
            alive = False
        if not alive:
            print("\nUSB chiqarildi — Diddy to'xtadi.", flush=True)
            on_lost()
            return


async def post_init(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    if os.getenv("DIDDY_PORTABLE") == "1":
        app.bot_data["usb_watch"] = asyncio.create_task(watch_usb(Path(__file__).resolve()))
        logger.info("Portable (USB) rejim: fleshka chiqarilsa bot o'chadi")
    app.bot_data["prune_task"] = asyncio.create_task(_prune_forever(app.bot_data["storage"], settings))
    queue = VideoQueue(lambda job: process_video(app, job), interval=settings.video_interval_seconds,
                       max_pending=settings.video_queue_max)
    queue.start()
    app.bot_data["video_queue"] = queue
    me = await app.bot.get_me()
    logger.info("Diddy 2.0 ishga tushdi: @%s model=%s web_search=%s", me.username, settings.model,
                settings.web_search)
    if not settings.owner_ids:
        logger.warning("OWNER_IDS bo'sh: /usage ishlamaydi va limitlar hammaga qo'llanadi. /id bilan ID'ingizni bilib oling.")


async def post_shutdown(app: Application) -> None:
    queue = app.bot_data.get("video_queue")
    if queue:
        await queue.stop()
    task = app.bot_data.get("prune_task")
    if task:
        task.cancel()
    app.bot_data["storage"].close()


def main() -> None:
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    logging.basicConfig(level=logging.INFO, format=log_format)
    file_handler = logging.handlers.RotatingFileHandler("diddy.log", maxBytes=2_000_000, backupCount=2,
                                                        encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(file_handler)
    # httpx logs every Telegram request URL, and that URL contains the bot token.
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    settings = load_settings()
    app = (Application.builder()
           .token(settings.telegram_bot_token)
           .concurrent_updates(True)   # one slow AI answer must not freeze everyone else
           .post_init(post_init)
           .post_shutdown(post_shutdown)
           .build())
    app.bot_data["settings"] = settings
    app.bot_data["storage"] = Storage(settings.db_path)
    app.bot_data["ai"] = DiddyAI(settings)

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler(["qidir", "search"], cmd_research))
    app.add_handler(CommandHandler(["xulosa", "summary"], cmd_summary))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("limit", cmd_limit))
    app.add_handler(CommandHandler("usage", cmd_usage))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler(["pauza", "pause"], cmd_pause))
    app.add_handler(CommandHandler(["davom", "resume"], cmd_resume))
    app.add_handler(CommandHandler(["holat", "status"], cmd_status))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.VIDEO_NOTE
                                    | filters.ANIMATION | filters.Document.VIDEO) & ~filters.COMMAND
                                   & (filters.UpdateType.MESSAGE | filters.UpdateType.BUSINESS_MESSAGE),
                                   on_message))
    app.add_error_handler(on_error)

    print("Diddy 2.0 ishga tushdi... (to'xtatish: Ctrl+C)")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
