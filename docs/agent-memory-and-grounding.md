# Agent Memory & Grounding Architecture

**Status:** Design proposal
**Scope:** How a long-running (24/7) agent operating inside WMSAUTO handles memory and
context such that it does not produce confident wrong data or issue wrong tasks to humans.

---

## 1. The problem is not memory size

"Memory/context issues" and "confidently wrong output" look like two problems. They are one.

An agent whose context has degraded **does not know that it degraded**. Model confidence is
generated from the fluency of its own reasoning, not from the completeness of its inputs.
Context loss is therefore silent, and:

```
silent context loss + fluent generation = confident wrong output
```

This is why the reflexive fix — a bigger memory, a vector store, more retrieval of the
agent's own history — makes the failure worse rather than better. A larger memory of the
agent's own prior statements produces an agent that is more *consistently* wrong, and more
persuasive about it.

In a warehouse the blast radius is physical. A wrong pick task, a wrong putaway location, a
wrong cycle-count adjustment, or a wrong replenishment is executed by a person. Once the
inventory record drifts from physical reality, every downstream decision is poisoned, and
the agent cannot detect the drift because the record *is* its only view of reality.

## 2. Organizing principle

> **Memory holds *how*, not *what*.**
>
> Procedures, site quirks, and pointers persist.
> Facts do not — they are re-derived from the system of record at the moment of use.

Corollary: **facts have TTLs, procedures do not.** Inventory position is true for seconds.
A putaway rule is true for months. The moment those two live in the same store under the
same trust level, the system will confidently assert stale inventory as current.

## 3. State layers

Five layers with deliberately unequal trust. Nothing is promoted between layers implicitly.

| Layer | Contents | Lifetime | Trust |
|---|---|---|---|
| **System of record** (WMS DB) | inventory, locations, orders, tasks | authoritative | Only source of fact. Never cached across a decision. |
| **Working context** (the turn) | current reasoning, retrieved rows | ephemeral | Assume lossy and summarizable. No consequential fact lives *only* here. |
| **Episodic log** (append-only) | `task_issued`, `task_confirmed`, `exception_raised`, `human_override` — with IDs | permanent | Audit and reconstruction. Not a reasoning shortcut. |
| **Procedural memory** | learned rules, site quirks, escalation contacts | slow, versioned, expiring | Human-reviewed before promotion. |
| **External signal** (§5) | carrier, weather, customs, recall, regulatory events | short TTL, per-signal | **Lowest trust.** May raise flags and questions. May never author a task or mutate state. |

Reads from the system of record are timestamped and version-stamped (`read_at`, etag/rowver)
so that staleness is a measurable quantity rather than an assumption.

## 4. Making confidence structural, not introspective

A model cannot be prompted into calibration. Do not rely on it reporting its own
uncertainty. Enforce these in code instead.

### 4.1 No assertion without provenance

Every fact the agent emits carries a provenance triple:

```
(source, record_id, read_at)
```

A claim with no provenance token cannot be serialized into output. This is a type-system
constraint at the boundary, not a prompt instruction — prompt instructions degrade under
context pressure, types do not.

### 4.2 Every instruction must be checkable by physical reality

Tasks issued to humans are phrased so that executing them verifies them. If the agent
says *"pick 12 of SKU-A from A-12-04"* and the scan at A-12-04 returns SKU-B, the error is
caught by the world, not by the model. Scan-to-confirm must not be bypassable.

### 4.3 Two-source gate on irreversible operations

Inventory adjustments, shipment release, and anything that moves atoms or money requires
agreement between two independent derivations (e.g. WMS record plus a recent physical scan
event). Disagreement escalates to a human; it does not resolve to the more recent value.

### 4.4 Abstention is a first-class output

`INSUFFICIENT_EVIDENCE` is a valid, expected, logged, and unpenalized result with a defined
handoff path. Without a modeled abstention, the agent will always produce *something*.

Monitor the abstention rate. A rate of exactly zero across a week is an alarm, not a
success.

---

## 5. External signal retrieval (Groq compound)

The system of record answers *"what is in the building."* It has no row for *"the carrier
just suspended service"* or *"the port is closed."* That class of fact is real, it changes
warehouse decisions, and no amount of internal grounding will surface it. This layer fills
that gap — and must be contained, because an open-web retrieval is the lowest-trust input
in the entire system.

### 5.1 Verified integration surface

Confirmed against Groq's docs (August 2026):

| Item | Value |
|---|---|
| Models | `groq/compound` (multiple tool calls per request), `groq/compound-mini` (single tool call, ~3× lower latency) |
| Built-in tools | Web Search (Tavily-backed), Visit Website, Code Execution, Wolfram Alpha |
| Tool restriction | `compound_custom.tools.enabled_tools` |
| Domain scoping | `search_settings.include_domains` / `exclude_domains` (wildcards supported), `search_settings.country` |
| Source attribution | `message.executed_tools[].search_results[]` → `{ title, url, content, score }` |
| Versioning | defaults to `2025-08-16`; `latest` selectable via the `Groq-Model-Version` header |

Custom user-provided tools are not supported.

The `search_results` array is the load-bearing detail. Because every retrieval returns
source URLs and relevance scores, web-derived signals **can** satisfy the §4.1 provenance
rule. Had it returned only synthesized prose, this layer would have to be rejected outright.

### 5.2 Trigger, not "always"

The stated intent is that current data is never missed when it matters. Firing a search on
*every* turn is the wrong way to get there:

- **Latency.** Warehouse task issuance is measured in seconds; an unconditional round trip
  breaks throughput on decisions that have no external dependency at all.
- **Injection surface.** Every unnecessary retrieval is another opportunity to pull
  adversarial or simply wrong text into context. Retrieval volume *is* attack surface.
- **Noise.** Most WMS decisions — pick this, put that, replenish here — depend on nothing
  outside the building. Retrieval adds risk with no upside.

So: **always evaluate the trigger; fire when it matches.** The trigger predicate is

> Does this decision depend on world state the system of record structurally cannot
> represent, and would a change in that state change the action?

Both clauses must hold. Standing triggers for WMSAUTO:

| Trigger | Affects |
|---|---|
| Carrier service disruption / suspension | outbound staging, ship-method selection |
| Severe weather on a lane or at the site | inbound scheduling, dock labor |
| Port, customs, or border closure | inbound ETA, cross-dock planning |
| Supplier recall notice | quarantine, hold, pick suppression |
| Regulatory / hazmat / compliance change | putaway constraints, documentation |
| Holiday or statutory schedule change | dock booking, labor planning |

If the trigger should instead be unconditional, that is a one-line change to the predicate —
but it should be a deliberate decision made against the three costs above, not a default.

### 5.3 Containment rules

These are the rules that keep a low-trust source from contaminating a grounded system.

1. **Signal, never fact.** A retrieval result may raise a flag, open a question, or request
   a human review. It may **never** author a task, mutate inventory, or satisfy the
   two-source gate in §4.3. External signal is not one of the two sources.
2. **Retrieved content is data, never instruction.** Fetched text is quarantined and
   labelled as untrusted external content. Text arriving from a retrieval that reads as a
   directive is logged as a possible injection attempt and discarded, never followed.
3. **Provenance is mandatory.** Persist `url`, `score`, and `retrieved_at` from
   `executed_tools[].search_results[]` into the episodic log for every signal acted on. A
   signal that cannot cite a URL does not exist.
4. **Tiered domain allowlist.** Use `include_domains` to scope retrieval to authoritative
   sources — carrier status pages, national weather services, port authorities, customs and
   regulatory bodies. An allowlisted domain set is what raises open-web search from
   unusable to merely low-trust. Open-web fallback, if enabled at all, is flagged at a
   distinctly lower tier.
5. **Restrict enabled tools.** Web Search and Visit Website only. Code Execution and
   Wolfram Alpha are not needed for this layer and are disabled via `enabled_tools`.
6. **Pin the version.** Run the dated version (`2025-08-16`), not `latest`. A 24/7 agent
   whose retrieval behaviour silently changes under it is precisely the drift failure §6.1
   exists to prevent. Version bumps are reviewed and deployed deliberately.
7. **Short TTL, explicit expiry.** A carrier disruption signal is true for hours, not days.
   Every signal carries an expiry; expired signals are dropped, not re-asserted.

### 5.4 Escalation path

External signal enters the human loop rather than the task loop:

```
trigger fires → compound retrieval → provenance captured → signal raised with sources
   → human reviews → human decides → decision written to SoR → agent re-derives from SoR
```

The agent's behaviour changes only after a human has acted on the signal and that action
has landed in the system of record. This keeps the authoritative path single-threaded even
though the information came from outside.

---

## 6. What 24/7 operation adds

Continuous runtime introduces failure modes that a one-shot invocation does not have.

### 6.1 Drift — bounded episodes

The agent does not run "forever." It runs bounded **episodes** (a shift, an hour, a task
batch). Each episode re-derives its world from the system of record on start. Nothing
crosses an episode boundary except the durable event log and reviewed procedural memory.

Deliberate amnesia at the boundary is a feature. It is the only reliable way to stop
assumption accumulation.

### 6.2 Compaction lies

When a long context is summarized, the summary is a lossy, model-authored artifact that
subsequently reads as authoritative source material.

Mitigations:
- Summaries are explicitly marked as summaries, never as facts.
- Consequential identifiers are carried forward as structured fields, not prose.
- After any compaction, re-read the system of record for anything about to be acted on.

### 6.3 Staleness and clock state

Every episode begins by re-establishing current time, current shift, open exceptions, and
the freshness of the data it is about to use. This is cheap; run it unconditionally.

### 6.4 External watchdog

A long-running agent cannot observe its own degradation. A separate process monitors
liveness *and* sanity: output rate, abstention rate, and spot-checks of recent assertions
against the system of record.

## 7. Operating inside a human environment

- **Trust decay is the primary human hazard.** If the agent is right 500 times, nobody
  checks the 501st. Verification must not erode with demonstrated accuracy: mandatory scan
  confirmation, rotating spot-checks, sampled audits.
- **Dissent halts the batch, not just the task.** A worker reporting "this doesn't match
  what I see" is the highest-value signal in the system and must be routed as such.
- **Never issue a task a human cannot safely refuse.** Every instruction states what it is
  based on and offers a one-tap mismatch path.
- **Phrasing carries certainty.** "WMS shows 12 units at A-12-04 as of 14:32" survives
  being wrong. "There are 12 units at A-12-04" destroys trust when it is wrong.

## 8. Build order

1. Provenance-typed fact objects and the no-assertion-without-citation boundary.
2. Append-only event log with stable IDs.
3. Episode boundaries and re-derivation on episode start.
4. Abstention path and escalation routing.
5. Two-source gate on irreversible operations.
6. External watchdog and metrics.
7. External signal layer (§5) — after the containment rules have something to attach to.
8. Procedural memory, behind a human review gate.

The "memory system" is deliberately last and is the smallest component. External retrieval
is deliberately late: it is only safe once provenance typing and the event log exist to
contain it.

## 9. Metrics that detect confident wrongness

| Metric | Why it matters |
|---|---|
| Human override rate (and trend) | Direct measure of disagreement with reality |
| Contradiction rate | Agent assertion vs. the next physical scan |
| Staleness at decision time (p50/p99) | Age of the data a decision was actually based on |
| Abstention rate | Must be nonzero and stable |
| Signal precision | Fraction of raised external signals a human judged actionable |
| Retrieval rate | Trigger firing frequency; a spike means the predicate is miscalibrated |
| **Time-to-detection** of a bad assertion | The one that matters most — not all errors are preventable |

## 10. Why this generalizes past coding

A coding agent is unusually safe, and not because the model is better at code. It is safe
because that domain hands it a **free, fast, automated reality oracle**: compile, test, run,
lint. A wrong answer is detected in seconds, by a machine, at no cost, before it reaches a
human.

Every other domain removes that oracle. There is no `pytest` for *"is this the right
aisle,"* *"is this invoice correct,"* or *"should this shipment go out."* The model has not
gotten worse — the error detector is simply gone, and confident wrongness stops being caught.

So generalizing an agent past coding is not a capability problem. It is the problem of
**manufacturing a replacement oracle**, in descending order of quality:

| Substitute | Cost | Automatic? |
|---|---|---|
| Physical / transactional confirmation (the scan) | near zero | yes |
| Two-source agreement (§4.3) | low | yes |
| Human confirmation | high, and erodes (§7) | no |

The layers in this document are that oracle. The system-of-record abstraction is what ports
between domains — repo and test suite for code, WMS and physical scan for the warehouse,
ledger for finance — while §5 covers the axis no internal record can reach. What must be
re-derived per domain is the question *"what plays the role of the compiler here?"*

Where the honest answer is *"nothing does,"* that is not a gap to paper over with a larger
memory. It is the precise point at which abstention (§4.4) and human confirmation are the
only correct outputs.

## 11. Prior art

Nothing in this document is invented. It is an assembly of established patterns applied to
one domain, and it is worth being explicit about that: the names below are the correct
search terms for the literature, and the correct vocabulary when hiring for this work.

| Section | Established name | Origin |
|---|---|---|
| §3 episodic log | Event sourcing | Formalized by Fowler c. 2005; the idea is as old as the accounting ledger |
| §6.1 bounded episodes | Crash-only software; stateless service design | Candea & Fox, HotOS 2003; the 12-factor app |
| §6.4 watchdog | Supervision trees, "let it crash" | Erlang/OTP, Ericsson |
| §4.3 two-source gate | N-version programming | Chen & Avizienis, late 1970s |
| §4.3 human escalation | Maker-checker, four-eyes principle, dual control | Banking and industrial ops |
| §4.4 abstention | Reject option in classification; selective prediction | Chow, c. 1970 |
| §4.2 scan-to-confirm | Poka-yoke (mistake-proofing) | Shingo, Toyota Production System, 1960s |
| §4.1 provenance triples | Data lineage; W3C PROV | Data engineering; PROV standardized 2013 |
| §5.3 rule 2 quarantine | Taint tracking; trust boundaries | Security engineering |
| §4.1 grounding | Attribution and grounded generation | Active area at every major LLM lab |

The contribution here is the assembly and the through-line (§2, §10), not any individual
mechanism. Treat any claim that this constitutes novel technique with the same skepticism
the rest of this document applies to unsourced assertions.

## 12. Honest limit

"Never wrong" is not achievable and should not be claimed. The achievable and correct goal:

> **Wrong output cannot become a confident, unverifiable, irreversible action.**

The objective is not a perfect model. It is a system in which model error is structurally
caught — by provenance, by physical confirmation, by episode boundaries, by a second
source, and by a human who is still expected to look — before it costs anything.
