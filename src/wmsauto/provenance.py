"""Provenance-typed facts.

Implements §4.1 of docs/agent-memory-and-grounding.md: no assertion without
provenance. A value that came from somewhere is a `Fact`; a value that did not
is just a Python object and cannot pass any gate in `wmsauto.gates`.

The design intent is that facts are re-derived at point of use (§2), so a
`Fact` always records *when* it was read and stays capable of reporting its own
age. Staleness is a measured quantity, never an assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class SourceKind(Enum):
    """Where a value came from, ordered loosely by trust (§3).

    The kind — not the individual source — is what determines independence in
    `wmsauto.gates.two_source`. Reading the same WMS table twice yields two
    facts of the same kind and therefore does not constitute corroboration.
    """

    SYSTEM_OF_RECORD = "system_of_record"
    PHYSICAL_SCAN = "physical_scan"
    HUMAN_ATTESTATION = "human_attestation"
    DERIVED = "derived"
    EXTERNAL_SIGNAL = "external_signal"

    @property
    def can_ground_a_decision(self) -> bool:
        """External signal informs humans; it never grounds an action (§5.3 r1)."""
        return self is not SourceKind.EXTERNAL_SIGNAL


class MissingProvenance(Exception):
    """Raised when a claim would be emitted without a citation (§4.1)."""


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a fact came from and when it was read."""

    source_kind: SourceKind
    source_id: str
    record_id: str
    read_at: datetime

    def __post_init__(self) -> None:
        if not self.source_id or not self.record_id:
            raise MissingProvenance(
                "provenance requires both source_id and record_id; "
                "a fact that cannot be traced back to a record is not a fact"
            )
        if self.read_at.tzinfo is None:
            raise ValueError("read_at must be timezone-aware")

    def cite(self) -> str:
        """Human-readable citation, for the phrasing rule in §7."""
        return f"{self.source_id}#{self.record_id} as of {self.read_at:%Y-%m-%d %H:%M:%S %Z}"


@dataclass(frozen=True, slots=True)
class Fact(Generic[T]):
    """A value that carries its own provenance.

    Constructing one requires a `Provenance`; there is no bare-value path.
    """

    value: T
    provenance: Provenance

    def age(self, now: datetime) -> timedelta:
        return now - self.provenance.read_at

    def is_stale(self, now: datetime, max_age: timedelta) -> bool:
        return self.age(now) > max_age

    def state(self) -> str:
        """Certainty-preserving phrasing (§7): attribute, don't assert."""
        return f"{self.provenance.source_id} shows {self.value} ({self.provenance.cite()})"


def read(
    value: T,
    *,
    source_kind: SourceKind,
    source_id: str,
    record_id: str,
    read_at: datetime,
) -> Fact[T]:
    """Construct a fact at the moment of reading. The only sanctioned entry point."""
    return Fact(
        value=value,
        provenance=Provenance(
            source_kind=source_kind,
            source_id=source_id,
            record_id=record_id,
            read_at=read_at,
        ),
    )
