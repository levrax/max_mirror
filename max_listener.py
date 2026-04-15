"""
MAX Bot long-polling listener.
Слушает входящие сообщения в MAX-боте.
Если пользователь присылает t.me ссылку — отвечает контентом из кэша.
"""

import asyncio
import logging
import os
import re
import sys

import aiohttp

# Настройка пути
sys.path.insert(0, os.path.dirname(__file__))

import cache
import config
import db

logging.basicConfig(
    level=logging.INFO,
    format="[MAX-LISTENER] %(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BASE = config.MAX_API_BASE.rstrip("/")
_TG_LINK_RE = re.compile(r"https?://t\.me/(\w+)/(\d+)")
_MAX_DEEPLINK_RE = re.compile(r"https?://max\.ru/\w+\?start=(\w+)_(\d+)")

_TIMEOUT = aiohttp.ClientTimeout(total=60)

# Дедупликация: не обрабатываем один и тот же update дважды
_processed_updates: set[str] = set()
_PROCESSED_MAX = 5000  # макс размер кэша


async def _send_text(session: aiohttp.ClientSession, chat_id: int, text: str, fmt: str | None = None) -> None:
    body: dict = {"text": text}
    if fmt:
        body["format"] = fmt
    async with session.post(
        f"{BASE}/messages",
        params={"access_token": config.MAX_BOT_TOKEN, "chat_id": chat_id},
        json=body,
    ) as resp:
        if resp.status != 200:
            logger.error("Send text error: %s", resp.status)


async def _send_photo(session: aiohttp.ClientSession, chat_id: int, photo_bytes: bytes, caption: str = "", fmt: str | None = None) -> None:
    # Upload
    async with session.post(
        f"{BASE}/uploads",
        params={"access_token": config.MAX_BOT_TOKEN, "type": "image"},
    ) as resp:
        if resp.status != 200:
            return
        data = await resp.json()
    upload_url = data.get("url")
    if not upload_url:
        return
    form = aiohttp.FormData()
    form.add_field("data", photo_bytes, filename="photo.jpg")
    async with session.post(upload_url, data=form) as resp2:
        if resp2.status != 200:
            return
        data2 = await resp2.json()
    token = None
    if "token" in data2:
        token = data2["token"]
    elif "photos" in data2:
        for info in data2["photos"].values():
            if isinstance(info, dict) and "token" in info:
                token = info["token"]
                break
    if not token:
        return
    body: dict = {"text": caption} if caption else {}
    if fmt:
        body["format"] = fmt
    body["attachments"] = [{"type": "image", "payload": {"token": token}}]
    async with session.post(
        f"{BASE}/messages",
        params={"access_token": config.MAX_BOT_TOKEN, "chat_id": chat_id},
        json=body,
    ) as resp:
        pass


async def _send_video(session: aiohttp.ClientSession, chat_id: int, video_bytes: bytes, caption: str = "", fmt: str | None = None) -> None:
    async with session.post(
        f"{BASE}/uploads",
        params={"access_token": config.MAX_BOT_TOKEN, "type": "video"},
    ) as resp:
        if resp.status != 200:
            return
        data = await resp.json()
    upload_url = data.get("url")
    token = data.get("token")
    if not upload_url:
        return
    form = aiohttp.FormData()
    form.add_field("data", video_bytes, filename="video.mp4")
    async with session.post(upload_url, data=form) as resp2:
        pass
    if not token:
        return
    body: dict = {"text": caption} if caption else {}
    if fmt:
        body["format"] = fmt
    body["attachments"] = [{"type": "video", "payload": {"token": token}}]
    async with session.post(
        f"{BASE}/messages",
        params={"access_token": config.MAX_BOT_TOKEN, "chat_id": chat_id},
        json=body,
    ) as resp:
        pass


_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
_PHOTO_RE = re.compile(r"background-image:url\('([^']+)'\)")
_AUTHOR_RE = re.compile(r'<span[^>]*class="tgme_widget_message_owner_name"[^>]*><span[^>]*>([^<]+)</span>')
_TAG_RE = re.compile(r'<[^>]+>')
_BR_RE = re.compile(r'<br\s*/?>')


def _html_to_text(html: str) -> str:
    text = _BR_RE.sub('\n', html)
    text = _TAG_RE.sub('', text)
    text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
    return text.strip()


async def _fetch_tg_post(session: aiohttp.ClientSession, channel: str, msg_id: int) -> dict | None:
    """Парсит публичный TG пост через embed страницу."""
    url = f"https://t.me/{channel}/{msg_id}?embed=1"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception:
        logger.warning("Failed to fetch TG embed %s/%s", channel, msg_id)
        return None

    result: dict = {"channel": channel, "msg_id": msg_id}
    text_match = _TEXT_RE.search(html)
    if text_match:
        result["text"] = _html_to_text(text_match.group(1))
    photo_match = _PHOTO_RE.search(html)
    if photo_match:
        result["photo_url"] = photo_match.group(1)
    author_match = _AUTHOR_RE.search(html)
    if author_match:
        result["author"] = author_match.group(1).strip()
    if not result.get("text") and not result.get("photo_url"):
        return None
    return result


async def _handle_tg_link(session: aiohttp.ClientSession, chat_id: int, channel_name: str, msg_id: int) -> bool:
    """Раскрывает TG ссылку — из кэша или через embed."""
    # Сначала кэш (свои каналы)
    for ch_id, title, username in db.get_all_tg_channels():
        if (username and username.lower() == channel_name.lower()) or str(ch_id) == channel_name:
            meta = cache.load(ch_id, msg_id)
            if meta:
                text = meta.get("text", "")
                media = meta.get("media", [])
                if media:
                    for m in media:
                        path = cache.get_media_path(ch_id, m["file"])
                        if not path:
                            continue
                        with open(path, "rb") as f:
                            data = f.read()
                        if m["type"] in ("photo", "image"):
                            await _send_photo(session, chat_id, data, text)
                        elif m["type"] == "video":
                            await _send_video(session, chat_id, data, text)
                elif text:
                    await _send_text(session, chat_id, text)
                return True
            break

    # Парсим публичный пост через embed
    post = await _fetch_tg_post(session, channel_name, msg_id)
    if not post:
        return False

    author = post.get("author", channel_name)
    post_text = post.get("text", "")
    header = f"**{author}:**\n{post_text}" if post_text else ""

    if post.get("photo_url"):
        try:
            async with session.get(post["photo_url"], timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    photo_bytes = await resp.read()
                    await _send_photo(session, chat_id, photo_bytes, header, fmt="markdown" if header else None)
                    return True
        except Exception:
            pass
    if header:
        await _send_text(session, chat_id, header, fmt="markdown")
        return True
    return False


_START_PAYLOAD_RE = re.compile(r"^(\w+)_(\d+)$")


async def _process_bot_started(session: aiohttp.ClientSession, update: dict) -> None:
    """Обрабатывает bot_started с payload (deep link)."""
    payload = update.get("payload")
    chat_id = update.get("chat_id")
    if not payload or not chat_id:
        return

    m = _START_PAYLOAD_RE.match(payload)
    if not m:
        return

    channel_name = m.group(1)
    msg_id = int(m.group(2))
    logger.info("Deep link: channel=%s msg=%s chat=%s", channel_name, msg_id, chat_id)
    ok = await _handle_tg_link(session, chat_id, channel_name, msg_id)
    if not ok:
        await _send_text(session, chat_id, "Не удалось получить пост. Возможно канал приватный.")


async def _process_message(session: aiohttp.ClientSession, update: dict) -> None:
    """Обрабатывает входящее сообщение."""
    msg = update.get("message", {})
    body = msg.get("body", {})
    text = body.get("text", "")
    chat_id = msg.get("recipient", {}).get("chat_id")

    if not chat_id or not text:
        return

    links = _TG_LINK_RE.findall(text) + _MAX_DEEPLINK_RE.findall(text)
    if not links:
        return

    found_any = False
    for channel_name, msg_id_str in links:
        msg_id = int(msg_id_str)
        ok = await _handle_tg_link(session, chat_id, channel_name, msg_id)
        if ok:
            found_any = True

    if not found_any:
        await _send_text(session, chat_id, "Не удалось получить пост. Возможно канал приватный.")


async def run() -> None:
    db.init_db()
    logger.info("MAX listener started (long polling)")

    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        marker = None
        while True:
            try:
                params: dict = {
                    "timeout": 30,
                    "types": "message_created,bot_started",
                }
                if marker:
                    params["marker"] = marker

                headers = {"Authorization": config.MAX_BOT_TOKEN}
                async with session.get(f"{BASE}/updates", params=params, headers=headers) as resp:
                    if resp.status != 200:
                        body_text = await resp.text()
                        logger.error("GET /updates error: %s %s", resp.status, body_text[:500])
                        await asyncio.sleep(5)
                        continue
                    data = await resp.json()

                new_marker = data.get("marker")
                if new_marker:
                    marker = new_marker

                updates = data.get("updates", [])
                if updates:
                    logger.info("Got %d updates", len(updates))
                for upd in updates:
                    # Дедупликация по timestamp + update_type
                    upd_ts = str(upd.get("timestamp", ""))
                    upd_type = upd.get("update_type", "")
                    upd_key = f"{upd_type}:{upd_ts}"
                    if upd_key in _processed_updates:
                        logger.debug("Skipping duplicate update: %s", upd_key)
                        continue
                    _processed_updates.add(upd_key)
                    if len(_processed_updates) > _PROCESSED_MAX:
                        _processed_updates.clear()

                    logger.info("Update [%s]: %s", upd_type, str(upd)[:500])
                    try:
                        if upd_type == "bot_started":
                            await _process_bot_started(session, upd)
                        elif upd_type == "message_created":
                            await _process_message(session, upd)
                        # Неизвестные update_type игнорируем
                    except Exception:
                        logger.exception("Error processing update")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("Polling error: %s", e)
                await asyncio.sleep(5)
            except Exception:
                logger.exception("Unexpected error in polling loop")
                await asyncio.sleep(5)


def main():
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    asyncio.run(run())


if __name__ == "__main__":
    main()
