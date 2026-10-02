"""Loads .env and config/*.yaml once. Everything else imports from here."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")


def _req(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required setting in .env: {name}")
    return val


BOT_TOKEN = _req("AGENT_BOT_TOKEN")
ADMIN_ID = int(_req("ADMIN_USER_ID"))
CHANNEL_ID = int(_req("CHANNEL_ID"))
OPENAI_API_KEY = _req("OPENAI_API_KEY")
GEN_MODEL = os.getenv("GEN_MODEL", "gpt-4.1")
CHECK_MODEL = os.getenv("CHECK_MODEL", GEN_MODEL)
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
TZ_NAME = os.getenv("TZ", "Asia/Tashkent")

DATA_DIR = BASE / "data"
MEDIA_OUT = DATA_DIR / "media"
DB_PATH = DATA_DIR / "agent.db"
TEMPLATES_DIR = BASE / "media" / "templates"
DATA_DIR.mkdir(exist_ok=True)
MEDIA_OUT.mkdir(exist_ok=True)

CAL = yaml.safe_load((BASE / "config" / "calendar.yaml").read_text(encoding="utf-8"))
VOICE = (BASE / "config" / "voice.md").read_text(encoding="utf-8")
SCHEDULE = CAL["schedule"]
RULES = CAL["rules"]
DAYS = CAL["days"]
