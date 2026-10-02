"""Agent Max — Multi Level MKS channel agent. Run: venv/bin/python main.py"""
import logging

from app import db
from app.bot import build_app

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # otherwise every poll request gets logged

if __name__ == "__main__":
    db.init()
    build_app().run_polling(drop_pending_updates=True)
