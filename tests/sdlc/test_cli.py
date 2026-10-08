"""The command line, end to end: run, pause/resume, stop, verify, and the human-gated promote."""
import io
import json
import subprocess
from pathlib import Path

import pytest

from sdlc import cli
from sdlc.workspace import Workspace

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@x"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def make_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Owner")
    git(path, "config", "user.email", "owner@example.invalid")
    for name, text in files.items():
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", "chore: baseline")
    return path


def write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data))
    return path


REQUIREMENTS = {"id": "requirements", "agent": "requirements", "exit_gates": ["stories_have_acceptance_criteria"]}
PLAN = '''KIND, SCOPE = "feat", "links"
EDITS = [{"file": "app.py", "find": "LIMIT = 1\\n", "replace": "LIMIT = 2\\n"}]
'''


@pytest.fixture
def delivery(tmp_path: Path) -> dict[str, Path]:
    """A source repo, a scenario that changes it through the development agent, and a promotion target."""
    source = make_repo(tmp_path / "source", {"app.py": "LIMIT = 1\n"})
    target = tmp_path / "target"
    subprocess.run(["git", "clone", "-q", str(source), str(target)], check=True)
    git(target, "config", "user.name", "Release Manager")      # a real target repository has an identity
    git(target, "config", "user.email", "rm@example.invalid")
    changes = tmp_path / "changes"
    changes.mkdir()
    (changes / "max_clicks.py").write_text(PLAN)
    sc = write_json(tmp_path / "scenario.json", {
        "name": "cli-delivery",
        "workspace": {"seed": "baseline", "ref": "HEAD", "paths": ["app.py"], "source": str(source)},
        "inputs": {"requirements": {"requirement": "Add a max click limit per link.",
                                    "existing_features": ["analytics"]}},          # "click" also matches analytics
        "stages": [REQUIREMENTS, {"id": "development", "agent": "development", "depends_on": ["requirements"],
                                  "mutates_workspace": True,
                                  "params": {"mode": "patch", "changes_dir": str(changes)}}]})
    return {"scenario": sc, "target": target, "run": tmp_path / "run", "source": source}


def test_run_verify_and_promote(delivery: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    run = delivery["run"]
    assert cli.main(["run", str(delivery["scenario"]), "--run-dir", str(run)]) == 0
    assert '"status": "COMPLETED"' in capsys.readouterr().out and (run / "run-report.md").exists()
    assert cli.main(["verify", str(run)]) == 0
    Workspace(run / "workspace").git("tag", "-a", "v1.2.3", "-m", "rc")         # a release tag travels with it
    assert cli.main(["promote", str(run), "--approver", "jane", "--target", str(delivery["target"])]) == 0
    target = delivery["target"]
    assert (target / "app.py").read_text() == "LIMIT = 2\n"
    assert git(target, "log", "-1", "--format=%s") == "feat(links): cap how many times a link can be used (US-02)"
    assert git(target, "tag") == "v1.2.3"
    promoted = [json.loads(x) for x in (run / "audit.jsonl").read_text().splitlines()][-1]
    assert promoted["event"] == "run.promoted" and promoted["actor"] == "jane"
    assert cli.main(["verify", str(run)]) == 0                                  # the promotion is audited too


def test_promote_without_a_release_tag(delivery: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["run", str(delivery["scenario"]), "--run-dir", str(delivery["run"])])
    assert cli.main(["promote", str(delivery["run"]), "--approver", "jane", "--target", str(delivery["target"])]) == 0
    assert capsys.readouterr().out.strip().endswith("promoted 1 commits, approved by jane")
    assert git(delivery["target"], "tag") == ""


def test_promote_refuses_unsafe_situations(delivery: dict[str, Path], tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    run, target = delivery["run"], delivery["target"]
    cli.main(["run", str(delivery["scenario"]), "--run-dir", str(run)])
    (target / "scratch.txt").write_text("half-finished local work")
    assert cli.main(["promote", str(run), "--approver", "jane", "--target", str(target)]) == 1
    assert "uncommitted changes" in capsys.readouterr().out
    (target / "scratch.txt").unlink()
    (target / "app.py").write_text("LIMIT = 99\n")                              # target diverged: conflict
    git(target, "commit", "-qam", "chore: diverge")
    assert cli.main(["promote", str(run), "--approver", "jane", "--target", str(target)]) == 1
    assert "repository left unchanged" in capsys.readouterr().out
    assert (target / "app.py").read_text() == "LIMIT = 99\n" and not (target / ".git" / "rebase-apply").exists()


def test_promote_refuses_incomplete_tampered_or_empty_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paused = write_json(tmp_path / "paused.json", {
        "name": "paused", "inputs": {"requirements": {"requirement": "shorten links"}},
        "stages": [{**REQUIREMENTS, "requires_approval": True}]})
    assert cli.main(["run", str(paused), "--run-dir", str(tmp_path / "p")]) == 3            # PAUSED
    assert cli.main(["promote", str(tmp_path / "p"), "--approver", "jane"]) == 1
    assert "run status is PAUSED" in capsys.readouterr().out
    empty = write_json(tmp_path / "empty.json", {"name": "empty", "stages": [REQUIREMENTS],
                                                 "inputs": {"requirements": {"requirement": "shorten"}}})
    assert cli.main(["run", str(empty), "--run-dir", str(tmp_path / "e")]) == 0
    target = make_repo(tmp_path / "t", {"x.txt": "x"})
    assert cli.main(["promote", str(tmp_path / "e"), "--approver", "jane", "--target", str(target)]) == 1
    assert "nothing to promote" in capsys.readouterr().out
    audit = tmp_path / "e" / "audit.jsonl"
    audit.write_text(audit.read_text().replace('"run.start"', '"run.forged"'))
    assert cli.main(["verify", str(tmp_path / "e")]) == 2
    assert cli.main(["promote", str(tmp_path / "e"), "--approver", "jane", "--target", str(target)]) == 2
    assert "audit chain broken" in capsys.readouterr().out


def test_pause_then_resume_with_recorded_decision(tmp_path: Path) -> None:
    sc = write_json(tmp_path / "s.json", {"name": "resume", "stages": [{**REQUIREMENTS, "requires_approval": True}],
                                          "inputs": {"requirements": {"requirement": "shorten links"}}})
    assert cli.main(["run", str(sc), "--run-dir", str(tmp_path / "r")]) == 3
    decisions = write_json(tmp_path / "a.json", {"requirements#1": {"status": "approved", "approver": "priya"}})
    assert cli.main(["resume", str(tmp_path / "r"), "--approvals", str(decisions)]) == 0
    state = json.loads((tmp_path / "r" / "state.json").read_text())
    assert state["run_status"] == "COMPLETED" and state["approvals_log"][0]["approver"] == "priya"


def test_interactive_approval_and_failed_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sc = write_json(tmp_path / "s.json", {"name": "ask", "inputs": {"requirements": {"requirement": "shorten links"}},
                                          "stages": [{**REQUIREMENTS, "requires_approval": True}]})
    monkeypatch.setattr("sys.stdin", io.StringIO("p\n"))                          # the human types "p" (pause)
    assert cli.main(["run", str(sc), "--run-dir", str(tmp_path / "i"), "--interactive"]) == 3
    bad = write_json(tmp_path / "bad.json", {"name": "bad", "stages": [{**REQUIREMENTS, "critical": False}],
                                             "inputs": {"requirements": {"requirement": "nothing known"}}})
    assert cli.main(["run", str(bad), "--run-dir", str(tmp_path / "f")]) == 1       # no stories -> blocked by CMP-002


def test_stop_sets_the_kill_switch(tmp_path: Path) -> None:
    assert cli.main(["stop", str(tmp_path), "--reason", "incident"]) == 0
    assert (tmp_path / "STOP").read_text() == "incident"


def test_default_run_dir_is_under_the_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sc = write_json(tmp_path / "s.json", {"name": "defaults", "inputs": {"requirements": {"requirement": "shorten"}},
                                          "stages": [REQUIREMENTS]})
    monkeypatch.setattr(cli, "REPO", tmp_path)
    assert cli.main(["run", str(sc)]) == 0
    assert [p.name.startswith("defaults-") for p in (tmp_path / "runs").iterdir()] == [True]


def test_scenario_must_declare_its_parts(tmp_path: Path) -> None:
    from sdlc import scenario
    with pytest.raises(ValueError, match="scenario missing 'stages'"):
        scenario.load(write_json(tmp_path / "x.json", {"name": "x", "inputs": {}}))
