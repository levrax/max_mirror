"""
Telegram -> MAX mirror bot.
Ловит новые/изменённые посты в TG-канале и дублирует их в MAX.
"""

import logging
import sys

from telegram.ext import ApplicationBuilder, MessageHandler, filters

import config
import handlers


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[BOT] %(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    if not config.TG_BOT_TOKEN:
        logger.error("TG_BOT_TOKEN not set in .env")
        sys.exit(1)
    if not config.TG_SOURCE_CHANNEL_ID:
        logger.error("TG_SOURCE_CHANNEL_ID not set in .env")
        sys.exit(1)
    if not config.MAX_BOT_TOKEN or config.MAX_BOT_TOKEN == "your_max_bot_token_here":
        logger.error("MAX_BOT_TOKEN not set in .env")
        sys.exit(1)
    if not config.MAX_TARGET_CHAT_ID:
        logger.error("MAX_TARGET_CHAT_ID not set in .env")
        sys.exit(1)

    app = ApplicationBuilder().token(config.TG_BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST, handlers.on_channel_post)
    )
    app.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_CHANNEL_POST, handlers.on_edited_channel_post
        )
    )

    logger.info(
        "Bot started. TG source: %s -> MAX target: %s",
        config.TG_SOURCE_CHANNEL_ID,
        config.MAX_TARGET_CHAT_ID,
    )
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
