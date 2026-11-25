# Build Spec: Agent Harness for Physical-Engineering's Last Exam (Kaggle)
From MZA to SZA, 2026-11-25. Brian approved the concept and ordered the build handover the same day. Product name is PENDING (Brian is naming it separately); use the neutral working label "the harness" in code and docs, repo: asiyetajwa/ple-agent-harness. Do not bake any candidate name into the code.

## The competition (verified from the official rules and the benchmark paper)
- "Agent Harness on Physical-Engineering's Last Exam", Google LLC on Kaggle. Benchmark built with Mercor and university partners. Paper: arXiv:2610.30537. Dev kit and submission template: google-research/ple on GitHub. Dev tasks are Harbor tasks; task list in the kit's eval/dev_set.yaml.
- Cash: 1st $250,000, 2nd $100,000, 3rd $50,000. Final deadline January 5, 2027, 23:59 UTC.
- The task: an open-source autonomous agent harness that solves long-horizon professional engineering tasks (85 total across structural, civil, electrical, mechanical). A task is an instruction plus an environment (on average 61 files: PDFs, spreadsheets, DXF/DWG CAD, IFC models, mail, documents, including planted superseded revisions) plus a deliverable spec (report, calc workbook, schedule, 2D CAD, 3D BIM/IFC, or solution.py) plus a grader (executable verifier, or an expert rubric judged by a model).
- Two stages. Stage 1: a Kaggle Writeup (max 2,000 words) acts as the gate. Rubric: Failure Analysis 25, Engineering Reasoning 25, Efficiency and Scaling 20, Cross-Model 15, Clarity 15. Required figures: architecture diagram, score vs cost plot, failure breakdown.
- Stage 2 (top 100): the harness is installed in a clean debian:bookworm container (30 min install, /opt/harness max 2 GiB), must pass a WebSocket serve smoke test (bind within 120s), then runs 56 hidden tasks x 3 runs in 2 CPU / 4 GB containers, 6 hours per task. No web search, no custom API keys, no external servers in Stage 2: Google supplies the model key free. Sponsored models (11, referenced as google-cloud/<id>): includes Gemini 4 family (Neon, Carbon, Hydrogen, Argon), Gemini 3.8 Flash, Gemini 3.1 Pro Preview, Gemma 4 26B, Claude Opus 5.5, GPT-OSS 120B, GLM 5.2, Llama 4 Maverick.
- Scoring: prizes go to the top 3 mean scores among systems on the performance vs cost Pareto frontier; qualifying mean score at least 0.138. Cost is token usage at list prices, cached reads included. Co-authored tasks score s = 0.4p + 0.6 * G * O', where G is a hard-constraint gate: fail one hard constraint and the objective term is zeroed. Rubric tasks are weighted binary items.
- Submission mechanics: GitHub repo (public at close) with the 40-char commit SHA in Project Links, containing submission.yaml, serve.py / harness.py, an MIT or Apache-2.0 licence, and unpacked artifacts/ (max 512 MiB, max 10k files, no LFS, no symlinks) produced by the kit's eval run. Must pass `kit check` and `kit artifacts`. Local dev may use any open-weight or personal API model to generate artifacts if documented; Stage 2 runs sponsored models only.
- Baselines from the paper: best public-set result 50.6% (Claude Opus 5.5 in the Claude Code harness) at $17.18 per task. The field's bottleneck: top agents pass 91% of constraints on average but only 41.3% of items pass ALL hard constraints.

## The concept (Brian-approved)
Rebuild the profession's independent checking engineer as software. An agent produces the deliverable; nothing counts as done until an independent checker has signed it off, requirement by requirement. Narrative (for the write-up, in this order): the micro problem we face first-hand (agents reporting success that never happened, in our own daily work), the same failure at macro scale (engineering's checking institution, the rework economics, the 41.3% figure), and this system as the fix at both scales.

## Architecture (build exactly this shape; improve internals freely, report material changes)
1. **Constraint Ledger.** First pass over the task: compile the brief and environment into a formal ledger of machine-checkable predicates (quantity, unit, threshold, code clause, geometry rule), each tagged hard or scored, each with a pointer to its source (file, page, clause). Version resolution happens here: identify superseded documents and exclude them, recording the decision. The ledger is the contract for the whole run and is saved as a run artifact.
2. **Solver.** Model-agnostic agent loop over the task's tools (filesystem, documents, spreadsheets, PDFs, mail, calendar, chat, code sandbox, per the Archipelago/Harbor environment). Builds the deliverable against the ledger and emits a claim set mapping every ledger item to where in the deliverable it is satisfied.
3. **Independent Sign-Off (the checker).** A separate agent, different model from the solver, given ONLY the deliverable, the ledger and the source environment, never the solver's transcript or reasoning. For each ledger item: if it is executable, execute it (IFC clash and geometry checks, spreadsheet recalculation and formula audit, unit and dimensional analysis, code execution and tests, arithmetic re-derivation); if not executable, verify adversarially against sources. Output: per-item pass/fail with evidence.
4. **Constraint-directed repair.** Each failed item returns to the solver with the exact predicate, the failure and the evidence; only the failing part is re-solved. Loop until the ledger is green or the Budget Governor stops the run. Every repair round is logged (the write-up's failure analysis feeds on this log).
5. **Budget Governor.** Per-task spend plan from dev-set calibration. Phase routing: cheap sponsored models for extraction, retrieval, ledger drafting and checker passes; the strongest model reserved for solver reasoning bottlenecks. Track cost at list prices including cached reads, predict marginal score per dollar, and stop when the next dollar buys too little. Determinism discipline for the 3-run Stage 2 format: fixed seeds where the API allows, no dependence on run order, idempotent tool use.

## The magic standard (Brian's order; the surface is held to this)
1. One drop, no instructions: the whole messy task goes in as it arrives; a finished, checked deliverable comes out. No per-task configuration.
2. It knows what it was never told: hidden requirements found wherever they sit; superseded revisions recognised and set aside quietly; units and versions reconciled without asking.
3. The catch is the showpiece: the checker visibly catches the one fatal flaw in an otherwise perfect deliverable, repairs it, re-checks and stamps the run.
4. Calm surface, deep audit: default output is the deliverable plus its sign-off summary (every hard requirement accounted for); the full ledger, executed checks and repair history are available as artifacts on demand.
5. Discipline-fluent: the same harness, no per-discipline tuning knobs exposed, across structural, civil, electrical and mechanical tasks.
6. It never asks a question the documents can answer.

## Targets (dev set, all reproducible from the repo)
- Beat 50.6% mean score (the paper's best baseline) on the dev set.
- At a small fraction of $17.18 per task; publish the score vs cost curve (a required write-up figure anyway).
- Headline metric: all-hard-constraints-pass rate, driven up from the field's 41.3%.
- Cross-model table: identical harness across at least 4 sponsored models (feeds the Cross-Model rubric, 15 points).
- Failure taxonomy from our own dev runs, categorised and counted (feeds Failure Analysis, 25 points).

## Engineering constraints to respect from day one
- Stage 2 sandbox: 2 CPU / 4 GB RAM, 6h per task, debian:bookworm, install in 30 min, /opt/harness max 2 GiB, WebSocket serve interface per the kit template, bind within 120s. Design for this envelope, not for our dev machines.
- No network beyond the supplied model endpoint in Stage 2; no external servers; no custom keys. Anything the harness needs must ship inside the repo/artifacts.
- Anti-overfit: graders re-run random dev tasks and re-grade artifacts; hard-coding anything task-specific is disqualification. Generality is a correctness property here.
- Licence: MIT (repo ships with it). Keep third-party dependencies licence-clean and inside the size envelope.

## Deliverables from the build
1. The harness in this repo, passing `kit check`, with artifacts generated by the kit eval on the dev set.
2. Dev-set results file: per-task score, cost, all-hard-constraints pass/fail, repair rounds, model used per phase.
3. The cross-model results table (same harness, 4+ sponsored models).
4. The failure taxonomy with counts and 3 to 5 fully documented catch stories (task, flaw, evidence, repair) for the write-up.
5. A short build report back to MZA in the Gold Room after each working session: what runs, what the numbers are, what is blocked.

## Working rules
- Brian's standing orders apply: report numbers only from actual runs; a claim is only as strong as the check behind it; if a dev run fails, say so plainly with the evidence.
- MZA handles: Kaggle registration and the final write-up, the submission mechanics, and all Brian-facing reporting. SZA owns the build.
- Name: pending Brian's pick; nothing in code or docs should hard-code a product name.
