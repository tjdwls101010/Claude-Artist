"""The aspect-ratio warning on a prompt: codex.prompt_warnings, and add reporting it while still recording the version."""

from __future__ import annotations

import json

from imagen.codex import prompt_warnings

NO_RATIO = "the first sentence states no aspect ratio; image_gen takes the ratio only from the prompt, so state {} there"

# The prompts behind the two results 성진 chose (festival v5 and 퇴근 m6-1), as the skill quoted them at 078daf0.
FESTIVAL_V5 = """A vertical 3:4 portrait poster (taller than wide, aspect ratio 3:4) for a neighborhood autumn street festival in Mangwon-dong, Seoul. It should look like a professional Korean graphic designer illustrated it by hand with care: a warm, cozy, contemporary editorial illustration style with flat shapes, subtle risograph-like grain and paper texture, slightly imperfect hand-drawn linework, and a limited autumn palette of persimmon orange, mustard yellow, brick red, deep forest green and warm cream, with small touches of navy.

Illustration: a charming narrow Mangwon market alley seen in gentle perspective and rising toward the upper middle of the poster. It has low Korean shopfronts with striped awnings, strings of warm glowing bulbs and small paper lanterns hung across the alley, and a ginkgo tree with golden leaves drifting down. Food stalls sell tteokbokki, hotteok, roasted chestnuts and sweet potatoes, with soft steam rising. There are flea-market tables with vintage goods, and a small busker playing acoustic guitar on a tiny corner stage. Small, friendly, stylized neighbors stroll through, including families, a couple, and someone walking a dog. Late-afternoon golden light. The scene feels busy but uncluttered, with clear negative space kept for the typography.

Typography: the hierarchy should be clean, legible and well designed, and every piece of text must be rendered exactly as written. Do not add any other text, fake letters or gibberish signage. Keep shop signs blank or as simple shapes.
- Top area, large and bold: the main title in Korean, "망원동 가을 골목 축제", set in a chunky, friendly, custom hand-lettered Korean display typeface in deep brick red or forest green on the cream background. It can sit on two lines, with "망원동" on the first line and "가을 골목 축제" on the second. Every Hangul character must be accurate and complete.
- Bottom info band (a cream or deep green panel), in neat smaller type:
  "10.17 SAT – 10.18 SUN" (prominent, with an en dash between the dates)
  "망원시장 일대"
  "먹거리 · 플리마켓 · 골목 공연" (with middle dots as separators)

Composition: balanced margins and a grid-based layout like a printed silkscreen festival poster, with a few small decorative ginkgo leaves and maple leaves as accents. It should look polished and print-ready, with no watermark, no border mockup and no photo-realism."""

COMMUTE_M6_1 = """Portrait 3:4 aspect ratio, taller than it is wide. A Korean typographic poster made by a graphic designer in the language of a safety sign: one flat ground of signal yellow and a single ink of near-black. The words "오늘도 무사히 퇴근" are set in three stacked lines, "오늘도" / "무사히" / "퇴근", in an enormous custom heavy Hangul display lettering that fills the poster edge to edge, with tight spacing and letters nearly touching. The ㅇ of "오" is drawn as a round wall clock face with its two hands pointing to six o'clock, and the final ㄴ of "근" stretches into a long flat bar that runs off the right edge like a path out of the building. Flat color only, no gradients, no glow, no texture, no other text. Output the final image in a 3:4 portrait aspect ratio."""


# ---- prompt_warnings ------------------------------------------------------------------------

def test_same_ratio_in_the_first_sentence_passes():
    assert prompt_warnings("Portrait 4:5 aspect ratio, taller than it is wide. A poster.", "4:5") == []


def test_an_equivalent_ratio_passes():
    assert prompt_warnings("Portrait 8:10 aspect ratio. A poster.", "4:5") == []


def test_a_ratio_only_in_the_second_sentence_warns():
    assert prompt_warnings("A poster of a festival. Portrait 4:5 aspect ratio.", "4:5") == [NO_RATIO.format("4:5")]


def test_a_different_ratio_warns():
    assert prompt_warnings("Square 1:1 aspect ratio. A poster.", "4:5") == ["the first sentence states 1:1 but this version's aspect is 4:5"]


def test_a_different_ratio_beside_the_right_one_still_warns():
    assert prompt_warnings("Portrait 4:5, not 3 : 4, and not 16:9. A poster.", "4:5") == ["the first sentence states 3:4, 16:9 but this version's aspect is 4:5"]


def test_zero_is_not_a_ratio():
    assert prompt_warnings("Portrait 0:0 frame. A poster.", "4:5") == [NO_RATIO.format("4:5")]


def test_a_sentence_ends_after_its_closing_quote():
    assert prompt_warnings('"Portrait 1:1." Then 4:5.', "4:5") == ["the first sentence states 1:1 but this version's aspect is 4:5"]
    assert prompt_warnings('"Portrait 4:5." A square 1:1 inset.', "4:5") == []
    assert prompt_warnings("(Portrait 4:5.) A square 1:1 inset.", "4:5") == []


def test_a_decimal_does_not_end_the_first_sentence():
    assert prompt_warnings("A poster printed 1.5 times larger, portrait 4:5. Text.", "4:5") == []


def test_a_prompt_without_a_full_stop_is_one_sentence():
    assert prompt_warnings("Portrait 4:5 poster of a festival", "4:5") == []


def test_aspect_none_is_not_checked():
    assert prompt_warnings("A cut-out ginkgo leaf.", "none") == []


def test_the_prompts_behind_chosen_results_pass():
    assert prompt_warnings(FESTIVAL_V5, "3:4") == []
    assert prompt_warnings(COMMUTE_M6_1, "3:4") == []


# ---- add --------------------------------------------------------------------------------------

def _job(sb, aspect="4:5"):
    r = sb.run("init", "job", "--request", "a poster", "--aspect", aspect)
    assert r.code == 0, r.raw
    return sb.data / "job"


def _version(job, n):
    return json.loads((job / "job.json").read_text())["versions"][n - 1]


def test_add_records_the_version_and_reports_the_warning(sandbox):
    job = _job(sandbox)
    r = sandbox.run("add", job, "--direction", "d", "--method", "generate", stdin="A poster. Portrait 4:5.")
    assert r.code == 0, r.raw
    assert r.out == {"version": 1, "warnings": [NO_RATIO.format("4:5")]}
    assert _version(job, 1)["prompt"] == "A poster. Portrait 4:5."


def test_add_without_a_problem_has_no_warnings_key(sandbox):
    job = _job(sandbox)
    r = sandbox.run("add", job, "--direction", "d", "--method", "generate", stdin="Portrait 4:5. A poster.")
    assert r.code == 0 and r.out == {"version": 1}


def test_add_checks_against_an_explicit_aspect_over_the_brief(sandbox):
    job = _job(sandbox)
    r = sandbox.run("add", job, "--direction", "d", "--method", "generate", "--aspect", "3:2", stdin="Landscape 3:2. A banner.")
    assert r.code == 0 and r.out == {"version": 1}
    r = sandbox.run("add", job, "--direction", "d", "--method", "generate", "--aspect", "3:2", stdin="Portrait 4:5. A banner.")
    assert r.code == 0 and r.out == {"version": 2, "warnings": ["the first sentence states 4:5 but this version's aspect is 3:2"]}


def test_add_with_aspect_none_has_no_warnings(sandbox):
    job = _job(sandbox)
    r = sandbox.run("add", job, "--direction", "d", "--method", "generate", "--aspect", "none", stdin="A cut-out ginkgo leaf.")
    assert r.code == 0 and r.out == {"version": 1}
