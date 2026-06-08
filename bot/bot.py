import logging
import os

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

TOKEN        = os.environ["TELEGRAM_TOKEN"]
GAME_URL     = os.environ.get("GAME_URL",     "https://diddle.retard.zone")
BACKEND_URL  = os.environ.get("BACKEND_URL",  "http://localhost:7113")
BOT_APP_NAME = os.environ.get("BOT_APP_NAME", "diddle")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Populated at startup once we know the bot's username.
_mini_app_url: str = GAME_URL


async def _set_mini_app_url(app: Application) -> None:
    """Resolve the t.me/{botname}/{appname} Mini App link at startup."""
    global _mini_app_url
    me = await app.bot.get_me()
    _mini_app_url = f"https://t.me/{me.username}/{BOT_APP_NAME}"
    log.info("Mini App URL: %s", _mini_app_url)


async def _record_member(update: Update) -> None:
    """Record the interacting user as a member of this chat (group or supergroup)."""
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None or chat.type not in ("group", "supergroup"):
        return
    display_name = user.first_name
    if user.last_name:
        display_name += f" {user.last_name}"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{BACKEND_URL}/group/member",
                headers={"Authorization": f"bot {TOKEN}"},
                json={
                    "user_id":      user.id,
                    "chat_id":      chat.id,
                    "display_name": display_name,
                    "username":     user.username,
                },
                timeout=5.0,
            )
    except Exception:
        log.warning("Failed to record group member %s in chat %s", user.id, chat.id)


async def cmd_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _record_member(update)
    keyboard = [[InlineKeyboardButton("Play Diddle 🎮", url=_mini_app_url)]]
    await update.message.reply_text(
        "Today's puzzle is ready 🔤",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


ADMIN_ID = 909757170


def _escape(s: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        s = s.replace(ch, f"\\{ch}")
    return s


async def _fetch_leaderboard(chat_id: int | None) -> list[dict] | None:
    params = {"chat_id": chat_id} if chat_id is not None else {}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BACKEND_URL}/leaderboard",
                headers={"Authorization": f"bot {TOKEN}"},
                params=params,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception:
        log.exception("leaderboard fetch failed")
        return None


def _format_leaderboard(board: list[dict], title: str = "Today's Leaderboard") -> str:
    completions = [e for e in board if not e["gave_up"]]
    gave_up     = [e for e in board if e["gave_up"]]
    lines = [f"🏆 *{_escape(title)}*\n"]
    for i, e in enumerate(completions, 1):
        delta = e["moves"] - e["optimal"]
        badge = "✨" if delta == 0 else f"\\+{delta}"
        lines.append(f"{i}\\. {_escape(e['name'])} — {e['moves']}/{e['optimal']} {badge}")
    for e in gave_up:
        lines.append(f"— {_escape(e['name'])} gave up")
    return "\n".join(lines)


async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _record_member(update)
    chat_id = update.effective_chat.id
    board   = await _fetch_leaderboard(chat_id)
    if board is None:
        await update.message.reply_text("Couldn't fetch scores right now — try again in a moment.")
        return
    if not board:
        await update.message.reply_text("No scores yet today — be the first! /play")
        return
    await update.message.reply_text(_format_leaderboard(board), parse_mode="MarkdownV2")


async def cmd_scores_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return  # complete silence for non-admin
    board = await _fetch_leaderboard(None)
    if board is None:
        await update.message.reply_text("Couldn't fetch scores right now.")
        return
    if not board:
        await update.message.reply_text("No scores yet today.")
        return
    await update.message.reply_text(
        _format_leaderboard(board, "All-Group Leaderboard"),
        parse_mode="MarkdownV2",
    )


async def cmd_alltime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _record_member(update)
    chat_id = update.effective_chat.id
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BACKEND_URL}/stats/group",
                headers={"Authorization": f"bot {TOKEN}"},
                params={"chat_id": chat_id},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        log.exception("stats/group fetch failed")
        await update.message.reply_text("Couldn't fetch stats right now — try again in a moment.")
        return

    diff      = data.get("puzzle_difficulty")
    handicaps = data.get("handicaps", [])
    diff_str  = f"\\+{diff:.1f}" if diff is not None else "n/a"

    medals = ["🥇", "🥈", "🥉"]
    lines  = [f"🏌️ *All\\-Time Handicaps*", f"Today's difficulty: {diff_str}\n"]
    for i, h in enumerate(handicaps):
        medal = medals[i] if i < len(medals) else "  "
        lines.append(
            f"{medal} {_escape(h['name'])}  \\+{h['handicap']:.1f}  "
            f"\\({h['days_played']} days\\)"
        )
    if not handicaps:
        lines.append("Not enough data yet — play at least 3 days to appear here\\.")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _record_member(update)
    await update.message.reply_text(
        "🔤 *Diddle* — daily word ladder\n\n"
        "Turn today's start word into the target, changing one letter at a time\\.\n\n"
        "/play — open today's puzzle\n"
        "/scores — see today's leaderboard",
        parse_mode="MarkdownV2",
    )


def main() -> None:
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(_set_mini_app_url)
        .build()
    )
    app.add_handler(CommandHandler("play",       cmd_play))
    app.add_handler(CommandHandler("scores",     cmd_scores))
    app.add_handler(CommandHandler("scores_all", cmd_scores_all))
    app.add_handler(CommandHandler("alltime",    cmd_alltime))
    app.add_handler(CommandHandler("help",       cmd_help))
    log.info("Bot polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
