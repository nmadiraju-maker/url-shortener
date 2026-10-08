"""Development Agent: materialises a baseline (greenfield) or applies feature change plans (brownfield)
into the git workspace, one conventional commit per story.

Offline mode replays reviewed change plans from `scenarios/changes/<feature>.py`.  To exercise the
governance loop realistically, a scenario may replay the *flawed first drafts* we observed from the
coding assistant (`draft_defects`), which policy/review must catch.  In LLM mode the plan is generated.
"""
from __future__ import annotations

import importlib.util
import io
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from ..errors import AgentError
from ..workspace import Workspace, git_executable
from .base import Agent, AgentContext, StageResult

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_plan(feature: str, changes_dir: Path) -> dict[str, Any] | None:
    path = changes_dir / f"{feature}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"change_{feature}", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return {"edits": mod.EDITS, "defects": getattr(mod, "DEFECTS", {}), "kind": getattr(mod, "KIND", "feat"),
            "scope": getattr(mod, "SCOPE", feature)}


def apply_edits(ws: Workspace, edits: list[dict[str, Any]]) -> list[str]:
    touched = []
    for e in edits:
        if "create" in e:
            ws.write(e["file"], e["create"])
        else:
            src = ws.read(e["file"])
            hits = src.count(e["find"])
            if hits != 1:  # never guess on brownfield code: anchor must be unique
                raise AgentError(f"patch conflict in {e['file']}: anchor found {hits} times: {e['find'][:60]!r}")
            ws.write(e["file"], src.replace(e["find"], e["replace"]))
        touched.append(e["file"])
    return touched


def export_baseline(ref: str, paths: list[str], dest: Path, source: Path = REPO_ROOT) -> str:
    """Export `paths` exactly as they are at git `ref` in `source`.

    If `source` is a git repository the export is exact or it fails (git's own error is reported): it never
    silently substitutes the working tree, which could smuggle uncommitted edits into a "baseline". A source
    that is not a git repository is copied as a plain directory, which is stated in the returned origin.
    """
    if not (source / ".git").exists():
        for p in paths:
            src = source / p
            if src.is_dir():
                shutil.copytree(src, dest / p, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
            elif src.exists():
                shutil.copy2(src, dest / p)
        return "directory (not a git repository)"
    proc = subprocess.run([git_executable(), "archive", "--format=tar", ref, *paths], cwd=source,
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise AgentError(f"cannot export {ref} from {source}: {proc.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return f"git:{ref}"


class DevelopmentAgent(Agent):
    name = "development"

    def __init__(self) -> None:
        self.invocations = 0

    def run(self, ctx: AgentContext) -> StageResult:
        self.invocations += 1
        ws, req = ctx.workspace, ctx.context.require_dict("requirements")
        base = ws.head()
        commits, notes = [], []
        if ctx.params.get("mode") == "materialize":
            source = Path(ctx.params.get("source", REPO_ROOT))
            origin = export_baseline(ctx.params["ref"], [p for g in ctx.params["commit_plan"] for p in g["paths"]],
                                     ws.root, source=source)
            for group in ctx.params["commit_plan"]:
                ws.git("add", "--", *group["paths"])    # the export is exact, so every path exists
                if not ws.git("diff", "--cached", "--name-only"):
                    raise AgentError(f"commit plan step {group['message'].splitlines()[0]!r} matched no files "
                                     f"in {ctx.params['ref']} of {source}")
                ws.git("commit", "-q", "-m", group["message"])
                commits.append(group["message"].splitlines()[0])
            notes.append(f"materialised blueprint from {origin}")
        else:
            changes_dir = Path(ctx.params.get("changes_dir", REPO_ROOT / "scenarios" / "changes"))
            defects = ctx.params.get("draft_defects", {}).get(str(self.invocations), [])
            story_by_feature = {s["feature"]: s for s in req["stories"]}
            req_art = ctx.context.get("requirements")
            req_ref = req_art.hash if req_art is not None else "unknown"
            for feature in [f for f in req["features"] if story_by_feature[f]["kind"] != "regression"]:
                plan = load_plan(feature, changes_dir)
                if plan is None:
                    raise AgentError(f"no change plan available for feature '{feature}'")
                apply_edits(ws, plan["edits"])
                for d in defects:
                    feat, _, name = d.partition(":")
                    if feat == feature:
                        apply_edits(ws, plan["defects"][name])
                        notes.append(f"replayed recorded first-draft defect '{d}'")
                story = story_by_feature[feature]
                msg = (f"{plan['kind']}({plan['scope']}): {story['i_want']} ({story['id']})\n\n"
                       f"Acceptance criteria: {', '.join(a['id'] for a in story['acceptance_criteria'])}\n"
                       f"Refs: requirements@{req_ref}")
                if ctx.feedback:
                    msg += "\nAddresses review feedback:\n" + "\n".join(f"- {f}" for f in ctx.feedback[-5:])
                if ws.commit(msg):
                    commits.append(msg.splitlines()[0])
        changed, deleted, lines = ws.changes_since(base)
        change = {"base": base, "head": ws.head(), "commits": commits, "files": sorted(changed), "deleted": deleted,
                  "lines_changed": lines, "invocation": self.invocations, "attempt": ctx.attempt,
                  "feedback_addressed": ctx.feedback, "notes": notes}
        return StageResult(summary=f"{len(commits)} commit(s), {len(changed)} files, {lines} lines",
                           artifacts={"code_change": change}, checks={"compiles": _compiles(changed)},
                           commit_message=None)


def _compiles(changed: dict[str, str]) -> bool:
    for path, src in changed.items():
        if path.endswith(".py"):
            try:
                compile(src, path, "exec")
            except SyntaxError:
                return False
    return True
