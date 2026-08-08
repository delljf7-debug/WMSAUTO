"""Decision outcomes, including abstention as a first-class result (§4.4).

A decision function in this codebase never returns a bare value. It returns one
of `Act`, `Abstain`, or `Escalate`, so that "I don't know" is representable and
countable rather than being squeezed into a plausible-looking answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar, Union

from .provenance import Fact

T = TypeVar("T")


class AbstainReason(Enum):
    """Why no action was taken. Each is a distinct metric in §9."""

    STALE_EVIDENCE = "stale_evidence"
    CONFLICTING_SOURCES = "conflicting_sources"
    INSUFFICIENT_SOURCES = "insufficient_sources"
    NOT_INDEPENDENT = "not_independent"
    UNGROUNDED_SOURCE = "ungrounded_source"


@dataclass(frozen=True, slots=True)
class Act(Generic[T]):
    """Proceed. Carries the facts the action was grounded in."""

    value: T
    grounds: tuple[Fact, ...]

    def citations(self) -> list[str]:
        return [f.provenance.cite() for f in self.grounds]


@dataclass(frozen=True, slots=True)
class Abstain:
    """Do nothing and say so. Not a failure — an expected, logged outcome."""

    reason: AbstainReason
    detail: str
    grounds: tuple[Fact, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class Escalate:
    """Route to a human now. Distinct from Abstain: someone must decide."""

    detail: str
    grounds: tuple[Fact, ...] = field(default=())
    sources: tuple[str, ...] = field(default=())


Decision = Union[Act[T], Abstain, Escalate]


def is_action(decision: Decision) -> bool:
    return isinstance(decision, Act)
