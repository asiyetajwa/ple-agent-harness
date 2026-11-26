"""Offline test of the phase flow: a fake task session and scripted agents stand in for the evaluator and model."""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MODEL_NAME", "test-model")
import engine  # noqa: E402
import serve  # noqa: E402


class FakeSession:
  instruction = "Write /app/out/calc.txt with the beam moment."
  workdir = "/app"
  mcp_servers = []
  final_message = ""

  def __init__(self):
    self.files = {}
    self.logs = []
    self.t0 = time.monotonic()

  def time_left(self):
    return 6 * 3600 - (time.monotonic() - self.t0)

  async def exec(self, command, timeout_sec=120):
    if command.startswith("rm -f "):
      self.files.pop(command[6:], None)
    return {"exit_code": 0, "output": ""}

  async def read_file(self, path):
    if path not in self.files:
      raise serve.taskbridge.TaskError("missing")
    return self.files[path].encode()

  async def write_file(self, path, data, append=False):
    self.files[path] = data if isinstance(data, str) else data.decode()

  async def log(self, text):
    self.logs.append(text)

  async def usage(self, *a):
    pass


def scripted(session, checker_rounds):
  """Builds a fake Agent class whose behaviour depends on its label."""
  calls = []

  class FakeAgent:
    def __init__(self, s, label="agent", shared=None, system=None, prompt=None, effort=None, allow=None):
      self.s, self.label, self.prompt, self.allow = s, label, prompt, allow
      self.mcp, self.tools, self.mcp_prompt = {}, [], ""

    async def run(self):
      calls.append((self.label, self.allow, self.s.time_left()))
      engine.USAGE.setdefault(self.label, {"input": 100, "output": 10, "cached": 0, "calls": 1})
      if self.label == "ledger":
        session.files[serve.LEDGER] = json.dumps({
            "deliverables": [{"path": "/app/out/calc.txt", "format": "txt", "what": "moment"}],
            "documents": {"current": ["spec_rev_B.pdf"], "superseded": [{"file": "spec_rev_A.pdf", "by": "spec_rev_B.pdf", "why": "later revision"}]},
            "requirements": [{"id": "R1", "text": "M within 1%", "type": "hard", "check": "exec"},
                             {"id": "R2", "text": "units stated", "type": "scored", "check": "review"}]})
        return "2 requirements"
      if self.label == "solver":
        session.files[serve.CLAIMS] = json.dumps({"R1": "calc.txt line 3", "R2": "header"})
        return "Wrote /app/out/calc.txt"
      if self.label.startswith("repair"):
        return "fixed R1"
      verdicts = checker_rounds.pop(0)
      session.files[serve.SIGNOFF] = json.dumps(verdicts)
      return "checked"

  return FakeAgent, calls


def run_case(checker_rounds):
  engine.USAGE.clear()
  s = FakeSession()
  fake, calls = scripted(s, checker_rounds)
  serve.engine.Agent = fake
  out = asyncio.run(serve.Run(s).run())
  return s, out, calls


def test_catch_repair_and_sign_off():
  s, out, calls = run_case([
      [{"id": "R1", "verdict": "fail", "evidence": "recomputed 41.2 kNm, file says 45.0", "fix": "use rev B span"},
       {"id": "R2", "verdict": "pass", "evidence": "kNm in header"}],
      [{"id": "R1", "verdict": "pass", "evidence": "41.2 kNm"}],
  ])
  labels = [c[0] for c in calls]
  assert labels == ["ledger", "solver", "checker", "repair1", "recheck1"]
  assert calls[2][1] == {"bash", "read_file"}  # the checker cannot write files
  assert "2 of 2 requirements verified, 1 of 1 hard" in out
  assert "spec_rev_A.pdf (replaced by spec_rev_B.pdf)" in out
  assert "still open" not in out
  record = json.loads(s.files["/tmp/.harness/record.json"])
  assert record["record"]["repairs"][0]["items"] == ["R1"]


def test_governor_stops_when_repair_does_not_help():
  s, out, calls = run_case([
      [{"id": "R1", "verdict": "fail", "evidence": "x", "fix": "y"}, {"id": "R2", "verdict": "pass"}],
      [{"id": "R1", "verdict": "fail", "evidence": "still x", "fix": "y"}],
  ])
  assert [c[0] for c in calls] == ["ledger", "solver", "checker", "repair1", "recheck1"]
  assert "Hard requirements still open: R1" in out
  assert any('"stop": "the last repair did not reduce the failures"' in l for l in s.logs)


def test_unreached_items_are_not_signed_off():
  s, out, calls = run_case([[{"id": "R2", "verdict": "pass"}], [{"id": "R1", "verdict": "pass"}]])
  assert [c[0] for c in calls][-2:] == ["repair1", "recheck1"]
  assert "2 of 2" in out


def test_phase_time_boxes():
  s, out, calls = run_case([[{"id": "R1", "verdict": "pass"}, {"id": "R2", "verdict": "pass"}]])
  ledger_box, solver_box = calls[0][2], calls[1][2]
  assert ledger_box <= 6 * 3600 * serve.LEDGER_UNTIL + 1
  assert solver_box <= 6 * 3600 * serve.SOLVER_UNTIL + 1
  assert [c[0] for c in calls] == ["ledger", "solver", "checker"]
