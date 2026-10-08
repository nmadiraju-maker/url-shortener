"""Git-backed workspace: every mutating stage is snapshotted so it can be rolled back atomically."""
from __future__ import annotations

import shutil
import subprocess
from functools import cache
from pathlib import Path

_ENV_ID = ["-c", "user.name=sdlc-dev-agent", "-c", "user.email=sdlc-bot@example.local"]


@cache
def git_executable() -> str:
    """Absolute path to git, resolved once: running a bare "git" would execute whatever comes first on
    PATH at the time of each call. Fails clearly if git is not installed."""
    path = shutil.which("git")
    if path is None:
        raise RuntimeError("git is required by the SDLC orchestrator but was not found on PATH")
    return path


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self.git("init", "-q", "-b", "main")
            self.git("commit", "-q", "--allow-empty", "-m", "chore: initialise workspace")

    def git(self, *args: str) -> str:
        # Argument list, no shell: nothing in args is interpreted by a shell.
        proc = subprocess.run([git_executable(), *_ENV_ID, *args], cwd=self.root, capture_output=True, text=True,
                              check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def snapshot(self) -> str:
        """Commit any stray changes then return HEAD as the restore point."""
        if self.git("status", "--porcelain"):
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "chore: checkpoint before stage")
        return self.head()

    def rollback(self, ref: str) -> None:
        self.git("reset", "-q", "--hard", ref)
        self.git("clean", "-q", "-fd")

    def commit(self, message: str) -> str | None:
        self.git("add", "-A")
        if not self.git("status", "--porcelain"):
            return None
        self.git("commit", "-q", "-m", message)
        return self.head()

    def changes_since(self, ref: str, exclude: tuple[str, ...] = ("docs/generated/", "CHANGELOG.md")
                      ) -> tuple[dict[str, str], list[str], int]:
        """Return (changed_files -> content, deleted_files, authored_lines_changed) vs ref, incl. uncommitted.

        Generated files (`exclude` prefixes) are reported but not counted towards lines changed."""
        self.git("add", "-A")
        names = self.git("diff", "--cached", "--name-status", ref).splitlines()
        changed, deleted = {}, []
        for line in names:
            status, path = line.split("\t", 1)
            if status.startswith("D"):
                deleted.append(path)
            else:
                changed[path] = (self.root / path).read_text()
        stat = self.git("diff", "--cached", "--numstat", ref).splitlines()
        lines = sum(int(a) + int(d) for a, d, p in (s.split("\t", 2) for s in stat)
                    if a.isdigit() and d.isdigit() and not p.startswith(exclude))
        self.git("reset", "-q")
        return changed, deleted, lines

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text()

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def python_files(self) -> dict[str, str]:
        return {str(p.relative_to(self.root)): p.read_text() for p in sorted(self.root.rglob("*.py"))
                if ".git" not in p.parts}
