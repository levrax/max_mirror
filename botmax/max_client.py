"""
HTTP-клиент для MAX Bot API.
Отправка, редактирование, удаление сообщений в MAX.
"""

import asyncio
import logging
from io import BytesIO

import aiohttp

import config

logger = logging.getLogger(__name__)

BASE = config.MAX_API_BASE


def _headers() -> dict[str, str]:
    return {"Authorization": config.MAX_BOT_TOKEN}


async def send_text(chat_id: int, text: str) -> str | None:
    """Отправляет текстовое сообщение. Возвращает message_id в MAX."""
    async with aiohttp.ClientSession() as s:
        resp = await s.post(
            f"{BASE}/messages",
            params={"chat_id": chat_id},
            headers=_headers(),
            json={"text": text},
        )
        data = await resp.json()
        if resp.status == 200 and "message" in data:
            mid = data["message"]["body"]["mid"]
            logger.info("MAX: sent text message %s", mid)
            return mid
        logger.error("MAX send_text error: %s %s", resp.status, data)
        return None


async def send_photo(chat_id: int, photo_bytes: bytes, caption: str = "") -> str | None:
    """Загружает фото и отправляет в MAX."""
    upload_url = await _upload_file(photo_bytes, "photo.jpg", upload_type="image")
    if not upload_url:
        return None
    body = {"text": caption} if caption else {}
    body["attachments"] = [{"type": "image", "payload": {"token": upload_url}}]
    async with aiohttp.ClientSession() as s:
        resp = await s.post(
            f"{BASE}/messages",
            params={"chat_id": chat_id},
            headers=_headers(),
            json=body,
        )
        data = await resp.json()
        if resp.status == 200 and "message" in data:
            mid = data["message"]["body"]["mid"]
            logger.info("MAX: sent photo message %s", mid)
            return mid
        logger.error("MAX send_photo error: %s %s", resp.status, data)
        return None


async def send_document(chat_id: int, doc_bytes: bytes, filename: str, caption: str = "") -> str | None:
    """Загружает файл и отправляет в MAX."""
    upload_token = await _upload_file(doc_bytes, filename)
    if not upload_token:
        return None
    body = {"text": caption} if caption else {}
    body["attachments"] = [{"type": "file", "payload": {"token": upload_token}}]
    async with aiohttp.ClientSession() as s:
        resp = await s.post(
            f"{BASE}/messages",
            params={"chat_id": chat_id},
            headers=_headers(),
            json=body,
        )
        data = await resp.json()
        if resp.status == 200 and "message" in data:
            mid = data["message"]["body"]["mid"]
            logger.info("MAX: sent document message %s", mid)
            return mid
        logger.error("MAX send_document error: %s %s", resp.status, data)
        return None


async def send_video(chat_id: int, video_bytes: bytes, caption: str = "") -> str | None:
    """Загружает видео и отправляет в MAX."""
    upload_token = await _upload_file(video_bytes, "video.mp4", upload_type="video")
    if not upload_token:
        return None
    body = {"text": caption} if caption else {}
    body["attachments"] = [{"type": "video", "payload": {"token": upload_token}}]
    async with aiohttp.ClientSession() as s:
        resp = await s.post(
            f"{BASE}/messages",
            params={"chat_id": chat_id},
            headers=_headers(),
            json=body,
        )
        data = await resp.json()
        if resp.status == 200 and "message" in data:
            mid = data["message"]["body"]["mid"]
            logger.info("MAX: sent video message %s", mid)
            return mid
        logger.error("MAX send_video error: %s %s", resp.status, data)
        return None


async def send_voice(chat_id: int, voice_bytes: bytes) -> str | None:
    """Загружает голосовое и отправляет в MAX как аудио с retry."""
    upload_token = await _upload_file(voice_bytes, "voice.ogg", upload_type="audio")
    if not upload_token:
        return None
    body = {"attachments": [{"type": "audio", "payload": {"token": upload_token}}]}
    # MAX обрабатывает аудио асинхронно — retry при attachment.not.ready
    for attempt in range(5):
        async with aiohttp.ClientSession() as s:
            resp = await s.post(
                f"{BASE}/messages",
                params={"chat_id": chat_id},
                headers=_headers(),
                json=body,
            )
            data = await resp.json()
            if resp.status == 200 and "message" in data:
                mid = data["message"]["body"]["mid"]
                logger.info("MAX: sent voice message %s", mid)
                return mid
            if data.get("code") == "attachment.not.ready":
                logger.info("MAX: audio not ready, retry %s/5...", attempt + 1)
                await asyncio.sleep(2)
                continue
            logger.error("MAX send_voice error: %s %s", resp.status, data)
            return None
    logger.error("MAX send_voice: audio not ready after 5 retries")
    return None


async def edit_message(chat_id: int, message_id: str, text: str) -> bool:
    """Редактирует текст сообщения в MAX."""
    async with aiohttp.ClientSession() as s:
        resp = await s.put(
            f"{BASE}/messages",
            params={"message_id": message_id},
            headers=_headers(),
            json={"text": text},
        )
        data = await resp.json()
        ok = resp.status == 200
        if ok:
            logger.info("MAX: edited message %s", message_id)
        else:
            logger.error("MAX edit error: %s %s", resp.status, data)
        return ok


async def delete_message(chat_id: int, message_id: str) -> bool:
    """Удаляет сообщение в MAX."""
    async with aiohttp.ClientSession() as s:
        resp = await s.delete(
            f"{BASE}/messages",
            params={"message_id": message_id},
            headers=_headers(),
        )
        ok = resp.status == 200
        if ok:
            logger.info("MAX: deleted message %s", message_id)
        else:
            data = await resp.json()
            logger.error("MAX delete error: %s %s", resp.status, data)
        return ok


async def _upload_file(file_bytes: bytes, filename: str, upload_type: str = "file") -> str | None:
    """
    Двухэтапная загрузка файла в MAX:
    1. POST /uploads?type=<type> → получаем url (и token для video)
    2. POST файл на полученный url → получаем token из ответа
    """
    async with aiohttp.ClientSession() as s:
        # Шаг 1: получаем upload URL
        resp = await s.post(
            f"{BASE}/uploads",
            params={"type": upload_type},
            headers=_headers(),
        )
        if resp.status != 200:
            text = await resp.text()
            logger.error("MAX get upload url error: %s %s", resp.status, text[:200])
            return None
        data = await resp.json()
        upload_url = data.get("url")
        if not upload_url:
            logger.error("MAX upload: no url in response: %s", data)
            return None

        # Для video token приходит сразу на шаге 1
        token = data.get("token")

        # Шаг 2: загружаем файл на полученный URL
        form = aiohttp.FormData()
        form.add_field("data", BytesIO(file_bytes), filename=filename)
        resp2 = await s.post(upload_url, data=form)
        if resp2.status != 200:
            text2 = await resp2.text()
            logger.error("MAX file upload error: %s %s", resp2.status, text2[:200])
            return None

        # Если token уже был (video) — возвращаем его
        if token:
            return token

        # Для image/file — достаём token из ответа загрузки
        try:
            data2 = await resp2.json()
            if "token" in data2:
                return data2["token"]
            # Image: {"photos": {"<photo_id>": {"token": "..."}}}
            if "photos" in data2:
                for photo_info in data2["photos"].values():
                    if isinstance(photo_info, dict) and "token" in photo_info:
                        return photo_info["token"]
        except Exception:
            pass

        logger.error("MAX upload step2: cannot extract token")
        return None
