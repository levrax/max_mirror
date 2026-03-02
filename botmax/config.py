import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_SOURCE_CHANNEL_ID = int(os.getenv("TG_SOURCE_CHANNEL_ID", "0"))

# MAX
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
MAX_TARGET_CHAT_ID = int(os.getenv("MAX_TARGET_CHAT_ID", "0"))
MAX_API_BASE = "https://platform-api.max.ru"
