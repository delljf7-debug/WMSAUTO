"""Procedural memory: SOPs as layer 4 (§3).

An SOP is a *how*, which makes it the correct thing to persist under §2. But a
corpus of uploaded documents imports two failure modes that must be closed
before it is safe to retrieve from:

**Retrieval always returns its best match.** Similarity search has no concept of
"nothing here applies." Asked about a situation no SOP covers, it returns the
nearest document and the answer reads exactly as confident as a correct one.
For a corpus documenting the modal path — which is what an SOP set is — every
off-path question lands in this hole. `ProcedureLibrary.lookup` therefore
enforces a relevance floor and abstains below it, and escalates rather than
guessing when two procedures score within a margin of each other.

**Documents go stale silently.** An SOP describing a screen that changed in the
last upgrade is wrong with full confidence. Procedures therefore carry an owner,
an effective date, a last-verified date, and a review interval; one past review
escalates for re-verification rather than being served as current.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Iterable, Sequence

from .decision import Abstain, AbstainReason, Act, Decision, Escalate

#: Below this relevance, no procedure is considered applicable.
DEFAULT_MIN_RELEVANCE = 0.30

#: Two procedures scoring within this margin are ambiguous; a human picks.
DEFAULT_AMBIGUITY_MARGIN = 0.08

_WORD_RE = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> frozenset[str]:
    return frozenset(_WORD_RE.findall(text.lower()))


def overlap_score(situation: str, procedure: "Procedure") -> float:
    """Default relevance: term overlap against the procedure's coverage.

    Deliberately simple and swappable — pass an embedding-backed scorer to
    `ProcedureLibrary` in production. The floor and ambiguity logic around it is
    what matters, and it is scorer-independent.
    """
    situation_terms = _terms(situation)
    if not situation_terms:
        return 0.0
    covered = procedure.coverage_terms()
    if not covered:
        return 0.0
    return len(situation_terms & covered) / len(situation_terms | covered)


Scorer = Callable[[str, "Procedure"], float]


@dataclass(frozen=True, slots=True)
class Procedure:
    """One SOP, with the lifecycle metadata layer 4 requires."""

    procedure_id: str
    title: str
    steps: tuple[str, ...]
    applies_to: frozenset[str]
    owner: str
    effective_from: datetime
    last_verified: datetime
    review_interval: timedelta = timedelta(days=180)
    does_not_cover: frozenset[str] = field(default=frozenset())
    superseded_by: str | None = None

    def coverage_terms(self) -> frozenset[str]:
        return frozenset().union(*(_terms(a) for a in self.applies_to)) if self.applies_to else frozenset()

    def is_effective(self, now: datetime) -> bool:
        return self.effective_from <= now and self.superseded_by is None

    def needs_reverification(self, now: datetime) -> bool:
        return now > self.last_verified + self.review_interval

    def explicitly_excludes(self, situation: str) -> bool:
        """An SOP that names its own limits is more useful than one that does not."""
        situation_terms = _terms(situation)
        return any(_terms(exclusion) <= situation_terms for exclusion in self.does_not_cover)

    def cite(self) -> str:
        return f"{self.procedure_id} ({self.title}), verified {self.last_verified:%Y-%m-%d}, owner {self.owner}"


@dataclass(frozen=True, slots=True)
class Match:
    procedure: Procedure
    relevance: float


class ProcedureLibrary:
    """Retrieval over procedural memory that can return nothing."""

    def __init__(
        self,
        procedures: Iterable[Procedure],
        *,
        scorer: Scorer = overlap_score,
        min_relevance: float = DEFAULT_MIN_RELEVANCE,
        ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
    ) -> None:
        self._procedures = tuple(procedures)
        self._scorer = scorer
        self._min_relevance = min_relevance
        self._ambiguity_margin = ambiguity_margin

    def __len__(self) -> int:
        return len(self._procedures)

    def rank(self, situation: str, *, now: datetime) -> tuple[Match, ...]:
        candidates = [
            Match(procedure=p, relevance=self._scorer(situation, p))
            for p in self._procedures
            if p.is_effective(now) and not p.explicitly_excludes(situation)
        ]
        return tuple(sorted(candidates, key=lambda m: m.relevance, reverse=True))

    def lookup(self, situation: str, *, now: datetime) -> Decision[Procedure]:
        """Return the applicable procedure, or decline to guess.

        This is the load-bearing method. Ordinary similarity search would return
        `ranked[0]` unconditionally; the value here is in the three paths that
        do not.
        """
        ranked = self.rank(situation, now=now)

        if not ranked or ranked[0].relevance < self._min_relevance:
            best = ranked[0].relevance if ranked else 0.0
            return Abstain(
                reason=AbstainReason.NO_APPLICABLE_PROCEDURE,
                detail=(
                    f"no procedure covers {situation!r} "
                    f"(best relevance {best:.2f} < floor {self._min_relevance:.2f})"
                ),
            )

        if len(ranked) > 1 and (ranked[0].relevance - ranked[1].relevance) < self._ambiguity_margin:
            return Escalate(
                detail=(
                    f"{situation!r} matches two procedures within "
                    f"{self._ambiguity_margin:.2f}: {ranked[0].procedure.procedure_id} and "
                    f"{ranked[1].procedure.procedure_id}; a human must choose"
                ),
                sources=(ranked[0].procedure.cite(), ranked[1].procedure.cite()),
            )

        best = ranked[0].procedure
        if best.needs_reverification(now):
            return Escalate(
                detail=(
                    f"{best.procedure_id} is past its review interval "
                    f"(last verified {best.last_verified:%Y-%m-%d}); "
                    "re-verify before following it"
                ),
                sources=(best.cite(),),
            )

        return Act(value=best, grounds=())

    def coverage(self, situations: Sequence[str], *, now: datetime) -> "CoverageReport":
        """What the corpus actually bought you.

        Run against a list of real situations from the event log. The uncovered
        set is the honest answer to "how much did 40 SOPs close the gap."
        """
        covered, uncovered, needs_human = [], [], []
        for situation in situations:
            decision = self.lookup(situation, now=now)
            if isinstance(decision, Act):
                covered.append(situation)
            elif isinstance(decision, Escalate):
                needs_human.append(situation)
            else:
                uncovered.append(situation)
        return CoverageReport(
            covered=tuple(covered),
            uncovered=tuple(uncovered),
            needs_human=tuple(needs_human),
        )


@dataclass(frozen=True, slots=True)
class CoverageReport:
    covered: tuple[str, ...]
    uncovered: tuple[str, ...]
    needs_human: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.covered) + len(self.uncovered) + len(self.needs_human)

    @property
    def covered_fraction(self) -> float:
        return len(self.covered) / self.total if self.total else 0.0

    def summary(self) -> str:
        return (
            f"{len(self.covered)}/{self.total} covered "
            f"({self.covered_fraction:.0%}), "
            f"{len(self.needs_human)} need a human, "
            f"{len(self.uncovered)} uncovered"
        )
