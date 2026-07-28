import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "llm"))

import coaching_style


def test_each_registered_style_returns_its_own_prompt():
    assert coaching_style.get_system_prompt("aggressive") == coaching_style.AGGRESSIVE_PROMPT
    assert coaching_style.get_system_prompt("supportive") == coaching_style.SUPPORTIVE_PROMPT
    assert coaching_style.get_system_prompt("technical") == coaching_style.TECHNICAL_PROMPT


def test_style_lookup_is_case_and_whitespace_insensitive():
    assert coaching_style.get_system_prompt(" Aggressive ") == coaching_style.AGGRESSIVE_PROMPT
    assert coaching_style.get_system_prompt("SUPPORTIVE") == coaching_style.SUPPORTIVE_PROMPT


def test_unknown_style_falls_back_to_default():
    assert coaching_style.get_system_prompt("nonexistent_style") == coaching_style.COACHING_STYLES[
        coaching_style.DEFAULT_STYLE
    ]


def test_default_style_is_itself_a_registered_style():
    assert coaching_style.DEFAULT_STYLE in coaching_style.COACHING_STYLES


def test_list_styles_matches_the_registry_keys():
    assert sorted(coaching_style.list_styles()) == sorted(coaching_style.COACHING_STYLES.keys())


def test_every_style_embeds_the_shared_rules():
    # The shared rules (no apologies, no invented data, word limits) must
    # apply regardless of personality - each style prompt is built by
    # f-string interpolation of _SHARED_RULES, so this checks that
    # interpolation actually happened for every registered style rather than
    # silently dropping the shared block for one of them.
    for style_name in coaching_style.list_styles():
        prompt = coaching_style.get_system_prompt(style_name)
        assert "Never invent data that is not provided to you" in prompt
        assert "Never apologize" in prompt
