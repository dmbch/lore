"""Validate stage — trust-boundary check on the Archivist's resolutions.

The epistemics absorb misclassified relationships (a paraphrase labelled novel,
an orthogonal claim labelled contradiction) via trust discounting, ECBF, and
decay. They cannot digest two failures: over-count (cost-DoS via spurious
novels) and hallucinated IDs (UUIDs the math has no way to ground). Both
fail-closed here, between reason and record.
"""

import structlog

from lore.domain import ArchivistOutput, ArchivistResolutionError

log = structlog.get_logger(__name__)


def validate_resolutions(
    *,
    reasoned: ArchivistOutput,
    proposition_count: int,
    retrieved_ids: frozenset[str],
) -> None:
    resolution_count = len(reasoned.resolutions)
    if resolution_count > proposition_count:
        log.debug(
            "consult.validate.resolution_overflow",
            resolution_count=resolution_count,
            proposition_count=proposition_count,
            reasoning=reasoned.reasoning,
        )
        msg = (
            f"archivist returned {resolution_count} resolutions for"
            f" {proposition_count} propositions"
            " — exceeds the one-resolution-per-proposition contract"
        )
        raise ArchivistResolutionError(msg)

    for resolution in reasoned.resolutions:
        if (
            corroborates := resolution.corroborates
        ) is not None and corroborates not in retrieved_ids:
            log.debug(
                "consult.validate.hallucinated_corroborates",
                claimed_id=corroborates,
                retrieved_ids=sorted(retrieved_ids),
                reasoning=reasoned.reasoning,
            )
            msg = (
                f"corroborates id {corroborates!r} not in retrieved set"
                " — the Archivist may have hallucinated the ID"
            )
            raise ArchivistResolutionError(msg)
        for h_id in resolution.contradicts:
            if h_id not in retrieved_ids:
                log.debug(
                    "consult.validate.hallucinated_contradicts",
                    claimed_id=h_id,
                    retrieved_ids=sorted(retrieved_ids),
                    reasoning=reasoned.reasoning,
                )
                msg = (
                    f"contradicts id {h_id!r} not in retrieved set"
                    " — the Archivist may have hallucinated the ID"
                )
                raise ArchivistResolutionError(msg)
