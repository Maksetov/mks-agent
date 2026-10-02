"""Audio (OpenAI TTS) and meme rendering (Pillow)."""
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import llm
from .settings import MEDIA_OUT, TEMPLATES_DIR

VOICES = {"N": "sage", "A": "alloy", "B": "onyx", "C": "nova"}
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def build_audio(script: list[dict], draft_tag: str) -> str:
    """One TTS call per line, MP3 frames concatenated (plays fine in Telegram)."""
    out = MEDIA_OUT / f"{draft_tag}.mp3"
    chunks = []
    for s in script:
        voice = VOICES.get(str(s.get("speaker", "N")).upper()[:1], "alloy")
        chunks.append(llm.tts(s["line"], voice))
    out.write_bytes(b"".join(chunks))
    return str(out)


def _font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default(size=size)


def _draw_block(draw, img_w, text, y, size, from_bottom=False, img_h=0):
    font = _font(size)
    lines = textwrap.wrap(text.upper(), width=max(12, int(img_w / (size * 0.62))))
    line_h = size + 6
    if from_bottom:
        y = img_h - line_h * len(lines) - 20
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(((img_w - w) / 2, y), line, font=font, fill="white",
                  stroke_width=max(2, size // 15), stroke_fill="black")
        y += line_h


def build_meme(template: str, top: str, bottom: str, draft_tag: str) -> str:
    src = next(p for p in TEMPLATES_DIR.iterdir() if p.stem == template)
    img = Image.open(src).convert("RGB")
    if img.width > 1280:
        img = img.resize((1280, int(img.height * 1280 / img.width)))
    draw = ImageDraw.Draw(img)
    size = max(28, img.width // 14)
    if top:
        _draw_block(draw, img.width, top, 15, size)
    if bottom:
        _draw_block(draw, img.width, bottom, 0, size, from_bottom=True, img_h=img.height)
    out = MEDIA_OUT / f"{draft_tag}.jpg"
    img.save(out, quality=90)
    return str(out)


def attach_media(payload: dict, draft_tag: str) -> None:
    """Render audio/meme files and store their paths inside the payload."""
    for n, m in enumerate(payload["messages"]):
        tag = f"{draft_tag}_{n}"
        if m["type"] == "audio":
            m["file"] = build_audio(m["script"], tag)
        elif m["type"] == "meme":
            m["file"] = build_meme(m["template"], m.get("top", ""), m.get("bottom", ""), tag)
