"""Command line: run | resume | stop | verify | promote."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import report, scenario
from .agents.registry import default_agents
from .approvals import ApprovalGateway, DecisionFileApprovals, InteractiveApprovals
from .audit import AuditLog
from .engine import EngineConfig, Orchestrator
from .workspace import Workspace, git_executable

REPO = Path(__file__).resolve().parents[1]


def _gateway(args: argparse.Namespace) -> ApprovalGateway:
    return InteractiveApprovals() if args.interactive else DecisionFileApprovals(args.approvals)


def cmd_run(args: argparse.Namespace) -> int:
    sc = scenario.load(args.scenario)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    run_dir = Path(args.run_dir or REPO / "runs" / f"{sc['name']}-{stamp}")
    orch = scenario.build(sc, run_dir, _gateway(args))
    status = orch.run()
    return _finish(orch, sc["name"], status)


def cmd_resume(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    sc = json.loads((run_dir / "scenario.json").read_text())
    orch = Orchestrator.resume(run_dir, default_agents(), workspace=Workspace(run_dir / "workspace"),
                               approvals=_gateway(args), config=EngineConfig(**sc.get("engine", {})))
    status = orch.run()
    return _finish(orch, sc["name"] + " (resumed)", status)


def _finish(orch: Orchestrator, title: str, status: str) -> int:
    path = report.write(orch, title)
    print(json.dumps({"run_dir": str(orch.run_dir), "status": status, "stages": orch.status,
                      "metrics": {k: v for k, v in orch.metrics.summary().items() if k != "per_stage"}}, indent=2))
    print(f"report: {path}")
    return 0 if status == "COMPLETED" else (3 if status == "PAUSED" else 1)


def cmd_stop(args: argparse.Namespace) -> int:
    (Path(args.run_dir) / "STOP").write_text(args.reason)
    print("kill switch set; the engine will safe-stop at the next scheduling point")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    ok = AuditLog(Path(args.run_dir) / "audit.jsonl", "verify").verify()
    print("audit chain intact" if ok else "AUDIT CHAIN BROKEN")
    return 0 if ok else 2


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Argument list, no shell, absolute git path; stdin closed so git can never wait for input.
    return subprocess.run([git_executable(), *args], cwd=repo, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120, check=False)


def cmd_promote(args: argparse.Namespace) -> int:
    """Human-gated merge of a completed run's commits into the target repository (default: this one)."""
    run_dir = Path(args.run_dir).resolve()
    target = Path(args.target).resolve() if args.target else REPO
    state = json.loads((run_dir / "state.json").read_text())
    if state["run_status"] != "COMPLETED":
        print(f"refusing: run status is {state['run_status']}")
        return 1
    if not AuditLog(run_dir / "audit.jsonl", "verify").verify():
        print("refusing: audit chain broken")
        return 2
    if _git(target, "status", "--porcelain").stdout.strip():
        print(f"refusing: {target} has uncommitted changes; commit or stash them first")
        return 1
    ws = Workspace(run_dir / "workspace")
    seed = ws.git("log", "--format=%H", "--grep=^chore: seed workspace", "-n", "1")
    base = args.base or seed or ws.git("rev-list", "--max-parents=0", "HEAD").splitlines()[0]
    patches = run_dir / "patches"
    ws.git("format-patch", "-q", "-o", str(patches), f"{base}..HEAD")
    files = sorted(patches.glob("*.patch"))
    if not files:
        print("nothing to promote")
        return 1
    proc = _git(target, "am", "-q", "--3way", *map(str, files))
    if proc.returncode != 0:
        _git(target, "am", "--abort")
        print("promotion failed, repository left unchanged:\n" + (proc.stderr + proc.stdout).strip())
        return 1
    tag = ws.git("tag", "--points-at", "HEAD")
    if tag:
        message = f"{tag} promoted from {state['run_id']} by {args.approver}"
        _git(target, "tag", "-a", tag, "-m", message)
    AuditLog(run_dir / "audit.jsonl", state["run_id"]).emit("run.promoted", actor=args.approver,
                                                            commits=len(files), target=str(target), tag=tag)
    print(f"promoted {len(files)} commits{' and tag ' + tag if tag else ''}, approved by {args.approver}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sdlc", description="Governed agentic SDLC orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--approvals")
    r.add_argument("--interactive", action="store_true")
    r.add_argument("--run-dir")
    r.set_defaults(fn=cmd_run)
    rs = sub.add_parser("resume")
    rs.add_argument("run_dir")
    rs.add_argument("--approvals")
    rs.add_argument("--interactive", action="store_true")
    rs.set_defaults(fn=cmd_resume)
    s = sub.add_parser("stop")
    s.add_argument("run_dir")
    s.add_argument("--reason", default="operator kill switch")
    s.set_defaults(fn=cmd_stop)
    v = sub.add_parser("verify")
    v.add_argument("run_dir")
    v.set_defaults(fn=cmd_verify)
    pr = sub.add_parser("promote")
    pr.add_argument("run_dir")
    pr.add_argument("--approver", required=True)
    pr.add_argument("--base")
    pr.add_argument("--target", help="repository to promote into (default: this repository)")
    pr.set_defaults(fn=cmd_promote)
    args = p.parse_args(argv)
    command: Callable[[argparse.Namespace], int] = args.fn
    return command(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
