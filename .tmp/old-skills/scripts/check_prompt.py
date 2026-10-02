"""Catch the prompt defects that fail silently, before a generation is spent discovering them.

A check earns a place in this file only by clearing all three bars:

1. **It fails silently.** No error, no warning -- just a different image than the one asked for.
   Omit the aspect ratio and you get a 1254x1254 square and no explanation.
2. **Its form is closed.** There is no sixteenth way to write a pixel dimension or a Midjourney
   flag. The property is decidable from the characters alone.
3. **You would otherwise pay a generation to find out**, and sometimes not even then.

**Everything else belongs in SKILL.md's pre-generation check**, and the reason is a
measurement. An earlier version tried to catch open-ended qualities with keyword lists -- did
the prompt commit to a costly composition decision, did it name a process rather than a
category, does it mix two media. Against prompts that did the right thing in wording the lists
did not anticipate, the composition check false-fired 7 times out of 8, and the media check
fired on the very fusion form the reference recommends. That is what a rule with no reason
attached looks like: it holds exactly the cases its author enumerated and snaps on the next one.

A check that punishes correct work is worse than no check. It teaches the reader to ignore the
linter, and the mechanical findings get ignored along with it. Quality opinions with a threshold
bolted on -- "under 40 words", "this adjective is empty" -- do the same damage more slowly:
they never lie, but they dilute the ERRORs next to them until the whole file reads as nagging.
Judgment about whether a prompt is *good* belongs to the pre-generation check in SKILL.md, which
unlike this file can actually read.

This module has no CLI. `generate.py --dry-run` is the only way to lint a prompt, because the
prose lives in job.json and pulling it out to a temp file to lint it is exactly what keeping it
in one place was for. `check()` and `parse_aspect()` are imported, never shelled out to.

These rules are verified by `tests/test_skill.py` at the repo root, which drives `check()` over a
fixture corpus. That suite deliberately lives outside the skill: it is a development artifact, and
this directory has to stay movable as one unit -- promoting the skill to a global or plugin install
is a directory move, and a path escaping upward out of the skill would not survive it.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------------------
# Shared helpers -- generate.py imports parse_aspect() so the two never disagree about
# what counts as an aspect ratio.
# --------------------------------------------------------------------------------------

ASPECT_RE = re.compile(r"\b(\d{1,2})\s*:\s*(\d{1,2})\b")


def parse_aspect(text: str) -> tuple[int, int] | None:
    """Return the first (w, h) aspect ratio named in the prompt, or None.

    Only named ratios count. Pixel dimensions are rejected elsewhere because this path
    ignores them entirely -- prompt text selects among fixed buckets, it does not set a size.
    """
    m = ASPECT_RE.search(text)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    return (w, h) if w and h else None


def first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


# --------------------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------------------

MJ_FLAG_RE = re.compile(r"--(?:ar|v|no|style|niji|q|s|chaos|seed|stylize|iw)\b", re.I)
WEIGHT_RE = re.compile(r"\([^()]*:\s*[01]?\.\d+\s*\)")
PIXEL_DIMS_RE = re.compile(r"\b\d{3,5}\s*[x×]\s*\d{3,5}\b", re.I)
SLOT_RE = re.compile(r"\[[A-Z][A-Z0-9_]{2,}\]")

TEXT_NOUNS = ["title", "headline", "caption", "subtitle", "lettering", "wordmark", "slogan"]

# Only used to tell "render this title" from "render no title". A refusal list mentions the same
# nouns as a request for them, and reading the second as the first is how W-TEXT-UNQUOTED fired on
# three prompts whose whole point was that no text appear.
NEGATION_RE = re.compile(r"\b(?:no|without|avoid|exclude|never|free of|devoid of)\b", re.I)


def _finding(code: str, msg: str, hint: str | None = None) -> dict:
    return {"code": code, "msg": msg, **({"hint": hint} if hint else {})}


def _has_any(text_low: str, terms: list[str]) -> list[str]:
    return [t for t in terms if t in text_low]


def _has_any_word(text_low: str, terms: list[str]) -> list[str]:
    """Whole-word variant, for vocabularies where a substring hit would be a false positive."""
    return [t for t in terms if re.search(r"\b" + re.escape(t) + r"\b", text_low)]


def _requested_text_nouns(text: str) -> list[str]:
    """Text nouns the prompt asks to render, ignoring the ones it asks to keep out.

    A noun counts as negated when a negation word sits within the few words before it, which is
    the whole span "no title", "without any lettering", "no logo, no watermark, no caption" can
    occupy. Anything wider starts swallowing the sentence before it.
    """
    found = []
    for noun in TEXT_NOUNS:
        for m in re.finditer(r"\b" + re.escape(noun) + r"\b", text, re.I):
            window = text[max(0, m.start() - 30):m.start()]
            if not NEGATION_RE.search(window):
                found.append(noun)
                break
    return found


# --------------------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------------------

def check(text: str) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    low = text.lower()
    words = _words(text)

    # ---- ERRORS: measured to break, every time ----------------------------------------

    aspect = parse_aspect(text)
    if aspect is None:
        errors.append(_finding(
            "E-NO-ASPECT",
            "화면비가 없습니다. 비율을 안 쓰면 1254x1254 정사각으로 조용히 쏠립니다.",
            "'Portrait 4:5 aspect ratio, taller than it is wide.' 처럼 비율 이름으로 씁니다.",
        ))

    if PIXEL_DIMS_RE.search(text):
        errors.append(_finding(
            "E-PIXEL-DIMS",
            "픽셀 치수가 있습니다. 이 경로에서 치수는 지정할 수 없고 무시됩니다 (총 픽셀은 항상 약 1.57MP).",
            "치수를 지우고 비율 이름만 남깁니다.",
        ))

    if MJ_FLAG_RE.search(text):
        errors.append(_finding(
            "E-MJ-FLAG", "Midjourney 플래그가 있습니다. 이 모델에서는 무의미하고 글자로 렌더될 수 있습니다."))

    if WEIGHT_RE.search(text):
        errors.append(_finding(
            "E-WEIGHT", "Stable Diffusion 가중치 문법이 있습니다. 위와 같음."))

    leaked = SLOT_RE.findall(text)
    if leaked:
        errors.append(_finding(
            "E-SLOT-LEAK",
            f"미치환 플레이스홀더가 남아 있습니다: {', '.join(sorted(set(leaked)))}",
            "슬롯 문자열은 그대로 이미지에 글자로 렌더됩니다. 실제 내용으로 바꾸세요.",
        ))

    # ---- WARNINGS: documented risk, judgment still applies ------------------------------

    # Aspect ratio behaves like viewpoint: it is dropped quietly unless it is stated up front
    # and repeated. Measured 1/4 on target when buried in a noun phrase and stated once;
    # 6/6 when it led the prompt as its own sentence and was restated at the end.
    if aspect is not None:
        w, h = aspect
        in_front = parse_aspect(first_sentence(text)) is not None
        tail = text[int(len(text) * 0.67):]
        repeated = parse_aspect(tail) is not None
        if not (in_front and repeated):
            missing = []
            if not in_front:
                missing.append("맨 앞 문장에 없음")
            if not repeated:
                missing.append("끝에서 반복되지 않음")
            warnings.append(_finding(
                "W-ASPECT-WEAK",
                f"화면비 {w}:{h}가 약하게 지정됐습니다 ({', '.join(missing)}).",
                "비율은 시점과 같은 부류로 조용히 버려집니다. 독립 문장으로 맨 앞에 두고 끝에서 한 번 더 씁니다 "
                "(실측: 묻어 쓰면 1/4 적중, 앞세우고 반복하면 6/6).",
            ))

    # There is no W-LONG-NEGATIVE. Counting negation words measured 45% false-positive over a real
    # session's prompts -- a 500-word prose prompt with five conventional refusals trips any
    # threshold you pick, while the documented failure is a comma-separated Stable-Diffusion
    # negative block, which is a different shape entirely. "How many negations is too many" is a
    # taste judgment with a number bolted on, which is exactly what the header of this file says
    # dilutes the ERRORs beside it. It went back to the pre-generation check.

    asked = _requested_text_nouns(text)
    if asked and not re.search("[\"“”]", text):
        warnings.append(_finding(
            "W-TEXT-UNQUOTED",
            f"렌더될 글자를 언급했는데({', '.join(asked)}) 따옴표 친 문자열이 없습니다.",
            '정확히 무엇이 찍혀야 하는지 따옴표로 지정합니다: reading "SOLSTICE"'))

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {"words": len(words), "aspect": f"{aspect[0]}:{aspect[1]}" if aspect else None},
    }
