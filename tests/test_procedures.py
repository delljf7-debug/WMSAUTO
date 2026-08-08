"""Tests for procedural memory retrieval.

The claim under test: a corpus of SOPs covering the modal path does not answer
off-path questions with the nearest document.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wmsauto.decision import Abstain, AbstainReason, Act, Escalate
from wmsauto.procedures import Procedure, ProcedureLibrary

NOW = datetime(2026, 8, 8, 14, 32, tzinfo=timezone.utc)


def sop(
    procedure_id: str,
    title: str,
    applies_to: set[str],
    *,
    verified_days_ago: int = 10,
    review_days: int = 180,
    does_not_cover: set[str] | None = None,
    superseded_by: str | None = None,
) -> Procedure:
    return Procedure(
        procedure_id=procedure_id,
        title=title,
        steps=("step one", "step two"),
        applies_to=frozenset(applies_to),
        owner="ops",
        effective_from=NOW - timedelta(days=365),
        last_verified=NOW - timedelta(days=verified_days_ago),
        review_interval=timedelta(days=review_days),
        does_not_cover=frozenset(does_not_cover or set()),
        superseded_by=superseded_by,
    )


@pytest.fixture
def library() -> ProcedureLibrary:
    return ProcedureLibrary(
        [
            sop("SOP-001", "Goods receipt against purchase order", {"goods receipt purchase order"}),
            sop("SOP-002", "Cycle count variance posting", {"cycle count variance posting"}),
            sop("SOP-014", "Outbound delivery packing", {"outbound delivery packing"}),
        ]
    )


# The headline behaviour


def test_off_path_situation_abstains_instead_of_returning_nearest_document(library):
    decision = library.lookup(
        "hazardous material spill containment on the dock", now=NOW
    )
    assert isinstance(decision, Abstain)
    assert decision.reason is AbstainReason.NO_APPLICABLE_PROCEDURE
    # It knows a nearest match exists and still declines to serve it.
    assert "best relevance" in decision.detail


def test_covered_situation_returns_the_procedure(library):
    decision = library.lookup("goods receipt purchase order", now=NOW)
    assert isinstance(decision, Act)
    assert decision.value.procedure_id == "SOP-001"


def test_ambiguous_match_escalates_rather_than_picking(library):
    ambiguous = ProcedureLibrary(
        [
            sop("SOP-A", "Stock transfer between plants", {"stock transfer posting"}),
            sop("SOP-B", "Stock transfer within plant", {"stock transfer posting"}),
        ]
    )
    decision = ambiguous.lookup("stock transfer posting", now=NOW)
    assert isinstance(decision, Escalate)
    assert "SOP-A" in decision.detail and "SOP-B" in decision.detail


# Lifecycle


def test_procedure_past_its_review_interval_escalates_for_reverification():
    stale = ProcedureLibrary(
        [sop("SOP-009", "Batch determination", {"batch determination"}, verified_days_ago=400)]
    )
    decision = stale.lookup("batch determination", now=NOW)
    assert isinstance(decision, Escalate)
    assert "past its review interval" in decision.detail


def test_superseded_procedures_are_never_returned():
    library = ProcedureLibrary(
        [sop("SOP-003", "Old putaway", {"putaway strategy"}, superseded_by="SOP-021")]
    )
    decision = library.lookup("putaway strategy", now=NOW)
    assert isinstance(decision, Abstain)


def test_procedure_not_yet_effective_is_not_returned():
    future = Procedure(
        procedure_id="SOP-030",
        title="New process",
        steps=("a",),
        applies_to=frozenset({"new process"}),
        owner="ops",
        effective_from=NOW + timedelta(days=30),
        last_verified=NOW,
    )
    library = ProcedureLibrary([future])
    assert isinstance(library.lookup("new process", now=NOW), Abstain)


def test_explicit_exclusions_are_respected():
    library = ProcedureLibrary(
        [
            sop(
                "SOP-005",
                "Cycle count",
                {"cycle count variance"},
                does_not_cover={"serialised stock"},
            )
        ]
    )
    assert isinstance(library.lookup("cycle count variance", now=NOW), Act)
    assert isinstance(
        library.lookup("cycle count variance serialised stock", now=NOW), Abstain
    )


# Honest measurement


def test_coverage_report_quantifies_what_the_corpus_bought(library):
    report = library.coverage(
        [
            "goods receipt purchase order",
            "outbound delivery packing",
            "hazardous material spill containment",
            "customs bonded warehouse transfer",
        ],
        now=NOW,
    )
    assert report.total == 4
    assert len(report.covered) == 2
    assert len(report.uncovered) == 2
    assert report.covered_fraction == 0.5
    assert "50%" in report.summary()


def test_empty_library_covers_nothing():
    report = ProcedureLibrary([]).coverage(["anything at all"], now=NOW)
    assert report.covered_fraction == 0.0
    assert len(report.uncovered) == 1
