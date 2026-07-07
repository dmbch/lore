import os
from pathlib import Path
from unittest.mock import patch

from lore.config import LoreSettings, load_settings
from lore.prompts import PromptsConfig, build_core_prompt, load_prompt

_COMPLETE_TOML = Path(__file__).parents[1] / "fixtures" / "lore_complete.toml"


def _load_test_settings() -> LoreSettings:
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}, clear=True):
        return load_settings(toml_path=_COMPLETE_TOML)


def test_load_prompt_reads_bundled_scribe() -> None:
    settings = _load_test_settings()
    result = load_prompt(settings.prompts.scribe)
    assert len(result) > 0


def test_load_prompt_reads_custom_file(tmp_path: Path) -> None:
    override = tmp_path / "custom_scribe.md"
    override.write_text("Custom scribe prompt")
    result = load_prompt(override)
    assert result == "Custom scribe prompt"


def test_archivist_prompt_drops_legacy_vocabulary() -> None:
    settings = _load_test_settings()
    text = load_prompt(settings.prompts.archivist)
    assert "confirms" not in text
    assert "hypothesis_id" not in text
    assert "identity match" not in text.lower()


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
        interpreter=settings.prompts.interpreter,
        archivist=settings.prompts.archivist,
        contract=settings.prompts.contract,
    )
    result = build_core_prompt(config, base=base)
    assert result == "Domain narrative.\n\nTerm definitions.\n\nBase system prompt."


def test_build_core_prompt_omits_absent_includes(tmp_path: Path) -> None:
    base = tmp_path / "base.md"
    base.write_text("Base system prompt.")

    settings = _load_test_settings()
    config = PromptsConfig(
        scribe=settings.prompts.scribe,
        interpreter=settings.prompts.interpreter,
        archivist=settings.prompts.archivist,
        contract=settings.prompts.contract,
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
        interpreter=settings.prompts.interpreter,
        archivist=settings.prompts.archivist,
        contract=settings.prompts.contract,
    )
    result = build_core_prompt(config, base=base)
    assert result == "Term definitions.\n\nBase system prompt."


# ---------------------------------------------------------------------------
# PromptsConfig: model construction
# ---------------------------------------------------------------------------


def test_prompts_config_requires_bundled_paths() -> None:
    pc = PromptsConfig(
        scribe=Path("/tmp/scribe.md"),
        interpreter=Path("/tmp/interpreter.md"),
        archivist=Path("/tmp/archivist.md"),
        contract=Path("/tmp/contract.md"),
    )
    assert pc.narrative is None
    assert pc.glossary is None
    assert pc.scribe == Path("/tmp/scribe.md")
    assert pc.interpreter == Path("/tmp/interpreter.md")
    assert pc.archivist == Path("/tmp/archivist.md")


def test_prompts_config_accepts_narrative_and_glossary() -> None:
    pc = PromptsConfig(
        narrative=Path("/tmp/narrative.md"),
        glossary=Path("/tmp/glossary.md"),
        scribe=Path("/tmp/scribe.md"),
        interpreter=Path("/tmp/interpreter.md"),
        archivist=Path("/tmp/archivist.md"),
        contract=Path("/tmp/contract.md"),
    )
    assert pc.narrative == Path("/tmp/narrative.md")
    assert pc.glossary == Path("/tmp/glossary.md")
