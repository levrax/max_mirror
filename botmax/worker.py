"""
Фоновый worker-процесс для отслеживания удалённых постов в Telegram.

Telegram Bot API не присылает update при удалении постов в каналах.
Worker периодически проверяет, существуют ли ещё source-посты в TG.
Если пост удалён — удаляет копию из MAX.
"""

import asyncio
import logging

from telegram import Bot
from telegram.error import BadRequest

import config
import db
import max_client

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 30  # секунд


async def _check_deleted_messages(tg_bot: Bot) -> None:
    """Проверяет все маппинги. Если TG-пост удалён — удаляет из MAX."""
    mappings = db.get_all_mappings()

    for tg_id, max_id in mappings:
        try:
            # Пробуем переслать сообщение — если удалено, будет ошибка
            fwd = await tg_bot.forward_message(
                chat_id=config.TG_SOURCE_CHANNEL_ID,
                from_chat_id=config.TG_SOURCE_CHANNEL_ID,
                message_id=tg_id,
            )
            # Удаляем тестовое пересланное сообщение, чтобы не засорять канал
            try:
                await tg_bot.delete_message(
                    chat_id=config.TG_SOURCE_CHANNEL_ID,
                    message_id=fwd.message_id,
                )
            except Exception:
                pass
        except BadRequest as e:
            if "message to forward not found" in str(e).lower():
                logger.info("TG message %s deleted -> deleting MAX %s", tg_id, max_id)
                try:
                    await max_client.delete_message(config.MAX_TARGET_CHAT_ID, max_id)
                except Exception:
                    logger.exception("Failed to delete MAX message %s", max_id)
                db.delete_mapping(tg_id)
            else:
                logger.warning("Unexpected error checking TG message %s: %s", tg_id, e)
        except Exception:
            logger.exception("Error checking TG message %s", tg_id)

        await asyncio.sleep(0.5)


async def deletion_checker_loop(tg_bot: Bot) -> None:
    """Бесконечный цикл проверки удалений."""
    logger.info("Deletion checker started (interval=%ss)", CHECK_INTERVAL)
    while True:
        try:
            await _check_deleted_messages(tg_bot)
        except Exception:
            logger.exception("Error in deletion checker loop")
        await asyncio.sleep(CHECK_INTERVAL)


def run_worker(tg_token: str) -> None:
    """Точка входа для worker-процесса."""
    logging.basicConfig(
        level=logging.INFO,
        format="[WORKER] %(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    tg_bot = Bot(token=tg_token)
    asyncio.run(deletion_checker_loop(tg_bot))
