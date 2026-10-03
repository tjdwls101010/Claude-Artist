"""What image_gen needs from a prompt that can be checked before generating: the aspect ratio stated in the first sentence, since image_gen takes the ratio from the prompt alone.

The check sees only whether a number pair is there and whether it matches; a clock time such as "04:05" in the first sentence reads as a ratio too.
"""

from __future__ import annotations

import re

# 성진: an abbreviation such as "Dr." ends the first sentence early, costing one extra warning (the version is still recorded); handle abbreviations if such warnings show up on real prompts.
FIRST_SENTENCE = re.compile(r".*?[.!?][\"')”’]*(?=\s|$)", re.DOTALL)
RATIO = re.compile(r"(\d+)\s*:\s*(\d+)")


def warnings(prompt: str, aspect: str) -> list[str]:
    """Problems with how the prompt states `aspect` (W:H, or none for no check); empty when there are none."""
    if aspect == "none":
        return []
    m = FIRST_SENTENCE.match(prompt)
    first = m.group(0) if m else prompt
    stated = [(int(a), int(b)) for a, b in RATIO.findall(first) if int(a) and int(b)]
    if not stated:
        return [f"the first sentence states no aspect ratio; image_gen takes the ratio only from the prompt, so state {aspect} there"]
    w, h = (int(x) for x in aspect.split(":"))
    mismatches = list(dict.fromkeys(f"{a}:{b}" for a, b in stated if a * h != b * w))
    if mismatches:
        return [f"the first sentence states {', '.join(mismatches)} but this version's aspect is {aspect}"]
    return []
