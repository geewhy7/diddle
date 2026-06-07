import logging
import os

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

TOKEN       = os.environ["TELEGRAM_TOKEN"]
GAME_URL    = os.environ.get("GAME_URL",    "https://diddle.retard.zone")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:7113")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


async def cmd_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[
        InlineKeyboardButton("Play Diddle 🎮", web_app=WebAppInfo(url=GAME_URL))
    ]]
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
        badge = "✨" if delta == 0 else ("+" + str(delta))
        lines.append(f"{i}\\. {e['name']} — {e['moves']}/{e['optimal']} {badge}")
    for e in gave_up:
        lines.append(f"— {e['name']} gave up")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🔤 *Diddle* — daily word ladder\n\n"
        "Turn today's start word into the target, changing one letter at a time\\.\n\n"
        "/play — open today's puzzle\n"
        "/scores — see today's leaderboard",
        parse_mode="MarkdownV2",
    )


def main() -> None:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("play",   cmd_play))
    app.add_handler(CommandHandler("scores", cmd_scores))
    app.add_handler(CommandHandler("help",   cmd_help))
    log.info("Bot polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
