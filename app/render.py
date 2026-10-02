"""Previews (to Dior's DM) and publishing (to the channel)."""
import asyncio

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.constants import ParseMode

from .checker import sanitize_html
from .settings import ADMIN_ID, CHANNEL_ID, DAYS, SCHEDULE

LETTERS = "ABCDEFGHIJ"


def _quiz_as_text(m: dict) -> str:
    lines = [f"📊 <b>{'QUIZ' if m['type'] == 'quiz' else 'POLL'}</b>", sanitize_html(m["question"]), ""]
    for i, o in enumerate(m["options"]):
        mark = " ✅" if m["type"] == "quiz" and i == m.get("correct_option_id") else ""
        lines.append(f"{LETTERS[i]}) {sanitize_html(o)}{mark}")
    if m.get("explanation"):
        lines += ["", f"💬 <i>{sanitize_html(m['explanation'])}</i>"]
    return "\n".join(lines)


def controls(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"approve:{draft_id}"),
         InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{draft_id}")],
        [InlineKeyboardButton("🔄 New version", callback_data=f"regen:{draft_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"reject:{draft_id}")],
    ])


async def _send_one(bot: Bot, chat_id: int, m: dict, preview: bool):
    t = m["type"]
    if t == "text":
        return await bot.send_message(chat_id, sanitize_html(m["text"]), parse_mode=ParseMode.HTML,
                                      disable_web_page_preview=True)
    if t in ("quiz", "poll"):
        if preview:
            return await bot.send_message(chat_id, _quiz_as_text(m), parse_mode=ParseMode.HTML)
        kwargs = dict(chat_id=chat_id, question=m["question"], options=m["options"], is_anonymous=True)
        if t == "quiz":
            kwargs.update(type=Poll.QUIZ, correct_option_id=m["correct_option_id"],
                          explanation=m.get("explanation"))
        return await bot.send_poll(**kwargs)
    if t == "audio":
        with open(m["file"], "rb") as f:
            msg = await bot.send_audio(chat_id, f, title=m.get("title", "MKS Listening"),
                                       performer="Multi Level MKS",
                                       caption=sanitize_html(m.get("caption", "")) or None,
                                       parse_mode=ParseMode.HTML)
        if preview:
            script = "\n".join(f"{s.get('speaker')}: {s.get('line')}" for s in m["script"])
            await bot.send_message(chat_id, "🔒 <b>Script (preview only, not posted):</b>\n"
                                   f"<tg-spoiler>{sanitize_html(script)}</tg-spoiler>",
                                   parse_mode=ParseMode.HTML)
        return msg
    if t == "meme":
        with open(m["file"], "rb") as f:
            return await bot.send_photo(chat_id, f, caption=sanitize_html(m.get("caption", "")) or None,
                                        parse_mode=ParseMode.HTML)
    raise ValueError(f"Unknown message type {t}")


async def send_preview(bot: Bot, dr: dict):
    day = DAYS[[k for k, v in DAYS.items() if v["skill"] == dr["skill"]][0]]
    head = (f"🗂 <b>Draft #{dr['id']}</b> · {dr['post_date']} · {day['label']}\n"
            f"Format: <code>{dr['format']}</code> · Topic: {sanitize_html(dr['topic'] or '-')}\n"
            f"Goes out at {SCHEDULE['publish_at']} if approved.")
    if dr["issues"]:
        head += "\n\n⚠️ <b>Checker still flags:</b>\n" + "\n".join(f"• {sanitize_html(i)}" for i in dr["issues"][:8])
    await bot.send_message(ADMIN_ID, head, parse_mode=ParseMode.HTML)
    for m in dr["payload"].get("messages", []):
        await _send_one(bot, ADMIN_ID, m, preview=True)
    await bot.send_message(ADMIN_ID, f"Draft #{dr['id']} — your call:", reply_markup=controls(dr["id"]))


async def publish(bot: Bot, dr: dict) -> list[int]:
    ids = []
    msgs = dr["payload"]["messages"]
    for n, m in enumerate(msgs):
        sent = await _send_one(bot, CHANNEL_ID, m, preview=False)
        ids.append(sent.message_id)
        if n < len(msgs) - 1:
            # quizzes right after their text; bigger gap only between separate posts
            gap = 3 if msgs[n + 1]["type"] in ("quiz", "poll") else SCHEDULE["gap_between_messages_sec"]
            await asyncio.sleep(gap)
    return ids
