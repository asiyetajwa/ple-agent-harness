# PLE Agent Harness

An open-source autonomous agent harness for Google's Kaggle competition
"Agent Harness on Physical-Engineering's Last Exam": long-horizon professional
engineering tasks across structural, civil, electrical and mechanical
engineering, solved end to end and independently checked before anything
counts as done.

## The idea

Engineering runs on independent checking: a second engineer, who did not do
the work, verifies it against the brief and the codes before it is signed.
This harness rebuilds that institution for agent-produced work.

1. **Constraint Ledger**: the brief and its files are compiled into a formal
   list of machine-checkable requirements, each tagged hard or scored.
2. **Solver**: an agent produces the deliverable against the ledger.
3. **Independent Sign-Off**: a second agent, on a different model and never
   shown the solver's reasoning, verifies every requirement, executing the
   check wherever a check can be executed (geometry and clash checks,
   spreadsheet recalculation, unit analysis, code tests).
4. **Constraint-directed repair**: failures return with their evidence and
   only the failing part is redone, until the ledger is green.
5. **Budget Governor**: cheaper models handle extraction and checking, the
   strongest model is reserved for the hardest reasoning, and spend per task
   is planned and capped, because the competition rewards correctness
   per dollar.

## Status

Build in progress. The full build specification is in [SPEC.md](SPEC.md).

## Licence

MIT
