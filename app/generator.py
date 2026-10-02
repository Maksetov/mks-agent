"""Builds a day's draft: pick format -> generate -> check -> retry with feedback -> render media."""
import json
import logging
import random
import time
from datetime import date, timedelta

from . import db, llm
from .checker import available_templates, check
from .media import attach_media
from .settings import DAYS, GEN_MODEL, RULES, VOICE

log = logging.getLogger(__name__)


def day_cfg(d: date) -> dict:
    return DAYS[d.strftime("%a").lower()[:3]]


def pick_format(d: date, day: dict, exclude: str | None = None) -> str:
    formats = list(day["formats"])
    if day["skill"] == "light" and not available_templates():
        formats = [f for f in formats if f != "meme"] or formats
    if exclude and len(formats) > 1:
        formats = [f for f in formats if f != exclude]
    since = (d - timedelta(days=RULES["no_repeat_format_days"])).isoformat()
    used = db.recent_formats(day["skill"], since)
    fresh = [f for f in formats if f not in used]
    if fresh:
        return random.choice(fresh)
    return min(formats, key=lambda f: used.get(f, ""))  # least recently used


def _system() -> str:
    return VOICE.replace("MAX_MESSAGES", str(RULES["max_messages"]))


def _user_prompt(d: date, day: dict, fmt: str, feedback: list[str] | None) -> str:
    parts = [
        f"DATE: {d.isoformat()} ({d.strftime('%A')})",
        f"DAY LABEL: {day['label']}   SKILL: {day['skill']}",
        f"FORMAT: {fmt}\n{day['formats'][fmt]}",
    ]
    if day.get("exam_style") == "strict":
        parts.append("Exam style is strict: wording, options and difficulty must look like the real Multi Level exam.")
    if day["skill"] == "light":
        tpl = available_templates()
        parts.append("AVAILABLE MEME TEMPLATES: " + (", ".join(tpl) if tpl else "none — do not use meme"))
    recent = db.recent_topics(RULES["avoid_topics_lookback"])
    if recent:
        parts.append("RECENT TOPICS (do not repeat or closely imitate):\n- " + "\n- ".join(recent))
    if feedback:
        parts.append("YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS. Fix all of them:\n- " + "\n- ".join(feedback))
    return "\n\n".join(parts)


def _shuffle_quiz(m: dict) -> None:
    """Randomise the key's position (models love option A). TFNG keeps its fixed order."""
    opts = m.get("options")
    k = m.get("correct_option_id")
    if not isinstance(opts, list) or not isinstance(k, int) or not 0 <= k < len(opts):
        return
    if {o.strip().lower() for o in opts} <= {"true", "false", "not given"}:
        return
    key = opts[k]
    random.shuffle(opts)
    m["correct_option_id"] = opts.index(key)


def _clean(payload: dict, shuffle: bool = True) -> dict:
    payload.setdefault("messages", [])
    for m in payload["messages"]:
        m.pop("file", None)  # never trust file paths from the model
        if shuffle and m.get("type") == "quiz":
            _shuffle_quiz(m)
    return payload


def generate(d: date, exclude_format: str | None = None) -> int:
    """Create a new pending draft for date d. Returns draft id. Blocking (run in a thread)."""
    day = day_cfg(d)
    fmt = pick_format(d, day, exclude_format)
    feedback, payload, issues = None, {}, ["not generated"]
    for attempt in range(1, RULES["max_attempts"] + 1):
        payload = _clean(llm.chat_json(GEN_MODEL, _system(), _user_prompt(d, day, fmt, feedback), temperature=0.8))
        issues = check(payload, day, fmt)
        log.info("Draft %s %s attempt %d: %d issues", d, fmt, attempt, len(issues))
        if not issues:
            break
        feedback = issues
    if payload.get("messages"):
        try:
            attach_media(payload, f"{d.isoformat()}_{int(time.time())}")
        except Exception as e:  # media failure must not kill the draft
            log.exception("Media rendering failed")
            issues = issues + [f"Media rendering failed: {e}"]
    return db.add_draft(d.isoformat(), day["skill"], fmt, payload, issues)


REVISE_SYSTEM_TAIL = """

YOU ARE EDITING AN EXISTING DRAFT. Apply the editor's instruction exactly, keep everything else
unchanged unless the instruction requires it, and return the FULL updated JSON in the same schema."""


def revise(draft_id: int, instruction: str) -> int:
    """Apply Dior's edit instruction to a draft, re-check, re-render media. Returns draft id."""
    dr = db.get(draft_id)
    d = date.fromisoformat(dr["post_date"])
    day, fmt = day_cfg(d), dr["format"]
    current = _clean(json.loads(json.dumps(dr["payload"])), shuffle=False)
    user = (f"{_user_prompt(d, day, fmt, None)}\n\nCURRENT DRAFT JSON:\n"
            f"{json.dumps(current, ensure_ascii=False, indent=1)}\n\nEDITOR'S INSTRUCTION:\n{instruction}")
    payload = _clean(llm.chat_json(GEN_MODEL, _system() + REVISE_SYSTEM_TAIL, user, temperature=0.4), shuffle=False)
    if not payload.get("messages"):
        raise RuntimeError("Edit failed: the model returned nothing usable. Draft unchanged.")
    issues = check(payload, day, fmt)
    try:
        attach_media(payload, f"{d.isoformat()}_{int(time.time())}")
    except Exception as e:
        log.exception("Media rendering failed")
        issues.append(f"Media rendering failed: {e}")
    db.update_payload(draft_id, payload, issues)
    return draft_id
