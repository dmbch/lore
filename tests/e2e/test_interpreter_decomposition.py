# pyright: reportPrivateUsage=false
"""Interpreter fitness fixtures: live-LLM baseline for the prompt-optimization loop.

Each test is one product claim about the Interpreter contract. Surfaces draw
only from held-out domains (medicine, economics/finance, law, geology,
linguistics, astronomy, culinary), disjoint from the prompt's few-shot teaching
domains, so a green measures transfer, not example recall. The consult date
T_NOW is likewise disjoint from every date in the prompt's few-shot examples,
so relative-date greens measure arithmetic, not example recall. The pre-rewrite
prompt baselined red on deictic grounding and relative-date resolution.

Measurement protocol: pass rate over repeated runs (k=5), not single-run green;
the stage is stochastic (Gemini 3 runs at default temperature; LiteLLM warns
against setting it lower).

Marked @pytest.mark.e2e, skipped without GEMINI_API_KEY (autouse fixture in
tests/e2e/conftest.py).
"""

from datetime import UTC, datetime

import pytest

from lore.domain import ConsultLoreRequest, InterpreterOutput
from lore.orchestrator import Orchestrator
from lore.orchestrator.interpret import interpret
from tests.e2e.conftest import judge

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]

T_NOW = int(datetime(2026, 11, 17, tzinfo=UTC).timestamp())


def _lines(propositions: list[str]) -> str:
    """One proposition per line for the judge.

    Criteria treat each line as one statement; an embedded newline would
    split a proposition into two bogus lines and false-fail the probe.
    """
    assert all("\n" not in p for p in propositions), "propositions must be single-line"
    return "\n".join(propositions)


async def _interpret(
    system: Orchestrator,
    *,
    question: str | None = None,
    hypothesis: str | None = None,
    context: str | None = None,
    reasoning: str | None = None,
) -> InterpreterOutput:
    """Run the Interpreter stage alone, through the production prompt-assembly path.

    The fixed confidence only satisfies request validation; the Interpreter never sees it.
    """
    request = ConsultLoreRequest(
        question=question,
        hypothesis=hypothesis,
        context=context,
        reasoning=reasoning,
        confidence=0.7 if hypothesis is not None else None,
    )
    return await interpret(
        providers=system._providers,
        request=request,
        settings=system._settings,
        t_now=T_NOW,
    )


async def test_atomic_hypothesis_yields_single_equivalent_proposition(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system, hypothesis="Metformin is the first-line therapy for type 2 diabetes."
    )

    assert len(out.propositions) == 1, out.propositions
    verdict = await judge(
        system,
        answer=out.propositions[0],
        criterion=(
            "The statement claims that metformin is the first-line therapy for "
            "type 2 diabetes, with no content added and none dropped."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_conjunction_yields_original_first_plus_two_atoms(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis="The euro fell 3% against the dollar and Brent crude rose above 90 dollars.",
    )

    assert len(out.propositions) == 3, out.propositions
    verdict = await judge(
        system,
        answer=out.propositions[0],
        criterion=(
            "The statement is the full composite claim, asserting both parts together: "
            "the euro fell 3% against the dollar, and Brent crude rose above 90 dollars. "
            "A statement carrying only one of the two parts fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_jargon_composite_normalizes_acronyms_and_decomposes(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "The ITT analysis showed a 12% drop in LDL, and the NNT for the "
            "primary endpoint was 45."
        ),
    )

    assert len(out.propositions) > 1, out.propositions
    verdict = await judge(
        system,
        answer=out.propositions[0],
        criterion=(
            "The statement is plain prose with the acronyms expanded: ITT as "
            "intention-to-treat, LDL as low-density lipoprotein, NNT as number "
            "needed to treat. Keeping the bare acronyms unexpanded fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_conditional_hypothesis_stays_single_proposition(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "If a language loses its case marking, its word order becomes more rigid "
            "within a few generations."
        ),
    )

    assert len(out.propositions) == 1, out.propositions


async def test_causal_chain_stays_single_proposition_with_link_intact(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "The hillside collapsed because sustained rainfall saturated the underlying clay layer."
        ),
    )

    assert len(out.propositions) == 1, out.propositions
    verdict = await judge(
        system,
        answer=out.propositions[0],
        criterion=(
            "The statement asserts a causal link: sustained rainfall saturating the "
            "underlying clay layer caused the hillside collapse. Stating the "
            "collapse and the saturation without the causal connection fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_comparison_hypothesis_stays_single_proposition(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis="Bench trials resolve faster than jury trials in civil contract cases.",
    )

    assert len(out.propositions) == 1, out.propositions


async def test_vague_hypothesis_without_context_stays_uninvented(
    system: Orchestrator,
) -> None:
    out = await _interpret(system, hypothesis="The patient got worse after the adjustment.")

    assert len(out.propositions) == 1, out.propositions
    verdict = await judge(
        system,
        answer=out.propositions[0],
        criterion=(
            "The statement says a patient got worse after an adjustment, and it "
            "names no specific patient, condition, medication, or adjustment. Any "
            "concrete detail not present in that vague claim fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_deictic_hypothesis_grounds_referent_from_context_and_reasoning(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis="we fixed it",
        context="investigating the ward's warfarin over-anticoagulation cases",
        reasoning=(
            "reducing the warfarin dose from 5mg to 2.5mg daily brought INR back "
            "into the 2 to 3 target range"
        ),
    )

    assert out.propositions, "hypothesis present, expected at least one proposition"
    verdict = await judge(
        system,
        answer=out.propositions[0],
        criterion=(
            "The statement stands alone without surrounding conversation and names "
            "the change concretely: reducing the warfarin dose (from 5mg to 2.5mg "
            "daily) brought INR back into the 2 to 3 target range or resolved the "
            "over-anticoagulation. A bare claim that 'we fixed it' "
            "without identifying the change fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_question_presupposition_stays_out_of_propositions(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        question="why did the rate hike crash the housing market?",
        hypothesis="Mortgage lending standards tightened in coastal metros before inland ones.",
    )

    assert out.propositions, "hypothesis present, expected at least one proposition"
    verdict = await judge(
        system,
        answer=_lines(out.propositions),
        criterion=(
            "None of the statements asserts that a rate hike crashed the housing "
            "market. Statements about mortgage lending standards tightening in "
            "coastal metros before inland ones are fine; the claim that the rate "
            "hike crashed the housing market appearing as a statement fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_colloquial_question_normalizes_preserving_intent(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        question=(
            "hey so umm what do we know about why the sourdough starters keep going flat lately?"
        ),
    )

    assert out.question is not None
    verdict = await judge(
        system,
        answer=out.question,
        criterion=(
            "The question is cleaned of filler and asks why sourdough starters "
            "keep going flat or losing activity. The intent is unchanged and no "
            "new constraints are added."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_question_only_yields_no_propositions(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system, question="What do we know about aquifer recharge rates in karst terrain?"
    )

    assert out.propositions == []


async def test_absolute_date_survives_in_proposition(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis="The 2025-09-04 rockfall was caused by freeze-thaw cycling of the cliff face.",
    )

    assert out.propositions, "hypothesis present, expected at least one proposition"
    verdict = await judge(
        system,
        answer=_lines(out.propositions),
        criterion=(
            "Every statement identifies the rockfall by the date September 4, 2025. "
            "The date may appear in any format, but a statement about an undated "
            "rockfall fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_relative_date_resolves_to_absolute_against_consult_date(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "we retargeted the wide-field survey telescope to the Perseus cluster two weeks ago"
        ),
    )

    assert out.propositions, "hypothesis present, expected at least one proposition"
    verdict = await judge(
        system,
        answer=out.propositions[0],
        criterion=(
            "The statement dates the telescope retargeting with an absolute "
            "calendar date or date range in late October or the first half of "
            "November 2026; any dates between 2026-10-26 and 2026-11-13 count. "
            "A statement that still says 'two weeks ago' or 'recently', or that "
            "carries no date at all, fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_keywords_contain_named_entities(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "Moody's downgraded Deutsche Bank two notches after the Bundesbank "
            "published adverse stress-test results."
        ),
    )

    lowered = [keyword.lower() for keyword in out.keywords]
    entities = ("moody", "deutsche bank", "bundesbank")
    found = {entity for entity in entities if any(entity in keyword for keyword in lowered)}
    assert len(found) >= 2, f"Expected named-entity keywords, got {out.keywords}"


async def test_multi_conjunct_compound_with_embedded_causal_splits_at_top_level(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "The trial enrolled 240 participants, the placebo arm had a 12% dropout "
            "rate, the treatment arm's LDL fell 18% because adherence exceeded 90%, "
            "and the safety board reported no serious adverse events."
        ),
    )

    assert len(out.propositions) == 5, out.propositions
    verdict = await judge(
        system,
        answer="\n".join(out.propositions[1:]),
        criterion=(
            "Exactly one of the statements asserts that the treatment arm's LDL "
            "(low-density lipoprotein) fell 18% because adherence exceeded 90%, "
            "keeping cause and effect in a single statement. The LDL fall and the "
            "adherence figure appearing only as separate unlinked statements fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_conjunct_atom_keeps_the_scoping_event_date(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "After the 2026-03-09 recall order, retail returns tripled because "
            "consumers feared contamination, and the insurer's claims backlog doubled."
        ),
    )

    assert len(out.propositions) == 3, out.propositions
    verdict = await judge(
        system,
        answer="\n".join(out.propositions[1:]),
        criterion=(
            "Both statements are anchored to the recall order of March 9, 2026, "
            "in any date format. In particular, the statement about the insurer's "
            "claims backlog doubling ties it to that recall order or its date "
            "rather than standing as a timeless claim. A backlog statement with "
            "no reference to the recall order or its date fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


_PARAGRAPH_HYPOTHESIS = (
    "The settlement came in well below our exposure estimate. Opposing "
    "counsel accepted it within a day of the revised offer. The arbitration "
    "clause was the deciding factor, since capping discovery costs removed "
    "their leverage."
)
_PARAGRAPH_CONTEXT = "post-mortem on the Hartwell v. Meridian Logistics contract dispute"
_PARAGRAPH_REASONING = (
    "the revised offer of 1.2 million settled a claim we had reserved 4 "
    "million against; the arbitration clause capped discovery costs on both sides"
)


async def test_paragraph_hypothesis_grounds_deixis_across_fields(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=_PARAGRAPH_HYPOTHESIS,
        context=_PARAGRAPH_CONTEXT,
        reasoning=_PARAGRAPH_REASONING,
    )

    assert out.propositions, "hypothesis present, expected at least one proposition"
    verdict = await judge(
        system,
        answer=_lines(out.propositions),
        criterion=(
            "The answer is a list of statements, one per line; judge each "
            "line as one unit. A multi-sentence line is a single statement, "
            "and a reference that resolves earlier in the same line is "
            "resolved. Each line, read alone, can be tied to the Hartwell v. "
            "Meridian Logistics contract dispute: no line leans on another "
            "line to resolve 'the settlement', 'it', or 'their'. The "
            "statements invent nothing: any figure or fact they carry appears "
            "in the source material (a 1.2 million revised offer, a 4 million "
            "reserve or exposure estimate, an arbitration clause capping "
            "discovery costs, acceptance within a day). Omitting some of "
            "those details does not fail. A line that cannot be tied to the "
            "dispute on its own, or that carries a fact outside that source "
            "material, fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_paragraph_hypothesis_preserves_all_claims_across_propositions(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=_PARAGRAPH_HYPOTHESIS,
        context=_PARAGRAPH_CONTEXT,
        reasoning=_PARAGRAPH_REASONING,
    )

    assert out.propositions, "hypothesis present, expected at least one proposition"
    verdict = await judge(
        system,
        answer=_lines(out.propositions),
        criterion=(
            "Taken together, the statements cover all three claims: the "
            "settlement came in well below the exposure estimate or reserve; "
            "opposing counsel accepted within a day of the revised offer; the "
            "arbitration clause was the deciding factor because capping discovery "
            "costs removed leverage. Coarser phrasings pass; a claim absent from "
            "every statement fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_overloaded_compound_stays_within_cap_without_dropping_claims(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "The kitchen audit found: the walk-in fridge ran at 6 degrees Celsius, "
            "two above spec; the freezer seals were cracked; the sourdough starter "
            "log had a five-day gap; the fryer oil exceeded 25% total polar "
            "materials; three cutting boards showed deep scoring; the dishwasher "
            "rinse cycle peaked at 71 degrees; the hood filters were last degreased "
            "in March; the dry store held six expired spice jars; the labels on "
            "prepped sauces omitted dates; the hand-wash station lacked soap; meat "
            "and fish shared a shelf; the thermometer calibration was overdue; the "
            "ice machine had visible scale; the allergen matrix was missing for two "
            "dishes; the waste bins were uncovered; the mop sink drained slowly; "
            "the glove boxes were empty; the delivery log skipped four days; a "
            "knife roll blocked a fire exit; and the pest-control report was unsigned."
        ),
    )

    assert out.propositions, "hypothesis present, expected at least one proposition"
    assert len(out.propositions) <= 16, out.propositions
    verdict = await judge(
        system,
        answer=_lines(out.propositions),
        criterion=(
            "Taken together, the statements represent every audit finding: the "
            "walk-in fridge at 6 degrees Celsius, two degrees above spec, cracked "
            "freezer seals, the "
            "five-day starter-log gap, fryer oil above 25% total polar materials, "
            "deeply scored cutting boards, the dishwasher rinse peaking at 71 "
            "degrees, hood filters last degreased in March, six expired spice "
            "jars, undated sauce labels, no soap at the hand-wash station, meat "
            "and fish sharing a shelf, overdue thermometer calibration, scale in "
            "the ice machine, the allergen matrix missing for two dishes, "
            "uncovered waste bins, the slow-draining mop sink, empty glove boxes, "
            "four skipped days in the delivery log, a knife roll blocking a fire "
            "exit, and an unsigned pest-control report. A finding folded into a "
            "coarser grouped statement passes; a finding absent from all "
            "statements fails."
        ),
    )
    assert verdict.passed, verdict.reasoning


async def test_axes_compose_on_hard_input(
    system: Orchestrator,
) -> None:
    out = await _interpret(
        system,
        hypothesis=(
            "after that rebalance last month, the fund's TER came in below the benchmark ETF's"
        ),
        context="reviewing the Alderbrook Global Equity Fund's fee competitiveness",
        reasoning=(
            "the rebalance moved assets out of the actively managed sleeve into "
            "passive index holdings, cutting ongoing fees"
        ),
    )

    assert out.propositions, "hypothesis present, expected at least one proposition"
    assert "last month" not in out.propositions[0].lower(), out.propositions[0]
    verdict = await judge(
        system,
        answer=out.propositions[0],
        criterion=(
            "The statement satisfies all four parts: (1) the acronyms are "
            "expanded, TER as total expense ratio and ETF as exchange-traded fund; "
            "(2) the rebalance is dated with an absolute month or date range "
            "within October 2026, not 'last month'; (3) the fund is named as the "
            "Alderbrook Global Equity Fund, with no unresolved 'the fund' or "
            "'that rebalance'; (4) the comparison stays whole: the fund's total "
            "expense ratio being below the benchmark's is one comparative claim, "
            "not separate absolute claims. Missing any one part fails."
        ),
    )
    assert verdict.passed, verdict.reasoning
