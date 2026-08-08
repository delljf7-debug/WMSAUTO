"""The two-source gate for irreversible operations (§4.3).

Inventory adjustments, shipment release, and anything that moves atoms or money
must be corroborated by two *independent* derivations. Disagreement escalates to
a human; it does not resolve to the more recent value.

Independence is defined as differing `SourceKind`. Reading the WMS twice is one
source read twice, not two sources — that distinction is the whole point of the
gate, and the spec did not originally pin it down (see docs §4.3 note).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TypeVar

from .decision import Abstain, AbstainReason, Act, Decision, Escalate
from .provenance import Fact, SourceKind

T = TypeVar("T")

#: Default freshness requirement for an irreversible operation. Callers should
#: pass an explicit budget; inventory position ages in seconds (§2).
DEFAULT_MAX_AGE = timedelta(minutes=5)


def two_source(
    primary: Fact[T],
    corroborating: Fact[T],
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> Decision[T]:
    """Gate an irreversible operation on two independent, fresh, agreeing facts.

    Returns `Act` only when all four conditions hold. Every other path is an
    `Abstain` or `Escalate` with a reason that is countable in §9 metrics.
    """
    facts = (primary, corroborating)

    # §5.3 r1 — external signal may raise a question, never satisfy the gate.
    ungrounded = [f for f in facts if not f.provenance.source_kind.can_ground_a_decision]
    if ungrounded:
        return Abstain(
            reason=AbstainReason.UNGROUNDED_SOURCE,
            detail=(
                "external signal cannot ground an irreversible operation; "
                "route it for human review instead"
            ),
            grounds=facts,
        )

    # Two reads of the same kind are one source, not two.
    if primary.provenance.source_kind is corroborating.provenance.source_kind:
        return Abstain(
            reason=AbstainReason.NOT_INDEPENDENT,
            detail=(
                f"both facts are {primary.provenance.source_kind.value}; "
                "corroboration requires differing source kinds"
            ),
            grounds=facts,
        )

    stale = [f for f in facts if f.is_stale(now, max_age)]
    if stale:
        oldest = max(stale, key=lambda f: f.age(now))
        return Abstain(
            reason=AbstainReason.STALE_EVIDENCE,
            detail=(
                f"{oldest.provenance.cite()} is {oldest.age(now)} old, "
                f"budget is {max_age}"
            ),
            grounds=facts,
        )

    # Disagreement is a human's call. Do not prefer the fresher value.
    if primary.value != corroborating.value:
        return Escalate(
            detail=(
                f"sources disagree: {primary.provenance.cite()} reports "
                f"{primary.value!r}, {corroborating.provenance.cite()} reports "
                f"{corroborating.value!r}"
            ),
            grounds=facts,
        )

    return Act(value=primary.value, grounds=facts)


def single_source(
    fact: Fact[T],
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> Decision[T]:
    """Gate a *reversible* operation. Fresh and grounded, but corroboration optional."""
    if not fact.provenance.source_kind.can_ground_a_decision:
        return Abstain(
            reason=AbstainReason.UNGROUNDED_SOURCE,
            detail="external signal cannot ground an action",
            grounds=(fact,),
        )
    if fact.is_stale(now, max_age):
        return Abstain(
            reason=AbstainReason.STALE_EVIDENCE,
            detail=f"{fact.provenance.cite()} is {fact.age(now)} old, budget is {max_age}",
            grounds=(fact,),
        )
    return Act(value=fact.value, grounds=(fact,))


def requires_corroboration(source_kinds: frozenset[SourceKind]) -> bool:
    """True when the available evidence cannot satisfy an irreversible-op gate."""
    groundable = {k for k in source_kinds if k.can_ground_a_decision}
    return len(groundable) < 2
