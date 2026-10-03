"""The instruction wrapped around a prompt so Codex passes it to image_gen untouched and reports what it passed.

Codex left to itself restructures a prompt into its bundled imagegen skill's labelled fields (Use case:, Asset type:, ...). The envelope asks for a verbatim pass and a self-report; the stronger form is used once after a report showed the prompt was changed.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA = {
    "type": "object",
    "properties": {"prompt_passed": {"type": "string"}, "image_path": {"type": "string"}},
    "required": ["prompt_passed", "image_path"],
    "additionalProperties": False,
}

ENVELOPE = """You are an image-generation executor. Your only job is to make one tool call and report what you passed.

Call the built-in image_gen tool EXACTLY ONCE. Pass the text between the <PROMPT> markers as the `prompt` argument, byte-for-byte verbatim. Do not restructure it, do not add labeled fields, do not add a "Use case:" line, do not append or prepend anything, and do not summarize it. The prompt has been authored deliberately; any edit to it is a defect.
{extra}
<PROMPT>
{prompt}
</PROMPT>

Respond with JSON matching the provided schema: `prompt_passed` must be the exact string you passed as the `prompt` argument, and `image_path` the absolute path of the saved PNG.
"""

STRONGER = """
YOUR PREVIOUS ATTEMPT MODIFIED THE PROMPT AND THE RUN WAS REJECTED. Do not restructure the text into labelled fields of any kind -- not "Use case:", not "Subject:", not "Scene/backdrop:", not "Style/medium:", not any other label. The text between the markers is already a finished prompt. Copy it character for character.
"""


def build(prompt: str, refs: list[Path], transparent: bool, stronger: bool) -> str:
    extra = []
    if refs:
        extra.append("Also pass referenced_image_paths: [" + ", ".join(json.dumps(str(p)) for p in refs) + "]")
    if transparent:
        extra.append("Set `transparent_background` to true.")
    body = ENVELOPE.format(extra=("\n" + "\n".join(extra) + "\n") if extra else "", prompt=prompt)
    return body + STRONGER if stronger else body
