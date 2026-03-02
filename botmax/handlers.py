"""
Обработчики событий Telegram-канала.
Пересылка постов из Telegram -> MAX.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
import db
import max_client

logger = logging.getLogger(__name__)


async def _download_tg_file(file_obj) -> bytes:
    """Скачивает файл из Telegram и возвращает байты."""
    f = await file_obj.get_file()
    return await f.download_as_bytearray()


async def _send_to_max(msg) -> str | None:
    """Определяет тип контента и отправляет в MAX. Возвращает MAX message_id."""
    chat_id = config.MAX_TARGET_CHAT_ID
    caption = msg.caption or ""
    text = msg.text or ""

    # Фото
    if msg.photo:
        photo = msg.photo[-1]  # наибольшее разрешение
        photo_bytes = await _download_tg_file(photo)
        return await max_client.send_photo(chat_id, bytes(photo_bytes), caption)

    # Видео
    if msg.video:
        video_bytes = await _download_tg_file(msg.video)
        return await max_client.send_video(chat_id, bytes(video_bytes), caption)

    # Голосовое сообщение
    if msg.voice:
        voice_bytes = await _download_tg_file(msg.voice)
        return await max_client.send_voice(chat_id, bytes(voice_bytes))

    # Аудио
    if msg.audio:
        audio_bytes = await _download_tg_file(msg.audio)
        filename = msg.audio.file_name or "audio.mp3"
        return await max_client.send_document(chat_id, bytes(audio_bytes), filename, caption)

    # Видеосообщение (кружок)
    if msg.video_note:
        vn_bytes = await _download_tg_file(msg.video_note)
        return await max_client.send_video(chat_id, bytes(vn_bytes), "")

    # Документ
    if msg.document:
        doc_bytes = await _download_tg_file(msg.document)
        filename = msg.document.file_name or "file"
        return await max_client.send_document(chat_id, bytes(doc_bytes), filename, caption)

    # Текст
    if text:
        return await max_client.send_text(chat_id, text)

    # Стикер — отправляем как текст-заглушку
    if msg.sticker:
        emoji = msg.sticker.emoji or "sticker"
        return await max_client.send_text(chat_id, f"[Стикер: {emoji}]")

    # Остальное — пробуем отправить caption или заглушку
    if caption:
        return await max_client.send_text(chat_id, caption)

    logger.warning("Unsupported message type: %s", msg.message_id)
    return None


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Новый пост в TG-канале -> отправить в MAX."""
    msg = update.channel_post
    if msg is None or msg.chat.id != config.TG_SOURCE_CHANNEL_ID:
        return

    # Игнорируем пересланные сообщения (worker проверяет удаления через forward)
    if msg.forward_origin is not None:
        return

    try:
        max_id = await _send_to_max(msg)
        if max_id:
            db.save_mapping(msg.message_id, max_id)
            logger.info("TG %s -> MAX %s", msg.message_id, max_id)
    except Exception:
        logger.exception("Failed to mirror message %s", msg.message_id)


async def on_edited_channel_post(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Редактирование поста в TG -> редактирование/пересылка в MAX."""
    msg = update.edited_channel_post
    if msg is None or msg.chat.id != config.TG_SOURCE_CHANNEL_ID:
        return

    max_id = db.get_max_id(msg.message_id)
    chat_id = config.MAX_TARGET_CHAT_ID

    if max_id is None:
        # Нет маппинга — просто отправляем заново
        try:
            new_max_id = await _send_to_max(msg)
            if new_max_id:
                db.save_mapping(msg.message_id, new_max_id)
        except Exception:
            logger.exception("Failed to re-mirror edited message %s", msg.message_id)
        return

    # Если пост текстовый — пробуем отредактировать через API
    if msg.text:
        try:
            ok = await max_client.edit_message(chat_id, max_id, msg.text)
            if ok:
                logger.info("Edited MAX message %s", max_id)
                return
        except Exception:
            logger.exception("Failed to edit MAX message %s", max_id)

    # Для медиа или если редактирование не удалось —
    # удаляем старое и отправляем заново
    try:
        await max_client.delete_message(chat_id, max_id)
        new_max_id = await _send_to_max(msg)
        if new_max_id:
            db.save_mapping(msg.message_id, new_max_id)
            logger.info("Re-mirrored edited message TG %s -> MAX %s", msg.message_id, new_max_id)
    except Exception:
        logger.exception("Failed to re-mirror edited message %s", msg.message_id)
