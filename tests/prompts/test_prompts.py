import os
from pathlib import Path
from unittest.mock import patch

from lore.config import LoreSettings, load_settings
from lore.prompts import PromptsConfig, build_core_prompt, build_system_prompt, load_prompt

_COMPLETE_TOML = Path(__file__).parents[1] / "fixtures" / "lore_complete.toml"


def _load_test_settings() -> LoreSettings:
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}, clear=True):
        return load_settings(toml_path=_COMPLETE_TOML)


def test_load_prompt_reads_bundled_scribe() -> None:
    settings = _load_test_settings()
    result = load_prompt(settings.prompts.scribe)
    assert len(result) > 0


def test_load_prompt_reads_bundled_consult() -> None:
    settings = _load_test_settings()
    result = load_prompt(settings.prompts.consult)
    assert len(result) > 0


def test_load_prompt_reads_custom_file(tmp_path: Path) -> None:
    override = tmp_path / "custom_scribe.md"
    override.write_text("Custom scribe prompt")
    result = load_prompt(override)
    assert result == "Custom scribe prompt"


def test_build_system_prompt_scribe_only() -> None:
    settings = _load_test_settings()
    result = build_system_prompt(settings.prompts)
    assert "Lore" in result
    assert result == load_prompt(settings.prompts.scribe)


def test_archivist_prompt_teaches_paraphrase_contributes_contradicts() -> None:
    settings = _load_test_settings()
    text = load_prompt(settings.prompts.archivist)
    assert "corroborates" in text
    assert "contributes" in text
    assert "contradicts" in text
    # Cross-resolution disjointness rule, with "pick most exact match" guidance.
    assert "most exact match" in text or "most specific match" in text
    assert "at most once" in text or "appears once" in text
    assert "notes" in text


def test_archivist_prompt_drops_legacy_vocabulary() -> None:
    settings = _load_test_settings()
    text = load_prompt(settings.prompts.archivist)
    assert "confirms" not in text
    assert "hypothesis_id" not in text
    assert "identity match" not in text.lower()


def test_scribe_prompt_teaches_disbelief_via_negative_confidence() -> None:
    settings = _load_test_settings()
    text = load_prompt(settings.prompts.scribe).lower()
    # Express disbelief via negative confidence on a positive-form hypothesis,
    # never as a textual negation.
    assert "negative confidence" in text
    assert "textual negation" in text or "do not negate" in text or "not a textual" in text


def test_build_system_prompt_with_narrative_and_glossary(tmp_path: Path) -> None:
    narrative = tmp_path / "narrative.md"
    narrative.write_text("Our domain narrative.")
    glossary = tmp_path / "glossary.md"
    glossary.write_text("Term definitions.")

    settings = _load_test_settings()
    config = PromptsConfig(
        narrative=narrative,
        glossary=glossary,
        scribe=settings.prompts.scribe,
        consult=settings.prompts.consult,
        interpreter=settings.prompts.interpreter,
        archivist=settings.prompts.archivist,
    )
    result = build_system_prompt(config)
    assert result.startswith("Our domain narrative.")
    assert "Term definitions." in result
    assert "Lore" in result
    parts = result.split("\n\n")
    assert parts[0] == "Our domain narrative."
    assert parts[1] == "Term definitions."


# ---------------------------------------------------------------------------
# build_core_prompt: parameterized base with optional domain includes
# ---------------------------------------------------------------------------


def test_build_core_prompt_prepends_narrative_then_glossary(tmp_path: Path) -> None:
    narrative = tmp_path / "narrative.md"
    narrative.write_text("Domain narrative.")
    glossary = tmp_path / "glossary.md"
    glossary.write_text("Term definitions.")
    base = tmp_path / "base.md"
    base.write_text("Base system prompt.")

    settings = _load_test_settings()
    config = PromptsConfig(
        narrative=narrative,
        glossary=glossary,
        scribe=settings.prompts.scribe,
        consult=settings.prompts.consult,
        interpreter=settings.prompts.interpreter,
        archivist=settings.prompts.archivist,
    )
    result = build_core_prompt(config, base=base)
    assert result == "Domain narrative.\n\nTerm definitions.\n\nBase system prompt."


def test_build_core_prompt_omits_absent_includes(tmp_path: Path) -> None:
    base = tmp_path / "base.md"
    base.write_text("Base system prompt.")

    settings = _load_test_settings()
    config = PromptsConfig(
        scribe=settings.prompts.scribe,
        consult=settings.prompts.consult,
        interpreter=settings.prompts.interpreter,
        archivist=settings.prompts.archivist,
    )
    result = build_core_prompt(config, base=base)
    assert result == load_prompt(base)


def test_build_core_prompt_with_glossary_only_skips_narrative(tmp_path: Path) -> None:
    glossary = tmp_path / "glossary.md"
    glossary.write_text("Term definitions.")
    base = tmp_path / "base.md"
    base.write_text("Base system prompt.")

    settings = _load_test_settings()
    config = PromptsConfig(
        glossary=glossary,
        scribe=settings.prompts.scribe,
        consult=settings.prompts.consult,
        interpreter=settings.prompts.interpreter,
        archivist=settings.prompts.archivist,
    )
    result = build_core_prompt(config, base=base)
    assert result == "Term definitions.\n\nBase system prompt."


# ---------------------------------------------------------------------------
# PromptsConfig: model construction
# ---------------------------------------------------------------------------


def test_prompts_config_requires_bundled_paths() -> None:
    pc = PromptsConfig(
        scribe=Path("/tmp/scribe.md"),
        consult=Path("/tmp/consult.md"),
        interpreter=Path("/tmp/interpreter.md"),
        archivist=Path("/tmp/archivist.md"),
    )
    assert pc.narrative is None
    assert pc.glossary is None
    assert pc.scribe == Path("/tmp/scribe.md")
    assert pc.consult == Path("/tmp/consult.md")
    assert pc.interpreter == Path("/tmp/interpreter.md")
    assert pc.archivist == Path("/tmp/archivist.md")


def test_prompts_config_accepts_narrative_and_glossary() -> None:
    pc = PromptsConfig(
        narrative=Path("/tmp/narrative.md"),
        glossary=Path("/tmp/glossary.md"),
        scribe=Path("/tmp/scribe.md"),
        consult=Path("/tmp/consult.md"),
        interpreter=Path("/tmp/interpreter.md"),
        archivist=Path("/tmp/archivist.md"),
    )
    assert pc.narrative == Path("/tmp/narrative.md")
    assert pc.glossary == Path("/tmp/glossary.md")
