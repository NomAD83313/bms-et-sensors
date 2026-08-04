# Matter + Thread Topology

This document describes the current canonical topology model used by `/matter/thread-topology`.

It reflects the backend merge strategy implemented in `app/matter/thread_topology.py`.

## Goal

Produce one stable topology snapshot that:

- matches Matter nodes with OTBR-observed routers and children
- keeps duplicate-address situations visible instead of silently hiding them
- prefers deterministic backend inference over browser-side heuristics
- remains stable after re-pairing and partial OTBR visibility

## Inputs

The topology builder merges three classes of evidence:

1. Matter inventory from `python-matter-server`
2. Matter-side Thread diagnostics (`ThreadNetworkDiagnostics`)
3. OTBR diagnostics:
   - router table
   - neighbor table
   - `meshdiag topology children`

## Core assumptions

- `ext_address` is identity
- `rloc16` is locator only
- duplicate available Matter `ext_address` values are quarantined
- OTBR may report valid children with only `rloc16`
- the browser should render the backend snapshot, not invent matches itself

## Output model

The endpoint returns:

- `nodes`
- `edges`
- `tree`
- `warnings`
- `matter_inventory`
- `observed_topology`
- `rules`
- `counters`

The UI at `/matter/` consumes this snapshot directly.

## Matching order

Exact `ext_address` matches are resolved first.

After that, inferred matches are applied in two layers:

1. router inference
2. child inference

### Router inference

Routers may be matched by `router_neighbor_set_evidence` when:

- the reported Matter `ext_address` is not trustworthy enough to use directly
- OTBR exposes a router candidate
- parent/upstream and neighbor/child sets make one candidate uniquely plausible

### Child inference

Child matching now uses one ordered pass with three explicit rules:

1. `same_rloc_and_parent_neighbor_evidence`
2. `quarantined_child_parent_evidence`
3. `unique_residual_parent_child`

#### 1. `same_rloc_and_parent_neighbor_evidence`

Use when:

- a trusted Matter child is observed under a parent
- OTBR reports a child under the same parent
- the child `rloc16` matches uniquely

This is the strongest child rule.

If OTBR reports only a child `rloc16`, the trusted Matter identity is preserved while the OTBR `rloc16` is adopted into the merged node.

#### 2. `quarantined_child_parent_evidence`

Use when:

- a Matter child is quarantined because its available `ext_address` conflicts
- under the same parent there is exactly one unresolved OTBR child
- no trusted sibling already owns that child locator

This keeps duplicate-address situations explicit while still allowing a safe match.

#### 3. `unique_residual_parent_child`

Use when:

- one trusted Matter child remains under a parent
- one unresolved OTBR child remains under that same parent
- the unresolved OTBR child is not already the same child identity

This is the last fallback and is intentionally weaker than the first two rules.

It is the rule that keeps the Pico case stable when OTBR exposes the child only as `rloc-only`.

## Warning policy

Warnings are preserved unless the backend has resolved the specific node or child relation.

Typical warnings:

- `matter_thread_address_conflict`
- `matter_node_unmatched_in_otbr`
- `otbr_node_unmatched_in_matter`

Warnings are filtered only after inferred matches are finalized, so the UI does not show stale mismatch noise for already-resolved nodes.

## Why this model exists

Direct `ext_address` equality is not enough in the field because:

- re-pairing can leave stale or duplicated reported addresses
- OTBR and Matter can observe the same network state with different completeness
- OTBR child visibility may collapse to `rloc16` only

The current model prefers a straight ordered inference path instead of several overlapping post-fix helpers.

## Practical operator guidance

When checking a problematic node:

1. Open `/matter/` and inspect the topology tree.
2. Open `/matter/thread-topology` and review `warnings`, `rules`, and matched node fields.
3. Confirm whether the node is:
   - exactly matched
   - inferred by router evidence
   - inferred by one of the child rules
   - still unresolved

If a node appears only in `Known Matter Devices`, the missing evidence is usually one of:

- no unique parent observation
- duplicate reported identity with no unique OTBR child remaining
- OTBR diagnostics not yet refreshed enough to expose the child/router relation

## Current scope limits

- This model is intended for stable operator-facing topology snapshots, not protocol forensics.
- It does not attempt probabilistic multi-candidate ranking.
- New inference types should be added as explicit ordered rules, not as ad hoc UI-side fallback logic.
