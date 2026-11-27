"""The harness: an engineering deliverable is not done until an independent checker has signed it off.

One task runs through five phases, each an isolated agent on the declared model (the competition allows one model per
entry, so independence comes from isolation and execution, not from a second model):

1. Ledger   compiles the instruction and the project files into a list of checkable requirements, each tagged hard
            or scored with its source, and decides which documents are current and which are superseded.
2. Solver   builds the deliverables against the ledger and records, per requirement, where it is satisfied.
3. Checker  a fresh agent that never sees the solver's reasoning verifies every requirement against the deliverables
            and the sources, executing a check wherever one can be executed, and returns pass or fail with evidence.
4. Repair   each failed requirement goes back, with its evidence, to a focused repair agent; only those items are
            re-checked. Repeats until the ledger is green or the governor stops it.
5. Governor plans time per phase from the task's limit, tracks tokens per phase, and stops repairing when a round
            no longer reduces the failures or the time or token budget is spent.

The harness keeps its own working files in HARNESS_DIR inside the task container, away from the deliverables, and
reports each phase as a JSON line in the run log. It never opens grader files.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from typing import Any

import engine
import taskbridge

HARNESS_DIR = "/tmp/.harness"
LEDGER = f"{HARNESS_DIR}/ledger.json"
CLAIMS = f"{HARNESS_DIR}/claims.json"
SIGNOFF = f"{HARNESS_DIR}/signoff.json"

# Reasoning effort per phase: the solver thinks hardest; ledger and checking are cheaper.
SOLVER_EFFORT = os.environ.get("SOLVER_EFFORT") or None  # None keeps the engine's default (the model's highest)
LEDGER_EFFORT = os.environ.get("LEDGER_EFFORT", "high")
CHECKER_EFFORT = os.environ.get("CHECKER_EFFORT", "high")
MAX_REPAIRS = int(os.environ.get("MAX_REPAIRS", "2"))
# Token budget for the whole task (input + output, cached reads counted at a tenth). 0 means no token cap.
TOKEN_BUDGET = int(os.environ.get("TOKEN_BUDGET", "0"))

# Shares of the task's time, measured from the start.
LEDGER_UNTIL = 0.10
SOLVER_UNTIL = 0.62
CHECK_SHARE = 0.10  # each check pass
FINAL_MARGIN_SEC = 240

FAIR_PLAY = (
    "Never open, list or run anything under /tests, /grader, /solution or /oracle, or any file that looks like a"
    " grader, a reference answer or a test of the deliverable; they are not part of the project."
)

LEDGER_SYSTEM = """You are the lead engineer opening a new job. Before any work starts, you write the job's \
requirement ledger: the contract the deliverables will be checked against. You work in a Linux container (tools act \
inside it; bash starts in {workdir}; no internet).{mcp}

""" + FAIR_PLAY + """

What to do:
1. Read the task instruction closely. Then survey the project files: list everything, open every document that \
could carry a requirement (briefs, specifications, drawings and their title blocks, schedules, mail threads, \
review comments, standards excerpts, spreadsheets). Use the task's own tools for formats bash cannot read.
2. Decide which documents are current. Revisions, dates, "superseded", "void", "issued for", later mail that \
overrides an attachment: when two versions disagree, the later valid one governs. Record every superseded document \
and why.
3. Write every requirement the deliverables must meet, including the ones the instruction does not state but the \
documents impose (a code clause, a limit in a specification, a client's mail, a reviewer's comment, a unit or \
format convention). Each requirement is one checkable statement with a number, unit, threshold, location or format \
where one exists. Mark it "hard" if breaking it makes the deliverable wrong, unsafe, non-compliant or unusable \
(limits, code checks, required content, required file, path or format), else "scored". Say how it can be checked: \
"exec" when a script can verify it against the deliverable (recalculation, a limit check, a file exists and opens, \
geometry, a test passes), else "review".
4. List every deliverable with its exact path and format.

Write the ledger with write_file to {ledger} as JSON in exactly this shape:
{{"deliverables": [{{"path": "...", "format": "...", "what": "..."}}],
 "documents": {{"current": ["path", ...], "superseded": [{{"file": "...", "by": "...", "why": "..."}}]}},
 "requirements": [{{"id": "R1", "text": "...", "type": "hard|scored", "source": "file, page or clause", \
"check": "exec|review", "how": "how to verify it"}}],
 "notes": "method notes for the engineer who will do the work: governing method, key inputs and where they are"}}
Keep requirement text short and specific. Do not start producing the deliverables. When the file is written, \
answer with one line: the number of requirements and deliverables."""

SOLVER_ADDENDUM = """

This job has a requirement ledger, compiled from the instruction and the project files before you started. It is \
the contract your deliverables will be independently checked against, item by item. Use the documents it lists \
as current; ignore the superseded ones. If you find a requirement it missed, meet it too.

""" + FAIR_PLAY + """

Before your final answer, write {claims} with write_file: a JSON object mapping each requirement id to where and \
how the deliverable meets it (file, sheet or section, and the value), so the checker can find it. Then answer with \
your summary as usual."""

SOLVER_PROMPT = """{instruction}

--- Requirement ledger (compiled from the instruction and the project files) ---
{ledger}"""

CHECKER_SYSTEM = """You are the independent checking engineer. You did not do this work and you have not seen how it \
was done. Your signature means every requirement is met; your job is to find what is wrong before anyone relies on \
it. You work in a Linux container (bash starts in {workdir}; no internet).{mcp}

""" + FAIR_PLAY + """

Rules:
- Do not change the deliverables or the project files. You may write scratch scripts under /tmp/.harness/check/ only.
- For a requirement marked "exec", verify it by executing a check: write and run a small script that opens the \
deliverable itself (python3 with whatever libraries the container has; openpyxl for workbooks, recomputing values \
rather than trusting cached results; csv, json, docx and pdf readers; ifcopenshell or text parsing for IFC; run \
solution code and its own checks) and recomputes the quantity from the governing inputs in the current documents. \
Compare with units and tolerances.
- For a "review" requirement, find the exact place in the deliverable that meets it and compare it with the source \
document. A claim that it is met is not evidence; the deliverable and the source are.
- Derive each value from the governing inputs yourself. Do not take the deliverable's own intermediate values, \
cached spreadsheet results or summary text as the reference: a deliverable that agrees with itself proves nothing.
- If a deliverable is code, copy the inputs it reads into a fresh directory under /tmp/.harness/check/ and run it \
there from scratch, as a grader would, then test what it produces.
- Check each requirement with its own command and read its output before you judge it. Never write verdicts from a \
script that decides pass or fail for many requirements at once.
- A missing deliverable, an unreadable file, a wrong path or format, or a value from a superseded document is a fail.
- Be strict on hard requirements and fair on scored ones. Do not invent requirements that are not in the list.

Write your result with bash to {signoff} as JSON: a list of {{"id": "R1", "verdict": "pass|fail", "observed": \
"an exact excerpt, copied character for character, from the output of a command you ran or a file you read that \
shows the deciding value or text", "evidence": "what you ran or read and what it showed, with numbers", "fix": "for a \
fail: exactly what must change"}} with one entry per requirement you were given. The harness checks every pass in \
code: its "observed" excerpt must appear in the output of a command you ran (not in a command you typed, and not in \
the command that writes this file), and for an "exec" requirement it must contain the number you checked. A pass \
without such an excerpt is recorded as unverified, not as a pass. Then answer with one line: how many passed and \
failed."""

CHECKER_PROMPT = """The task the deliverables answer:
{instruction}

The requirements to verify:
{requirements}

The deliverables and where the engineer says each requirement is met (pointers only; verify them):
{deliverables}
{claims}

Current documents: {current}
Superseded documents (must not be used): {superseded}"""

EVIDENCE_RETRY = """Only the requirements below are still open. Your earlier check marked them pass but did not show \
the evidence (the harness note says why). Check each one again with its own command now, and quote the deciding \
output exactly in "observed". If you cannot show it, the verdict is fail.
{items}"""

REPAIR_PROMPT = """{instruction}

--- Requirement ledger ---
{ledger}

--- Independent check: these requirements FAILED ---
{failures}

The deliverables already exist and passed every other requirement. Fix only what failed: change the smallest part \
of the deliverables that makes each failed requirement true, without breaking anything that passed. Recompute with \
code where numbers are involved. Then update {claims} for the items you fixed, and answer with a short summary of \
what you changed."""


VERDICT_WORDS = re.compile(r"\b(pass(ed)?|fail(ed)?|ok|verified|true|false|yes|no)\b", re.I)


def squash(text: str) -> str:
  return " ".join(text.split())


def bind_evidence(rows: dict[str, dict[str, Any]], items: list[dict[str, Any]],
                  capture: list[tuple[str, str]]) -> None:
  """Downgrades every pass whose quoted excerpt is not in what the checker actually ran or read.

  Evidence counts only from outputs of commands (and file reads) that do not mention the sign-off file, and an
  excerpt that also appears in any command the checker typed is an echo of its own text, not a measurement.
  """
  # (typed command, output) pairs; a file read has nothing typed. The sign-off write is never evidence.
  pairs = [("" if cmd.startswith("read_file ") else squash(cmd), squash(out))
           for cmd, out in capture if "signoff" not in cmd]
  kind = {r["id"]: r.get("check") for r in items}
  for rid, row in rows.items():
    if row.get("verdict") != "pass":
      continue
    seen = squash(str(row.get("observed") or ""))
    why = None
    if len(seen) < 6:
      why = "no excerpt quoted"
    elif not VERDICT_WORDS.sub("", seen).strip(" :.-=|"):
      why = "the excerpt is only a verdict word"
    elif not any(seen in out for _, out in pairs):
      why = "the excerpt is not in the output of any command run or file read"
    elif not any(seen in out and seen not in cmd for cmd, out in pairs):
      # every output that shows it came from a command that already held the text (print, echo, a heredoc'd script)
      why = "the excerpt appears in a command the checker typed, so it is an echo, not a measurement"
    elif kind.get(rid) == "exec" and not re.search(r"\d", seen):
      why = "an exec requirement needs the checked number in the excerpt"
    if why:
      row["verdict"] = "unverified"
      row["fix"] = f"The check did not show its evidence ({why}). Verify it again and quote the output."
      row["evidence"] = f"{row.get('evidence', '')} [harness: {why}]"[:2000]


class Boxed:
  """A session whose time ends at `until` (monotonic seconds) or at the task's end, whichever is first."""

  def __init__(self, session: taskbridge.Session, until: float, keep_final: bool,
               capture: list[tuple[str, str]] | None = None):
    object.__setattr__(self, "_s", session)
    object.__setattr__(self, "_until", until)
    object.__setattr__(self, "_keep", keep_final)
    object.__setattr__(self, "_final", "")
    object.__setattr__(self, "_capture", capture)

  async def exec(self, command: str, timeout_sec: int = 120) -> dict[str, Any]:
    result = await self._s.exec(command, timeout_sec)
    if self._capture is not None:
      self._capture.append((command, str(result.get("output") or "")))
    return result

  async def read_file(self, path: str) -> bytes:
    data = await self._s.read_file(path)
    if self._capture is not None:
      self._capture.append((f"read_file {path}", data[:2_000_000].decode("utf-8", "replace")))
    return data

  def __getattr__(self, name: str) -> Any:
    if name == "final_message":
      return self._s.final_message if self._keep else self._final
    return getattr(self._s, name)

  def __setattr__(self, name: str, value: Any) -> None:
    if name == "final_message":
      if self._keep:
        self._s.final_message = value
      else:
        object.__setattr__(self, "_final", value)
      return
    setattr(self._s, name, value)

  def time_left(self) -> float | None:
    box = self._until - time.monotonic()
    real = self._s.time_left()
    return box if real is None else min(real, box)


class Run:
  """One task: the phases, the governor, and the run record."""

  def __init__(self, session: taskbridge.Session):
    self.s = session
    self.start = time.monotonic()
    left = session.time_left()
    self.total = float(left) if left else 6 * 3600.0
    self.record: dict[str, Any] = {"model": engine.MODEL, "phases": [], "repairs": []}
    self.shared: engine.Agent | None = None

  def at(self, share: float) -> float:
    return self.start + self.total * share

  def end(self) -> float:
    return self.start + self.total - FINAL_MARGIN_SEC

  def tokens(self) -> int:
    return sum(u["input"] - u["cached"] + u["cached"] // 10 + u["output"] for u in engine.USAGE.values())

  def over_budget(self) -> bool:
    return bool(TOKEN_BUDGET) and self.tokens() >= TOKEN_BUDGET

  async def note(self, phase: str, **data: Any) -> None:
    entry = {"phase": phase, "t": round(time.monotonic() - self.start), **data}
    self.record["phases"].append(entry)
    await self.s.log("harness " + json.dumps(entry)[:4000])

  async def agent(self, label: str, until: float, keep_final: bool = False,
                  capture: list[tuple[str, str]] | None = None, **kw: Any) -> tuple[engine.Agent, str]:
    a = engine.Agent(Boxed(self.s, until, keep_final, capture), label=label, shared=self.shared, **kw)
    text = await a.run()
    if self.shared is None:
      self.shared = a
    return a, text

  async def read_json(self, path: str) -> Any:
    try:
      return json.loads((await self.s.read_file(path)).decode("utf-8", "replace"))
    except (taskbridge.TaskError, ValueError):
      return None

  # ---------------------------------------------------------------- phases

  async def ledger(self) -> dict[str, Any]:
    await self.s.exec(f"mkdir -p {HARNESS_DIR}/check", timeout_sec=30)
    _, text = await self.agent(
        "ledger", self.at(LEDGER_UNTIL),
        system=LEDGER_SYSTEM.replace("{ledger}", LEDGER), prompt=self.s.instruction, effort=LEDGER_EFFORT)
    ledger = await self.read_json(LEDGER)
    if not isinstance(ledger, dict) or not isinstance(ledger.get("requirements"), list):
      ledger = {"deliverables": [], "documents": {"current": [], "superseded": []}, "requirements": [],
                "notes": "The ledger could not be compiled; work from the instruction."}
    reqs = [r for r in ledger["requirements"] if isinstance(r, dict) and r.get("id")]
    ledger["requirements"] = reqs
    await self.note("ledger", requirements=len(reqs), hard=sum(r.get("type") == "hard" for r in reqs),
                    superseded=len((ledger.get("documents") or {}).get("superseded") or []), summary=text[:300])
    return ledger

  async def solve(self, ledger: dict[str, Any]) -> str:
    system = engine.SYSTEM + SOLVER_ADDENDUM.replace("{claims}", CLAIMS)
    prompt = SOLVER_PROMPT.format(instruction=self.s.instruction, ledger=json.dumps(ledger, indent=1)[:60000])
    _, text = await self.agent("solver", self.at(SOLVER_UNTIL), keep_final=True, system=system, prompt=prompt,
                               effort=SOLVER_EFFORT)
    await self.note("solver", summary=text[:300])
    return text

  async def check(self, ledger: dict[str, Any], items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    claims = await self.read_json(CLAIMS) or {}
    wanted = {r["id"] for r in items}
    claims_text = json.dumps({k: v for k, v in claims.items() if k in wanted}, indent=1)[:20000] if isinstance(claims, dict) else ""
    docs = ledger.get("documents") or {}
    prompt = CHECKER_PROMPT.format(
        instruction=self.s.instruction,
        requirements=json.dumps(items, indent=1)[:40000],
        deliverables=json.dumps(ledger.get("deliverables") or [], indent=1)[:8000],
        claims=claims_text,
        current=json.dumps(docs.get("current") or [])[:4000],
        superseded=json.dumps(docs.get("superseded") or [])[:4000])
    results: dict[str, dict[str, Any]] = {}
    todo, attempt = items, 0
    while todo:
      capture: list[tuple[str, str]] = []
      await self.s.exec(f"rm -f {SIGNOFF}", timeout_sec=30)
      until = min(self.end() - 60, time.monotonic() + self.total * CHECK_SHARE * (1.0 if attempt == 0 else 0.5))
      await self.agent(label if attempt == 0 else f"{label}-evidence", until,
                       system=CHECKER_SYSTEM.replace("{signoff}", SIGNOFF), capture=capture,
                       prompt=prompt if attempt == 0 else prompt + "\n\n" + EVIDENCE_RETRY.format(
                           items="\n".join(f"- {r['id']}: {results[r['id']].get('evidence', '')[-300:]}" for r in todo)),
                       effort=CHECKER_EFFORT, allow={"bash", "read_file"})
      out = await self.read_json(SIGNOFF)
      ids = {r["id"] for r in todo}
      rows = {row["id"]: row for row in (out if isinstance(out, list) else [])
              if isinstance(row, dict) and row.get("id") in ids}
      bind_evidence(rows, todo, capture)
      results.update(rows)
      for r in todo:  # an item the checker never reached is not signed off
        results.setdefault(r["id"], {"id": r["id"], "verdict": "unchecked", "evidence": "", "fix": ""})
      unverified = [r for r in todo if results[r["id"]].get("verdict") == "unverified"]
      await self.note("evidence", label=label, attempt=attempt, checked=len(rows), unverified=len(unverified),
                      excerpts=len(capture))
      attempt += 1
      if attempt > 1 or not unverified or self.left() < self.total * CHECK_SHARE:
        break
      todo = unverified
    return results

  async def repair(self, ledger: dict[str, Any], failed: list[dict[str, Any]], n: int) -> None:
    failures = "\n".join(
        f"- {f['id']} ({f.get('type', '')}): {f.get('text', '')}\n  evidence: {f.get('evidence', '')}\n  fix: {f.get('fix', '')}"
        for f in failed)
    prompt = REPAIR_PROMPT.format(instruction=self.s.instruction, ledger=json.dumps(ledger, indent=1)[:40000],
                                  failures=failures[:30000], claims=CLAIMS)
    until = min(self.end() - self.total * CHECK_SHARE, time.monotonic() + self.total * 0.12)
    _, text = await self.agent(f"repair{n}", until, keep_final=False, system=engine.SYSTEM + "\n\n" + FAIR_PLAY,
                               prompt=prompt, effort=SOLVER_EFFORT)
    self.record["repairs"].append({"round": n, "items": [f["id"] for f in failed], "summary": text[:400]})

  # ---------------------------------------------------------------- the run

  async def run(self) -> str:
    ledger = await self.ledger()
    summary = await self.solve(ledger)
    reqs = ledger["requirements"]
    if not reqs:
      return summary
    by_id = {r["id"]: r for r in reqs}
    if self.left() < self.total * CHECK_SHARE * 0.5:
      await self.note("signoff", skipped="no time left to check")
      return summary
    verdicts = await self.check(ledger, reqs, "checker")
    rounds, previous = 0, None
    while True:
      failing = [v for v in verdicts.values() if v.get("verdict") != "pass"]
      await self.note("signoff", round=rounds, passed=len(reqs) - len(failing), failed=len(failing),
                      hard_failed=sum(by_id[v["id"]].get("type") == "hard" for v in failing),
                      failing=[{"id": v["id"], "evidence": str(v.get("evidence", ""))[:300]} for v in failing][:30],
                      tokens=self.tokens())
      # Repair only what the checker showed to be wrong. An item it could not evidence (unverified) or never
      # reached (unchecked) stays open in the sign-off, but rewriting a deliverable nobody faulted helps nothing.
      to_fix = [v for v in failing if v.get("verdict") == "fail"]
      if not to_fix:
        if failing:
          await self.note("governor", stop="nothing shown to be wrong; the rest stay open",
                          open_items=[v["id"] for v in failing])
        break
      stop = None
      if rounds >= MAX_REPAIRS:
        stop = "repair limit"
      elif previous is not None and len(to_fix) >= previous:
        stop = "the last repair did not reduce the failures"
      elif self.left() < self.total * (CHECK_SHARE + 0.06):
        stop = "not enough time for another repair and check"
      elif self.over_budget():
        stop = "token budget spent"
      if stop:
        await self.note("governor", stop=stop, open_items=[v["id"] for v in failing])
        break
      rounds += 1
      previous = len(to_fix)
      await self.repair(ledger, [{**by_id[v["id"]], **v} for v in to_fix], rounds)
      verdicts.update(await self.check(ledger, [by_id[v["id"]] for v in to_fix], f"recheck{rounds}"))
    return await self.closing(ledger, verdicts, summary)

  def left(self) -> float:
    return self.end() - time.monotonic()

  async def closing(self, ledger: dict[str, Any], verdicts: dict[str, dict[str, Any]], summary: str) -> str:
    reqs = ledger["requirements"]
    hard = [r for r in reqs if r.get("type") == "hard"]
    open_hard = [r["id"] for r in hard if verdicts.get(r["id"], {}).get("verdict") != "pass"]
    passed = sum(verdicts.get(r["id"], {}).get("verdict") == "pass" for r in reqs)
    self.record["signoff"] = {"requirements": len(reqs), "passed": passed, "hard": len(hard), "open_hard": open_hard,
                              "usage": engine.USAGE}
    await self.s.log("harness " + json.dumps({"phase": "record", **self.record})[:60000])
    try:
      await self.s.write_file(f"{HARNESS_DIR}/record.json", json.dumps({"record": self.record, "ledger": ledger,
                                                                        "verdicts": verdicts}, indent=1))
    except taskbridge.TaskError:
      pass
    lines = [summary.strip(), "", "Independent check of the deliverables against the requirement ledger:",
             f"{passed} of {len(reqs)} requirements verified, {len(hard) - len(open_hard)} of {len(hard)} hard requirements."]
    superseded = (ledger.get("documents") or {}).get("superseded") or []
    if superseded:
      lines.append("Superseded documents set aside: " + "; ".join(
          f"{d.get('file')} (replaced by {d.get('by')})" for d in superseded if isinstance(d, dict))[:1500])
    if open_hard:
      lines.append("Hard requirements still open: " + ", ".join(open_hard))
    text = "\n".join(lines)
    self.s.final_message = text
    return text


async def solve(session: taskbridge.Session) -> str:
  print(f"task: workdir {session.workdir}, model {engine.MODEL}, {len(session.mcp_servers)} MCP server(s)",
        file=sys.stderr)
  return await Run(session).run()


if __name__ == "__main__":
  taskbridge.run(solve)
