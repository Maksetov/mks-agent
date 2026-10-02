"""Thin OpenAI wrapper. Swapping provider later = rewrite this file only."""
import json
import logging

from openai import BadRequestError, OpenAI

from .settings import OPENAI_API_KEY, TTS_MODEL

log = logging.getLogger(__name__)
client = OpenAI(api_key=OPENAI_API_KEY, timeout=120, max_retries=2)


def chat_json(model: str, system: str, user: str, temperature: float | None = None) -> dict:
    kwargs = dict(
        model=model,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    if temperature is not None:
        kwargs["temperature"] = temperature
    try:
        r = client.chat.completions.create(**kwargs)
    except BadRequestError as e:
        # some models reject custom temperature; retry with defaults
        if "temperature" in str(e) and "temperature" in kwargs:
            kwargs.pop("temperature")
            r = client.chat.completions.create(**kwargs)
        else:
            raise
    text = r.choices[0].message.content or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("Model returned invalid JSON: %s", text[:300])
        return {}


def tts(text: str, voice: str) -> bytes:
    r = client.audio.speech.create(model=TTS_MODEL, voice=voice, input=text, response_format="mp3")
    return r.content
