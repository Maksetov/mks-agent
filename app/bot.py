"""Telegram side: admin DM commands, approval buttons, edit loop, daily jobs."""
import asyncio
import logging
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          Defaults, MessageHandler, filters)

from . import db, generator, render
from .settings import ADMIN_ID, BOT_TOKEN, CHANNEL_ID, SCHEDULE, TZ_NAME

log = logging.getLogger(__name__)
TZ = ZoneInfo(TZ_NAME)
ADMIN = filters.User(user_id=ADMIN_ID)
GEN_LOCK = asyncio.Lock()

HELP = (
    "Agent Max — commands\n"
    "/today — show or create today's draft\n"
    "/tomorrow — prepare tomorrow's draft now\n"
    "/status — what's happening today\n"
    "/check — test channel access (posts nothing)\n"
    "/cancel — cancel a pending edit\n\n"
    f"Drafts arrive at {SCHEDULE['generate_at']}, publish at {SCHEDULE['publish_at']} only if approved."
)


def _hm(s: str) -> dtime:
    h, m = map(int, s.split(":"))
    return dtime(h, m, tzinfo=TZ)


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


def hhmm() -> str:
    return now().strftime("%H:%M")


def past_publish_time() -> bool:
    return hhmm() >= SCHEDULE["publish_at"]


async def _dm(ctx, text, **kw):
    await ctx.bot.send_message(ADMIN_ID, text, **kw)


async def make_draft(ctx, d: date, notify=True):
    if GEN_LOCK.locked():
        await _dm(ctx, "⏳ Already generating something. Wait a minute.")
        return
    async with GEN_LOCK:
        if notify:
            await _dm(ctx, f"⚙️ Generating draft for {d.isoformat()}… (30–120 s)")
        draft_id = await asyncio.to_thread(generator.generate, d)
    await render.send_preview(ctx.bot, db.get(draft_id))


async def do_publish(ctx, dr):
    if db.kv_get(f"published:{dr['post_date']}"):
        return
    ids = await render.publish(ctx.bot, dr)
    db.set_status(dr["id"], "published", ids)
    db.kv_set(f"published:{dr['post_date']}", dr["id"])
    await _dm(ctx, f"📤 Draft #{dr['id']} published ({len(ids)} message(s)).")


# ---------- commands ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP)


async def cmd_today(update: Update, ctx):
    dr = db.live_for_date(today().isoformat())
    if dr and dr["status"] == "published":
        await update.message.reply_text(f"Today's post (#{dr['id']}) is already published.")
    elif dr:
        await render.send_preview(ctx.bot, dr)
    else:
        await make_draft(ctx, today())


async def cmd_tomorrow(update: Update, ctx):
    d = today() + timedelta(days=1)
    dr = db.live_for_date(d.isoformat())
    if dr:
        await render.send_preview(ctx.bot, dr)
    else:
        await make_draft(ctx, d)


async def cmd_status(update: Update, ctx):
    dr = db.live_for_date(today().isoformat())
    if not dr:
        txt = "No live draft for today. /today to create one."
    else:
        txt = f"Today: draft #{dr['id']} · {dr['format']} · status: {dr['status']}"
    pending_edit = db.kv_get("awaiting_edit")
    if pending_edit:
        txt += f"\nWaiting for your edit on draft #{pending_edit}."
    await update.message.reply_text(txt)


async def cmd_check(update: Update, ctx):
    try:
        chat = await ctx.bot.get_chat(CHANNEL_ID)
        me = await ctx.bot.get_chat_member(CHANNEL_ID, ctx.bot.id)
        ok = me.status == "administrator" and getattr(me, "can_post_messages", False)
        await update.message.reply_text(
            f"Channel: {chat.title}\nMy status: {me.status}\n"
            f"Can post: {'yes ✅' if ok else 'NO ❌ — give me Post Messages permission'}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Can't reach the channel: {e}\nCheck CHANNEL_ID and that I'm an admin.")


async def cmd_cancel(update: Update, ctx):
    db.kv_set("awaiting_edit", None)
    await update.message.reply_text("Edit cancelled.")


# ---------- buttons ----------
async def on_button(update: Update, ctx):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        await q.answer("Not yours.", show_alert=True)
        return
    await q.answer()
    action, draft_id = q.data.split(":")
    dr = db.get(int(draft_id))
    if not dr or dr["status"] not in ("pending", "approved"):
        await q.edit_message_text(f"Draft #{draft_id} is outdated ({dr['status'] if dr else 'missing'}).")
        return
    d = date.fromisoformat(dr["post_date"])

    if action == "approve":
        db.set_status(dr["id"], "approved")
        if d == today() and past_publish_time():
            await q.edit_message_text(f"✅ Draft #{dr['id']} approved — publish time passed, posting now.")
            await do_publish(ctx, db.get(dr["id"]))
        else:
            await q.edit_message_text(f"✅ Draft #{dr['id']} approved — goes out {d.isoformat()} at "
                                      f"{SCHEDULE['publish_at']}.")
    elif action == "reject":
        db.set_status(dr["id"], "rejected")
        await q.edit_message_text(f"❌ Draft #{dr['id']} rejected. /today (or /tomorrow) makes a new one.")
    elif action == "regen":
        await q.edit_message_text(f"🔄 Replacing draft #{dr['id']}…")
        await make_draft(ctx, d, notify=False)
    elif action == "edit":
        db.kv_set("awaiting_edit", dr["id"])
        await q.edit_message_text(
            f"✏️ Editing draft #{dr['id']}. Send the change in one message, e.g.\n"
            "“make quiz 2 harder”, “replace option C”, “shorter intro, less sarcasm”.\n/cancel to stop."
        )


async def on_text(update: Update, ctx):
    draft_id = db.kv_get("awaiting_edit")
    if not draft_id:
        await update.message.reply_text(HELP)
        return
    db.kv_set("awaiting_edit", None)
    await update.message.reply_text(f"⚙️ Applying your edit to draft #{draft_id}…")
    async with GEN_LOCK:
        await asyncio.to_thread(generator.revise, int(draft_id), update.message.text)
    await render.send_preview(ctx.bot, db.get(int(draft_id)))


# ---------- daily jobs ----------
async def job_generate(ctx):
    if not db.live_for_date(today().isoformat()):
        await make_draft(ctx, today())


async def job_remind(ctx):
    dr = db.live_for_date(today().isoformat())
    if not dr:
        await _dm(ctx, "⏰ No draft for today. /today to create one.")
    elif dr["status"] == "pending":
        await _dm(ctx, f"⏰ Draft #{dr['id']} still waiting. It goes out at {SCHEDULE['publish_at']} "
                       "only if you approve it.", reply_markup=render.controls(dr["id"]))


async def job_publish(ctx):
    dr = db.live_for_date(today().isoformat())
    if not dr:
        await _dm(ctx, "⏭ Nothing to publish today (no draft).")
    elif dr["status"] == "approved":
        await do_publish(ctx, dr)
    elif dr["status"] == "pending":
        db.set_status(dr["id"], "skipped")
        await _dm(ctx, f"⏭ Draft #{dr['id']} wasn't approved — skipped. Nothing was posted.")


async def job_catch_up(ctx):
    """After a restart: make today's draft if the morning run was missed."""
    if SCHEDULE["generate_at"] <= hhmm() < SCHEDULE["publish_at"]:
        await job_generate(ctx)


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error", exc_info=ctx.error)
    try:
        await _dm(ctx, f"⚠️ Error: {type(ctx.error).__name__}: {str(ctx.error)[:300]}")
    except Exception:
        pass


def build_app() -> Application:
    app = (Application.builder().token(BOT_TOKEN)
           .defaults(Defaults(tzinfo=TZ)).build())
    app.add_handler(CommandHandler(["start", "help"], cmd_start, filters=ADMIN))
    app.add_handler(CommandHandler("today", cmd_today, filters=ADMIN))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow, filters=ADMIN))
    app.add_handler(CommandHandler("status", cmd_status, filters=ADMIN))
    app.add_handler(CommandHandler("check", cmd_check, filters=ADMIN))
    app.add_handler(CommandHandler("cancel", cmd_cancel, filters=ADMIN))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(ADMIN & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    jq = app.job_queue
    jq.run_daily(job_generate, _hm(SCHEDULE["generate_at"]), name="generate")
    for i, t in enumerate(SCHEDULE["remind_at"]):
        jq.run_daily(job_remind, _hm(t), name=f"remind{i}")
    jq.run_daily(job_publish, _hm(SCHEDULE["publish_at"]), name="publish")
    jq.run_once(job_catch_up, 15, name="catch_up")
    return app
