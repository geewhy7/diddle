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

_mini_app_url: str = GAME_URL


async def _set_mini_app_url(app: Application) -> None:
    global _mini_app_url
    me = await app.bot.get_me()
    _mini_app_url = f"https://t.me/{me.username}/{BOT_APP_NAME}"
    log.info("Mini App URL: %s", _mini_app_url)


def _escape(s: str) -> str:
    for ch in r"\_*[]()~`>#+-=|{}.!":
        s = s.replace(ch, f"\\{ch}")
    return s


async def cmd_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("Play Diddle 🎮", url=_mini_app_url)]]
    await update.message.reply_text(
        "Today's puzzle is ready 🔤",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BACKEND_URL}/leaderboard",
                headers={"Authorization": f"bot {TOKEN}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            board = resp.json()
    except Exception:
        log.exception("leaderboard fetch failed")
        await update.message.reply_text("Couldn't fetch scores right now — try again in a moment.")
        return

    if not board:
        await update.message.reply_text("No scores yet today — be the first! /play")
        return

    completions = [e for e in board if not e["gave_up"]]
    gave_up     = [e for e in board if e["gave_up"]]

    lines = ["🏆 *Today's Leaderboard*\n"]
    for i, e in enumerate(completions, 1):
        delta = e["moves"] - e["optimal"]
        badge = "✨" if delta == 0 else f"\\+{delta}"
        lines.append(f"{i}\\. {_escape(e['name'])} — {e['moves']}/{e['optimal']} {badge}")
    for e in gave_up:
        lines.append(f"— {_escape(e['name'])} gave up")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_alltime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BACKEND_URL}/stats/alltime",
                headers={"Authorization": f"bot {TOKEN}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        log.exception("stats/alltime fetch failed")
        await update.message.reply_text("Couldn't fetch stats right now — try again in a moment.")
        return

    diff    = data.get("puzzle_difficulty")
    players = data.get("players", [])

    diff_str = f"\\+{diff:.1f}" if diff is not None and diff >= 0 else (
               f"{diff:.1f}" if diff is not None else "n/a"
    )

    medals = ["🥇", "🥈", "🥉"]
    lines  = ["🏌️ *All\\-Time Standings*", f"Today's difficulty: {diff_str}\n"]
    for i, p in enumerate(players):
        medal    = medals[i] if i < len(medals) else "  "
        hcap_str = f"\\+{p['handicap']:.1f}" if p["handicap"] >= 0 else f"{p['handicap']:.1f}"
        lines.append(
            f"{medal} {_escape(p['name'])}  {hcap_str} avg  "
            f"\\({p['days_played']} {'day' if p['days_played'] == 1 else 'days'}\\)"
        )

    if not players:
        lines.append("No completions yet\\.")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🔤 *Diddle* — daily word ladder\n\n"
        "Turn today's start word into the target, changing one letter at a time\\.\n\n"
        "/play — open today's puzzle\n"
        "/scores — today's leaderboard\n"
        "/alltime — all\\-time standings",
        parse_mode="MarkdownV2",
    )


def main() -> None:
    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(_set_mini_app_url)
        .build()
    )
    app.add_handler(CommandHandler("play",    cmd_play))
    app.add_handler(CommandHandler("scores",  cmd_scores))
    app.add_handler(CommandHandler("alltime", cmd_alltime))
    app.add_handler(CommandHandler("help",    cmd_help))
    log.info("Bot polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
