import os
import asyncio
import shutil
import logging
import tempfile
from dotenv import load_dotenv
from telegram import Update, InputMediaDocument
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from main import process_market, normalize_resolved_arg

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
ALLOWED_THREAD_ID = int(os.environ["ALLOWED_THREAD_ID"])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg:
        return

    logger.info("MSG chat_id=%s thread_id=%s text=%r", msg.chat.id, msg.message_thread_id, (msg.text or "")[:80])

    if msg.chat.id != ALLOWED_CHAT_ID or msg.message_thread_id != ALLOWED_THREAD_ID:
        logger.info("IGNORED: expected chat=%s thread=%s", ALLOWED_CHAT_ID, ALLOWED_THREAD_ID)
        return

    text = (msg.text or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(lines) < 2:
        await msg.reply_text(
            "Формат сообщения:\n<название рынка>\n<address>\n<YES или NO — опционально>",
            reply_to_message_id=msg.message_id,
        )
        return

    market_query = lines[0]
    address = lines[1]
    resolved_override = normalize_resolved_arg(lines[2]) if len(lines) >= 3 else None

    status_msg = await msg.reply_text(
        f"Обрабатываю {market_query!r} / {address[:8]}...",
        reply_to_message_id=msg.message_id,
    )

    tmp_dir = tempfile.mkdtemp(prefix="polybot_")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, process_market, market_query, address, tmp_dir, resolved_override
        )

        pnl = result['pnl']
        pnl_sign = "+" if pnl >= 0 else ""
        resolved = result['resolved_side']
        is_active = resolved is None

        summary = (
            f"*{result['market_title']}*\n"
            f"{'Status: ACTIVE' if is_active else f'Resolution: {resolved}'}\n"
            f"Trades: {result['trade_count']} "
            f"(Maker: {result['count_maker']}, Taker: {result['count_taker']})\n"
            f"\n"
            f"YES: {result['remaining_yes']:.2f} sh\n"
            f"NO:  {result['remaining_no']:.2f} sh\n"
            f"\n"
            f"{'Current value' if is_active else 'Final value'}:  `${result['final_value']:.2f}`\n"
            f"Total spent:       `${result['total_spent']:.2f}`\n"
            f"{'Unrealized PnL' if is_active else 'PnL'}:     `{pnl_sign}${pnl:.2f}`"
        )

        await context.bot.delete_message(msg.chat.id, status_msg.message_id)

        with open(result['chart_path'], 'rb') as chart_f, \
             open(result['report_path'], 'rb') as report_f, \
             open(result['trades_path'], 'rb') as trades_f:
            await msg.reply_media_group(
                media=[
                    InputMediaDocument(media=chart_f, filename="chart.png", caption=summary, parse_mode="Markdown"),
                    InputMediaDocument(media=report_f, filename="report.txt"),
                    InputMediaDocument(media=trades_f, filename="trades.json"),
                ],
                reply_to_message_id=msg.message_id,
            )

    except ValueError as e:
        await status_msg.edit_text(f"Ошибка: {e}")
    except Exception:
        logger.exception("Unexpected error while processing %s / %s", slug, address)
        await status_msg.edit_text("Произошла непредвиденная ошибка. Проверьте логи.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started. Listening for messages in chat %d, thread %d", ALLOWED_CHAT_ID, ALLOWED_THREAD_ID)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
