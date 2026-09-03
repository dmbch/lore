# pyright: reportPrivateUsage=false
"""Archivist fitness: resolution promises, probed at the reason stage.

The product claims: First: an atom that decomposition made broader or vaguer
than its composite (an anchor the composite still holds) must not enter the
archive as a free-floating novel. The Archivist never lets it `contributes`; it
corroborates a retrieved claim its anchor-restored reading plainly matches, and
otherwise refuses it and records the drop in `notes`. The composite and the
well-grounded sibling still resolve, so the grounded facts survive. Second:
when the composite itself dropped an anchor the envelope holds (here in
`question`), the Interpreter failed wholesale and the write fails whole: no
resolutions at all, a note, and an answer instructing the oracle to restate.
Even a plain anchor-restored corroboration is refused, because the restated
consult carries the oracle's vote through the full pipeline; a salvage now
would land one opinion on the ledger twice. Third: a `contributes` string
stays within the consult inputs; the Archivist's only minting channel adds
no detail from its own knowledge (the reason-stage twin of the Interpreter's
no-invention probe). Fourth: a `contributes` keeps the input's surface
forms, expanding or contracting nothing. Fifth: a configured deployment
glossary supplies the canonical form for a term the input uses. Sixth:
expletives never enter a `contributes`; the claim's content words survive.

Stage-only probe: `reason()` is called directly with a synthetic
`InterpreterOutput`, mirroring how `test_interpreter_decomposition.py` calls
`interpret()`. Feeding the degraded atom in by hand keeps the Interpreter's
stochastic decomposition out of the loop, so a red isolates the Archivist.

The reservoir-inspection and apiary scenarios are held out: neither appears in
a few-shot example of either prompt (the prompts teach the anchoring rule on a
product recall), so a green measures transfer, not example recall.

Measurement protocol: judge a prompt change by pass rate at k >= 5 via
`mise run rate -- tests/e2e/test_archivist_resolution.py`, never a
single green; the stage is stochastic (Gemini 3 runs at default temperature;
LiteLLM warns against setting it lower).

Marked @pytest.mark.e2e, skipped without GEMINI_API_KEY (autouse fixture in
tests/e2e/conftest.py).
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from lore.config import LoreSettings
from lore.domain import ArchivistOutput, ConsultLoreRequest, InterpreterOutput, SearchResult
from lore.orchestrator import Orchestrator
from lore.orchestrator._reason import reason
from tests.e2e.conftest import judge

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

T_NOW = int(datetime(2026, 11, 17, tzinfo=UTC).timestamp())

COMPOSITE = (
    "The Aldrin Vale reservoir inspection was completed in March 2026, "
    "and the inspection found sediment buildup in the intake tunnels."
)
SIBLING_ATOM = "The Aldrin Vale reservoir inspection was completed in March 2026."
# Under-grounded: "the inspection" dropped the Aldrin Vale anchor the composite holds.
DEGRADED_ATOM = "The inspection found sediment buildup in the intake tunnels."

# Wholesale failure: the composite itself leans on "the inspection"; only the
# question names Aldrin Vale.
UNGROUNDED_COMPOSITE = (
    "The inspection was completed in March 2026, "
    "and the inspection found sediment buildup in the intake tunnels."
)
UNGROUNDED_SIBLING = "The inspection was completed in March 2026."

ANCHORED_STORED_CLAIM = SearchResult(
    id="H1",
    content=("The Aldrin Vale reservoir inspection found sediment buildup in the intake tunnels."),
    c_herd=0.72,
    oracle_count=4,
    last_attested=date(2026, 9, 3),
    score=0.9,
    proximity=0.88,
)


def _numbered(statements: list[str]) -> str:
    """One statement per numbered line, so the judge reads each on its own."""
    return "\n".join(f"{i}. {s}" for i, s in enumerate(statements, 1))


async def _reason(
    system: Orchestrator,
    *,
    hypothesis: str,
    propositions: list[str],
    question: str | None = None,
    retrieved: list[SearchResult] | None = None,
    settings: LoreSettings | None = None,
) -> ArchivistOutput:
    """Run the Archivist stage alone on a synthetic Interpreter output.

    Feeding `propositions` directly lets a deliberately-degraded atom reach the
    Archivist without the Interpreter's stochastic decomposition in the loop.
    The fixed confidence only satisfies request validation; the Archivist never sees it.
    """
    request = ConsultLoreRequest(question=question, hypothesis=hypothesis, confidence=0.7)
    interpreted = InterpreterOutput(propositions=propositions)
    return await reason(
        providers=system._providers,
        request=request,
        interpreted=interpreted,
        enriched=retrieved or [],
        settings=settings or system._settings,
        t_now=T_NOW,
    )


async def test_undergrounded_atom_is_refused_and_noted(
    system: Orchestrator,
) -> None:
    out = await _reason(
        system,
        hypothesis=COMPOSITE,
        propositions=[COMPOSITE, SIBLING_ATOM, DEGRADED_ATOM],
    )

    contributed = [r.contributes for r in out.resolutions if r.contributes is not None]

    # The refusal is recorded on the observability surface, naming the dropped atom.
    assert out.notes, f"Expected a note for the refused atom, got none. reasoning:\n{out.reasoning}"
    noted = await judge(
        system,
        answer="\n".join(out.notes),
        criterion=(
            "The notes record that a statement about sediment buildup in the intake "
            "tunnels was refused, dropped, or not stored because it no longer says "
            "which inspection it belongs to (the Aldrin Vale reservoir inspection)."
        ),
    )
    assert noted.passed, noted.reasoning

    # No degraded copy entered the archive: no stored novel refers to the sediment
    # finding without identifying which inspection produced it.
    uncorrupted = await judge(
        system,
        answer=_numbered(contributed),
        criterion=(
            "Read each numbered statement on its own. None of them reports sediment "
            "buildup in intake tunnels without identifying the inspection as the "
            "Aldrin Vale reservoir inspection. A standalone statement like 'the "
            "inspection found sediment buildup in the intake tunnels', with no "
            "indication of which inspection, fails."
        ),
    )
    assert uncorrupted.passed, uncorrupted.reasoning

    # The well-grounded sibling resolves on its own: refusing the degraded atom
    # must not collapse decomposition into composite-only storage.
    sibling = await judge(
        system,
        answer=_numbered(contributed),
        criterion=(
            "At least one numbered statement, read on its own, states that the "
            "Aldrin Vale reservoir inspection was completed in March 2026 and makes "
            "no claim about sediment."
        ),
    )
    assert sibling.passed, sibling.reasoning

    # Both grounded facts survive, tied to the inspection.
    survived = await judge(
        system,
        answer=_numbered(contributed),
        criterion=(
            "Taken together, the statements record that the Aldrin Vale reservoir "
            "inspection was completed in March 2026 and found sediment buildup in "
            "the intake tunnels. A coarser phrasing passes as long as both facts, "
            "tied to the Aldrin Vale reservoir inspection, are present."
        ),
    )
    assert survived.passed, survived.reasoning


async def test_undergrounded_atom_corroborates_its_anchored_match(
    system: Orchestrator,
) -> None:
    """The refusal governs `contributes` only: a degraded atom whose anchor-restored
    reading plainly matches a stored claim still corroborates it. Corroboration
    creates no node, so the mature claim keeps accruing attestations."""
    out = await _reason(
        system,
        hypothesis=COMPOSITE,
        propositions=[COMPOSITE, SIBLING_ATOM, DEGRADED_ATOM],
        retrieved=[ANCHORED_STORED_CLAIM],
    )

    corroborated = [r.corroborates for r in out.resolutions if r.corroborates is not None]
    assert "H1" in corroborated, (
        f"Expected the degraded atom's anchored match H1 to be corroborated. "
        f"resolutions: {out.resolutions!r}\nreasoning:\n{out.reasoning}"
    )

    contributed = [r.contributes for r in out.resolutions if r.contributes is not None]
    uncorrupted = await judge(
        system,
        answer=_numbered(contributed),
        criterion=(
            "Read each numbered statement on its own. None of them reports sediment "
            "buildup in intake tunnels without identifying the inspection as the "
            "Aldrin Vale reservoir inspection. An empty list passes."
        ),
    )
    assert uncorrupted.passed, uncorrupted.reasoning


async def test_ungrounded_composite_fails_the_write_whole(
    system: Orchestrator,
) -> None:
    """When the composite itself dropped the anchor, held here only by the
    question, the Interpreter failed wholesale and the write fails whole: no
    resolutions at all, even though the degraded atom's restored reading
    plainly matches a stored claim. The answer requests a restatement, and the
    restated consult carries the oracle's vote; a salvaged corroboration now
    would land the same opinion on the ledger twice."""
    out = await _reason(
        system,
        question="How did the Aldrin Vale reservoir inspection go?",
        hypothesis=(
            "the inspection was completed in March 2026, and it found sediment "
            "buildup in the intake tunnels"
        ),
        propositions=[UNGROUNDED_COMPOSITE, UNGROUNDED_SIBLING, DEGRADED_ATOM],
        retrieved=[ANCHORED_STORED_CLAIM],
    )

    assert not out.resolutions, (
        f"Expected a whole-write failure with no resolutions. "
        f"resolutions: {out.resolutions!r}\nreasoning:\n{out.reasoning}"
    )

    assert out.notes, f"Expected a note for the failed write, got none. reasoning:\n{out.reasoning}"
    noted = await judge(
        system,
        answer="\n".join(out.notes),
        criterion=(
            "The notes record that the submitted statements fail to say which "
            "inspection they are about (the Aldrin Vale reservoir inspection) "
            "and that the write failed as a result. Any phrasing of the "
            "failure counts: the write fails wholesale, nothing was stored, "
            "the claims were refused."
        ),
    )
    assert noted.passed, noted.reasoning

    instructed = await judge(
        system,
        answer=out.answer,
        criterion=(
            "The answer tells the oracle that the submitted claim was not stored "
            "or recorded, and asks for it to be restated naming which inspection "
            "it concerns (the Aldrin Vale reservoir inspection). Additional "
            "content answering the question is fine."
        ),
    )
    assert instructed.passed, instructed.reasoning


async def test_contributes_stays_within_the_consult_inputs(
    system: Orchestrator,
) -> None:
    """The `contributes` channel mints nothing: every concrete detail in the
    stored string comes from the consult inputs, never from the Archivist's
    own knowledge. Reason-stage twin of the Interpreter probe
    `test_vague_hypothesis_without_context_stays_uninvented`: the same
    no-invention promise, pinned at the other minting channel. A regression
    probe: green on write, its value is catching future prompt drift."""
    novel = (
        "Hive seven at the Marrow Lane apiary swarmed on 2026-05-19 "
        "after the colony raised nine queen cells."
    )
    unrelated = SearchResult(
        id="H9",
        content="The Grover Street tram depot switched its night shift to LED lighting in 2024.",
        c_herd=0.4,
        oracle_count=2,
        last_attested=date(2026, 1, 12),
        score=0.31,
        proximity=0.12,
    )
    out = await _reason(
        system,
        hypothesis=novel,
        propositions=[novel],
        retrieved=[unrelated],
    )

    corroborated = [r.corroborates for r in out.resolutions if r.corroborates is not None]
    assert not corroborated, (
        f"Expected no corroboration against an unrelated neighborhood. "
        f"resolutions: {out.resolutions!r}\nreasoning:\n{out.reasoning}"
    )
    contradicted = [r.contradicts for r in out.resolutions if r.contradicts]
    assert not contradicted, (
        f"Expected no disbelief onto the unrelated neighborhood. "
        f"resolutions: {out.resolutions!r}\nreasoning:\n{out.reasoning}"
    )
    contributed = [r.contributes for r in out.resolutions if r.contributes is not None]
    assert len(contributed) == 1, (
        f"Expected the novel hypothesis to contribute exactly once. "
        f"resolutions: {out.resolutions!r}\nreasoning:\n{out.reasoning}"
    )

    grounded = await judge(
        system,
        answer=contributed[0],
        criterion=(
            "The statement reports at minimum that hive seven swarmed, and "
            "every concrete detail it carries appears in this source "
            "material: hive seven, the Marrow Lane apiary, a swarm on "
            "May 19 2026 in any date format, and the colony raising nine "
            "queen cells beforehand (a causal phrasing of that sequence "
            "passes). Rewording passes; beyond that minimum, omitting "
            "listed details passes. Any concrete detail from outside the "
            "source material "
            "fails: an added cause such as overcrowding, a bee species or "
            "breed, weather or season, an outcome of the swarm, or any name, "
            "number, date, or place not in the list."
        ),
    )
    assert grounded.passed, grounded.reasoning


async def test_novel_contribution_keeps_the_inputs_surface_forms(
    system: Orchestrator,
) -> None:
    """The minted statement preserves the input's surface forms: HTTP and TLS
    stay as the practitioner wrote them, neither expanded nor contracted.
    Pins the golden-archive drift row that stored both expanded. String
    asserts only, no judge."""
    novel = "The HTTP service authenticates inbound requests with mutual TLS."
    out = await _reason(
        system,
        hypothesis=novel,
        propositions=[novel],
    )

    contributed = [r.contributes for r in out.resolutions if r.contributes is not None]
    assert contributed, (
        f"Expected the novel hypothesis to contribute. "
        f"resolutions: {out.resolutions!r}\nreasoning:\n{out.reasoning}"
    )

    for minted in contributed:
        lowered = minted.lower()
        assert "HTTP" in minted, (
            f"Expected the surface form HTTP in the stored statement. contributes: {minted!r}"
        )
        assert "TLS" in minted, (
            f"Expected the surface form TLS in the stored statement. contributes: {minted!r}"
        )
        assert "hypertext transfer protocol" not in lowered, (
            f"Expected HTTP left unexpanded. contributes: {minted!r}"
        )
        assert "transport layer security" not in lowered, (
            f"Expected TLS left unexpanded. contributes: {minted!r}"
        )


async def test_glossary_supplies_the_canonical_form(
    system: Orchestrator,
    tmp_path: Path,
) -> None:
    """The deployment glossary supplies the canonical form for a term the
    input uses: the glossary names PostgreSQL canonical, so the stored
    statement writes PostgreSQL where the input wrote Postgres. Preservation
    alone would keep Postgres. String asserts only, no judge."""
    glossary = tmp_path / "glossary.md"
    glossary.write_text('PostgreSQL is the canonical name; the input may write it as "Postgres".')
    prompts = system._settings.prompts.model_copy(update={"glossary": glossary})
    settings = system._settings.model_copy(update={"prompts": prompts})

    novel = "The team migrated the billing database to Postgres 17."
    out = await _reason(
        system,
        hypothesis=novel,
        propositions=[novel],
        settings=settings,
    )

    contributed = [r.contributes for r in out.resolutions if r.contributes is not None]
    assert contributed, (
        f"Expected the novel hypothesis to contribute. "
        f"resolutions: {out.resolutions!r}\nreasoning:\n{out.reasoning}"
    )

    for minted in contributed:
        assert "PostgreSQL" in minted, (
            f"Expected the glossary's canonical form PostgreSQL in the stored "
            f"statement. contributes: {minted!r}"
        )


async def test_novel_contribution_drops_expletives_but_keeps_the_claim(
    system: Orchestrator,
) -> None:
    """The register floor drops the expletive while the claim content
    survives: the intensity it carried lives in the oracle's confidence
    scalar, not the ledger. String asserts only, no judge."""
    novel = "The staging cluster is fucking broken after the 2026-08-25 kernel patch."
    out = await _reason(
        system,
        hypothesis=novel,
        propositions=[novel],
    )

    contributed = [r.contributes for r in out.resolutions if r.contributes is not None]
    assert contributed, (
        f"Expected the novel hypothesis to contribute. "
        f"resolutions: {out.resolutions!r}\nreasoning:\n{out.reasoning}"
    )

    for minted in contributed:
        lowered = minted.lower()
        assert "fucking" not in lowered, (
            f"Expected the expletive dropped from the stored statement. contributes: {minted!r}"
        )
        assert "staging cluster" in lowered, (
            f"Expected the claim's subject in the stored statement. contributes: {minted!r}"
        )
        assert "2026-08-25" in lowered, (
            f"Expected the patch date in the stored statement. contributes: {minted!r}"
        )
        assert "kernel patch" in lowered, (
            f"Expected the kernel patch in the stored statement. contributes: {minted!r}"
        )
