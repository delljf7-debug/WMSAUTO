"""Tests for gated command execution.

The claim under test: shell access does not create a path around the gates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wmsauto import Abstain, Act, EventLog, EventType, SourceKind, read, two_source
from wmsauto.execution import (
    CommandPolicy,
    CommandRefused,
    mutate_via_command,
    read_via_command,
)

NOW = datetime(2026, 8, 8, 14, 32, tzinfo=timezone.utc)
POLICY = CommandPolicy(allowed=frozenset({"echo", "false", "true"}))


@pytest.fixture
def log() -> EventLog:
    return EventLog()


def granted_authorization() -> Act:
    """A real gate pass: two independent, fresh, agreeing sources."""
    wms = read(12, source_kind=SourceKind.SYSTEM_OF_RECORD, source_id="wms", record_id="A-12-04", read_at=NOW)
    scan = read(12, source_kind=SourceKind.PHYSICAL_SCAN, source_id="rf-07", record_id="A-12-04", read_at=NOW)
    decision = two_source(wms, scan, now=NOW)
    assert isinstance(decision, Act)
    return decision


# Deny-by-default


def test_unlisted_program_is_refused(log):
    with pytest.raises(CommandRefused):
        read_via_command(["rm", "-rf", "/"], policy=POLICY, log=log, episode_id="ep-1", now=NOW)


def test_refusals_are_logged(log):
    with pytest.raises(CommandRefused):
        read_via_command(["curl", "http://example.invalid"], policy=POLICY, log=log, episode_id="ep-1", now=NOW)
    assert len(log.of_type(EventType.EXCEPTION_RAISED)) == 1


def test_allowlist_names_programs_not_command_strings():
    # Argument smuggling cannot widen the allowlist.
    assert POLICY.permits(["echo", "hello"])
    assert not POLICY.permits(["echo hello"])
    assert not POLICY.permits([])


# Reads produce facts


def test_successful_read_yields_a_fact_citing_the_command(log):
    decision = read_via_command(
        ["echo", "12"], policy=POLICY, log=log, episode_id="ep-1", now=NOW
    )
    assert isinstance(decision, Act)
    assert decision.value.strip() == "12"
    (fact,) = decision.grounds
    assert fact.provenance.record_id == "echo 12"
    assert fact.provenance.read_at == NOW


def test_failed_command_abstains_rather_than_returning_empty(log):
    decision = read_via_command(
        ["false"], policy=POLICY, log=log, episode_id="ep-1", now=NOW
    )
    assert isinstance(decision, Abstain)
    assert "exited 1" in decision.detail


def test_reads_are_logged(log):
    read_via_command(["echo", "ok"], policy=POLICY, log=log, episode_id="ep-1", now=NOW)
    assert log[0].payload["mode"] == "read"


# Mutations require a gate pass


def test_mutation_without_authorization_is_impossible(log):
    with pytest.raises(TypeError):
        mutate_via_command(  # type: ignore[call-arg]
            ["echo", "adjust"], policy=POLICY, log=log, episode_id="ep-1", now=NOW
        )


def test_mutation_rejects_an_abstention_as_authorization(log):
    refused = Abstain(reason=None, detail="no")  # type: ignore[arg-type]
    with pytest.raises(CommandRefused):
        mutate_via_command(
            ["echo", "adjust"],
            authorization=refused,  # type: ignore[arg-type]
            policy=POLICY,
            log=log,
            episode_id="ep-1",
            now=NOW,
        )


def test_mutation_rejects_a_gate_pass_carrying_no_grounds(log):
    with pytest.raises(CommandRefused):
        mutate_via_command(
            ["echo", "adjust"],
            authorization=Act(value=12, grounds=()),
            policy=POLICY,
            log=log,
            episode_id="ep-1",
            now=NOW,
        )


def test_authorized_mutation_runs_and_records_its_citations(log):
    result = mutate_via_command(
        ["echo", "adjust"],
        authorization=granted_authorization(),
        policy=POLICY,
        log=log,
        episode_id="ep-1",
        now=NOW,
    )
    assert result.succeeded
    issued = log.of_type(EventType.TASK_ISSUED)[0]
    assert len(issued.citations) == 2
    assert all("as of" in c for c in issued.citations)


def test_a_failed_gate_cannot_produce_an_authorization(log):
    """The end-to-end claim: disagreeing sources leave no way to mutate."""
    wms = read(12, source_kind=SourceKind.SYSTEM_OF_RECORD, source_id="wms", record_id="A-12-04", read_at=NOW)
    scan = read(7, source_kind=SourceKind.PHYSICAL_SCAN, source_id="rf-07", record_id="A-12-04", read_at=NOW)
    decision = two_source(wms, scan, now=NOW)
    assert not isinstance(decision, Act)
    with pytest.raises(CommandRefused):
        mutate_via_command(
            ["echo", "adjust"],
            authorization=decision,  # type: ignore[arg-type]
            policy=POLICY,
            log=log,
            episode_id="ep-1",
            now=NOW,
        )


def test_stale_evidence_cannot_authorize_a_mutation(log):
    old = read(12, source_kind=SourceKind.SYSTEM_OF_RECORD, source_id="wms", record_id="A-12-04", read_at=NOW - timedelta(hours=3))
    scan = read(12, source_kind=SourceKind.PHYSICAL_SCAN, source_id="rf-07", record_id="A-12-04", read_at=NOW)
    decision = two_source(old, scan, now=NOW)
    assert not isinstance(decision, Act)
    with pytest.raises(CommandRefused):
        mutate_via_command(
            ["echo", "adjust"],
            authorization=decision,  # type: ignore[arg-type]
            policy=POLICY,
            log=log,
            episode_id="ep-1",
            now=NOW,
        )
