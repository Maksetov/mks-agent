"""Quality gate. Hard rules in code first (cheap, exact), then two LLM passes:
1. blind solve: checker answers each quiz WITHOUT the key; disagreement = broken item
2. examiner review: level, naturalness, voice, format fit
"""
import html
import json
import re

from . import llm
from .settings import CHECK_MODEL, RULES, TEMPLATES_DIR

ALLOWED_TAGS = ("b", "i", "u", "tg-spoiler")
BANNED = [
    "believe in yourself", "you got this", "you've got this", "never give up", "dream big",
    "fuck", "shit", "#", "**",
]


ABSOLUTES = re.compile(
    r"\b(all|every|always|never|no|none|nothing|only|completely|entirely|permanently|totally|"
    r"eliminat\w*|solv\w*|guarantee\w*|impossible|cannot|most)\b", re.I)
TFNG = {"true", "false", "not given"}
HEDGED_NEG = re.compile(r"\bnot\s+(all|every|always|only|completely|entirely|necessarily|most)\b", re.I)


def _extreme(text: str) -> bool:
    return bool(ABSOLUTES.search(HEDGED_NEG.sub("", text)))


def sanitize_html(text: str) -> str:
    """Escape everything, then re-allow only Telegram-safe tags."""
    out = html.escape(text or "", quote=False)
    for t in ALLOWED_TAGS:
        out = out.replace(f"&lt;{t}&gt;", f"<{t}>").replace(f"&lt;/{t}&gt;", f"</{t}>")
    return out


def _balanced(text: str) -> bool:
    for t in ALLOWED_TAGS:
        if text.count(f"<{t}>") != text.count(f"</{t}>"):
            return False
    return True


def available_templates():
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(p.stem for p in TEMPLATES_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def hard_checks(payload: dict, day: dict) -> list[str]:
    issues = []
    msgs = payload.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return ["No messages produced."]
    if len(msgs) > RULES["max_messages"]:
        issues.append(f"Too many messages ({len(msgs)} > {RULES['max_messages']}).")
    if not payload.get("topic"):
        issues.append("Missing topic label.")

    is_listening = day.get("media") == "tts" or day["skill"] == "test"
    is_light = day["skill"] == "light"
    templates = available_templates()

    for n, m in enumerate(msgs, 1):
        t = m.get("type")
        where = f"Message {n} ({t})"
        blob = json.dumps(m, ensure_ascii=False).lower()
        for word in BANNED:
            if word in blob:
                issues.append(f"{where}: banned content '{word}'.")

        if t == "text":
            txt = m.get("text", "")
            if not txt.strip():
                issues.append(f"{where}: empty.")
            if len(txt) > 3500:
                issues.append(f"{where}: too long ({len(txt)} chars).")
            if not _balanced(sanitize_html(txt)):
                issues.append(f"{where}: unbalanced HTML tags.")

        elif t in ("quiz", "poll"):
            q, opts = m.get("question", ""), m.get("options", [])
            if not q or len(q) > 300:
                issues.append(f"{where}: question empty or > 300 chars ({len(q)}).")
            if not isinstance(opts, list) or not 2 <= len(opts) <= 10:
                issues.append(f"{where}: needs 2–10 options.")
                continue
            if any((not o) or len(o) > 100 for o in opts):
                issues.append(f"{where}: every option must be 1–100 chars.")
            if len({o.strip().lower() for o in opts}) != len(opts):
                issues.append(f"{where}: duplicate options.")
            if t == "poll" and not is_light:
                issues.append(f"{where}: regular polls are for Sunday only; use a quiz.")
            if t == "quiz":
                k = m.get("correct_option_id")
                if not isinstance(k, int) or not 0 <= k < len(opts):
                    issues.append(f"{where}: correct_option_id invalid.")
                    continue
                exp = m.get("explanation", "")
                if not exp or len(exp) > 200 or exp.count("\n") > 2:
                    issues.append(f"{where}: explanation must be 1–200 chars, max 2 line breaks.")
                # giveaway distractors: wrong options are extreme/absolute, the key is the only hedged one
                if {o.strip().lower() for o in opts} - TFNG and day["skill"] != "grammar":
                    extreme = sum(1 for i, o in enumerate(opts) if i != k and _extreme(o))
                    if extreme >= 2 and not _extreme(opts[k]):
                        issues.append(f"{where}: {extreme} distractors are extreme/absolute statements and the key "
                                      "is the only hedged one — answerable by elimination without reading.")
                # length clue: correct answer clearly the longest
                others = [len(o) for i, o in enumerate(opts) if i != k]
                if others and len(opts[k]) > 1.4 * max(others) and len(opts[k]) - max(others) > 12:
                    issues.append(f"{where}: correct option is much longer than the others (accidental clue).")

        elif t == "audio":
            if not is_listening:
                issues.append(f"{where}: audio not allowed on this day.")
            script = m.get("script") or []
            if not script or any(not s.get("line") for s in script):
                issues.append(f"{where}: empty script.")
            words = sum(len(s.get("line", "").split()) for s in script)
            if words > 280:
                issues.append(f"{where}: script too long ({words} words, max ~280).")
            if len(m.get("caption", "")) > 900:
                issues.append(f"{where}: caption too long.")

        elif t == "meme":
            if not is_light:
                issues.append(f"{where}: memes are Sunday only.")
            if m.get("template") not in templates:
                issues.append(f"{where}: unknown template '{m.get('template')}'.")
            if len(m.get("caption", "")) > 900:
                issues.append(f"{where}: caption too long.")
        else:
            issues.append(f"{where}: unknown message type.")

    # script must never leak into text messages (listening)
    for m in msgs:
        if m.get("type") == "audio":
            lines = [s.get("line", "") for s in m.get("script", []) if len(s.get("line", "")) > 40]
            texts = " ".join(x.get("text", "") for x in msgs if x.get("type") == "text")
            if any(line[:40] in texts for line in lines):
                issues.append("Listening script is printed in a text message.")
    return issues


BLIND_SYSTEM = """You are a strict C2-level English examiner taking a test item cold.
Read the context and the question, then choose the single best option.
Return ONLY JSON: {"answer_index": <0-based int>, "ambiguous": <true if 2+ options are defensible>, "reason": "<max 25 words>"}"""

GUESS_SYSTEM = """You are a test-wise B1 student. You have NOT seen the text or audio the question is about.
Using only the question and options (tone, extreme wording, common sense, option length), pick the most likely answer.
Return ONLY JSON: {"answer_index": <0-based int>, "confident": <true only if the options themselves make the answer obvious>}"""

REVIEW_SYSTEM = """You are the quality editor for Multi Level MKS, a B2–C2 English exam-prep Telegram channel.
Check the draft below against: (1) level is B2+ and difficulty comes from meaning, not rare words;
(2) English is natural and error-free (except deliberate errors in find-the-mistake items);
(3) every quiz has exactly one defensible answer and no clue from length, grammar or extreme wording;
distractors must be tempting for a B2 reader (partly true, use words from the text, mention-then-correct,
right info wrong claim). Fail it if a B1 student could pass by eliminating obviously absurd options;
(3b) quizzes in one draft test different things (e.g. gist vs detail vs inference vs NOT GIVEN), not the same idea twice;
(4) the content matches the requested format description; (5) voice: dry, direct, slightly sarcastic,
no motivational fluff, no childish tone; (6) all messages are the same skill and connected.
Only flag real problems. Return ONLY JSON: {"pass": <bool>, "issues": ["<specific, fixable issue>", ...]}"""


def _context_for(msgs, upto):
    parts = []
    for m in msgs[:upto]:
        if m.get("type") == "text":
            parts.append(m["text"])
        elif m.get("type") == "audio":
            parts.append("AUDIO TRANSCRIPT:\n" + "\n".join(f"{s.get('speaker','')}: {s.get('line','')}"
                                                          for s in m.get("script", [])))
    return "\n\n".join(parts)


def blind_solve(payload: dict) -> list[str]:
    issues = []
    msgs = payload["messages"]
    for i, m in enumerate(msgs):
        if m.get("type") != "quiz":
            continue
        opts = "\n".join(f"{j}) {o}" for j, o in enumerate(m["options"]))
        ctx = _context_for(msgs, i)
        if ctx:  # if it can be answered WITHOUT the text, the item is broken
            g = llm.chat_json(CHECK_MODEL, GUESS_SYSTEM, f"QUESTION:\n{m['question']}\n\nOPTIONS:\n{opts}", temperature=0)
            if g.get("answer_index") == m["correct_option_id"] and g.get("confident"):
                issues.append(f"Quiz '{m['question'][:60]}…' is answerable without reading/listening "
                              "(distractors too weak or too extreme). Make distractors plausible misreadings of the text.")
        user = f"CONTEXT:\n{ctx or '(none)'}\n\nQUESTION:\n{m['question']}\n\nOPTIONS:\n{opts}"
        res = llm.chat_json(CHECK_MODEL, BLIND_SYSTEM, user, temperature=0)
        ans = res.get("answer_index")
        if res.get("ambiguous"):
            issues.append(f"Quiz '{m['question'][:60]}…' is ambiguous: {res.get('reason', '')}")
        elif ans != m["correct_option_id"]:
            issues.append(
                f"Quiz '{m['question'][:60]}…': blind solver chose option {ans}, key says "
                f"{m['correct_option_id']}. Reason: {res.get('reason', '')}"
            )
    return issues


def review(payload: dict, day: dict, fmt: str) -> list[str]:
    user = (f"DAY: {day['label']} | FORMAT: {fmt}\nFORMAT DESCRIPTION: {day['formats'][fmt]}\n\n"
            f"DRAFT JSON:\n{json.dumps(payload, ensure_ascii=False, indent=1)}")
    res = llm.chat_json(CHECK_MODEL, REVIEW_SYSTEM, user, temperature=0)
    if res.get("pass") is True:
        return []
    return [f"Editor: {x}" for x in res.get("issues", [])] or ["Editor: failed without details."]


def check(payload: dict, day: dict, fmt: str) -> list[str]:
    issues = hard_checks(payload, day)
    if issues:  # don't spend LLM calls on structurally broken drafts
        return issues
    return blind_solve(payload) + review(payload, day, fmt)
