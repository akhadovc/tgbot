"""Turn model Markdown into safe Telegram HTML and split long replies."""
from __future__ import annotations

import html
import re

TELEGRAM_LIMIT = 4096
CHUNK = 3800  # leave room for HTML tags added after splitting

_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.M)
_BULLET = re.compile(r"^(\s*)[-*]\s+", re.M)


def to_telegram_html(text: str) -> str:
    """Convert the common Markdown subset to Telegram HTML. Everything else is escaped."""
    placeholders: list[str] = []

    def keep(fragment: str) -> str:
        placeholders.append(fragment)
        return f"\x00{len(placeholders) - 1}\x00"

    text = _FENCE.sub(lambda m: keep(f"<pre>{html.escape(m.group(1).rstrip(), quote=False)}</pre>"), text)
    text = _INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = _LINK.sub(lambda m: keep(
        f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'), text)
    text = html.escape(text, quote=False)
    text = _HEADING.sub(r"<b>\1</b>", text)
    text = _BULLET.sub(r"\1• ", text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)


def split_message(text: str, limit: int = CHUNK) -> list[str]:
    """Split on paragraph, then line, then word boundaries; never exceed ``limit``."""
    text = text.strip()
    if not text:
        return []
    parts: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = max(window.rfind("\n\n"), -1)
        if cut < limit // 3:
            cut = window.rfind("\n")
        if cut < limit // 3:
            cut = window.rfind(" ")
        if cut < limit // 3:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def format_sources(sources: list[tuple[str, str]], limit: int = 5) -> str:
    """Markdown block listing (title, url) sources, de-duplicated by URL."""
    seen, lines = set(), []
    for title, url in sources:
        if url in seen:
            continue
        seen.add(url)
        label = (title or url).strip().replace("[", "(").replace("]", ")")[:80]
        lines.append(f"{len(lines) + 1}. [{label}]({url})")
        if len(lines) >= limit:
            break
    return "\n\n**Manbalar:**\n" + "\n".join(lines) if lines else ""
