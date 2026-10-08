"""Policy guardrails (policy-as-code) for security, compliance and change control.

Outcome per evaluation: ALLOW, ESCALATE (needs human approval) or BLOCK (stage attempt fails,
workspace rolls back, agent may retry with the violations as feedback).
"""
from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import asdict, dataclass, field
from typing import Any

ALLOW, ESCALATE, BLOCK = "allow", "escalate", "block"
_RANK = {ALLOW: 0, ESCALATE: 1, BLOCK: 2}

SECRET_PATTERNS = [
    re.compile(r"""(?i)\b(api_?key|secret|password|passwd|token)\b\s*[:=]\s*['"][^'"\s]{12,}['"]"""),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
]
DANGEROUS_CALLS = {"eval", "exec", "os.system", "pickle.loads", "marshal.loads", "os.popen"}
SQL_METHODS = {"execute", "executemany", "executescript"}
PII_NAMES = {"client_ip", "email", "password", "ssn", "raw_ip", "phone"}
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


@dataclass
class Violation:
    rule: str
    category: str      # security | compliance | change_control
    outcome: str       # escalate | block
    message: str
    location: str = ""


@dataclass
class PolicyConfig:
    # Security boundaries of this codebase: an agent touching any of these needs human approval.
    protected_paths: list[str] = field(default_factory=lambda: [
        "urlshort/storage.py",           # schema and migrations
        "urlshort/config.py",            # production safety checks, secret handling
        "urlshort/web/clientip.py",      # trusted-proxy logic (client IP spoofing boundary)
        "urlshort/web/middleware.py",    # security headers, error envelope
        "Dockerfile", ".github/workflows/*",
        "pyproject.toml", "requirements.txt", "requirements-dev.txt",
    ])
    # Agents may never modify their own guardrails or version-control internals.
    forbidden_paths: list[str] = field(default_factory=lambda: ["sdlc/*", "sdlc/**", ".git/*"])
    generated_paths: list[str] = field(default_factory=lambda: ["docs/generated/*", "CHANGELOG.md"])
    max_changed_files: int = 15
    max_changed_lines: int = 800


@dataclass
class PolicyReport:
    violations: list[Violation]

    @property
    def outcome(self) -> str:
        return max((v.outcome for v in self.violations), key=_RANK.__getitem__, default=ALLOW)

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "violations": [asdict(v) for v in self.violations]}

    def feedback(self) -> list[str]:
        return [f"[{v.rule}] {v.message} {v.location}".strip() for v in self.violations]


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else ""
        return f"{base}.{f.attr}" if base else f.attr
    return ""


def _is_formatted_sql(name: str, node: ast.Call) -> bool:
    """True when an execute()-style call receives an f-string, %/+ expression or str.format() result."""
    if name.split(".")[-1] not in SQL_METHODS or not node.args:
        return False
    first = node.args[0]
    if isinstance(first, (ast.JoinedStr, ast.BinOp)):
        return True
    return isinstance(first, ast.Call) and _call_name(first).endswith("format")


def _names_in(node: ast.AST) -> set[str]:
    out = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    out |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    out |= {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return out


class PolicyEngine:
    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    # ---------------- code (security + compliance) ----------------
    def scan_code(self, files: dict[str, str]) -> list[Violation]:
        out: list[Violation] = []
        for path, src in sorted(files.items()):
            for pat in SECRET_PATTERNS:
                if pat.search(src):
                    out.append(Violation("SEC-002", "security", BLOCK, "hard-coded secret detected", path))
            if not path.endswith(".py"):
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError as exc:
                out.append(Violation("QLT-001", "security", BLOCK, f"syntax error: {exc.msg}", f"{path}:{exc.lineno}"))
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node)
                loc = f"{path}:{node.lineno}"
                if name in DANGEROUS_CALLS:
                    out.append(Violation("SEC-001", "security", BLOCK, f"dangerous call '{name}'", loc))
                if name.startswith("subprocess.") and any(
                        k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                        for k in node.keywords):
                    out.append(Violation("SEC-001", "security", BLOCK, "subprocess with shell=True", loc))
                if _is_formatted_sql(name, node):
                    out.append(Violation("SEC-003", "security", BLOCK,
                                         "SQL built with string formatting; use parameters", loc))
                if name.split(".")[-1] in LOG_METHODS and name.split(".")[0] in {"log", "logger", "logging"}:
                    leaked = _names_in(node) & PII_NAMES
                    if leaked:
                        out.append(Violation("CMP-001", "compliance", BLOCK,
                                             f"PII in log statement: {sorted(leaked)}", loc))
        return out

    # ---------------- change control ----------------
    def check_changes(self, changed: dict[str, str], *, deleted: list[str] | None = None,
                      allowed_scope: list[str] | None = None, lines_changed: int = 0) -> list[Violation]:
        cfg, out = self.config, []
        deleted = deleted or []
        for path in sorted(set(changed) | set(deleted)):
            if any(fnmatch.fnmatch(path, p) for p in cfg.forbidden_paths):
                out.append(Violation("CHG-001", "change_control", BLOCK,
                                     "agents may not modify the orchestrator (their own guardrails) or VCS internals",
                                     path))
            elif any(fnmatch.fnmatch(path, p) for p in cfg.protected_paths):
                out.append(Violation("CHG-002", "change_control", ESCALATE,
                                     "protected path (security boundary, schema or build) changed; "
                                     "human approval required", path))
            if allowed_scope is not None and not any(fnmatch.fnmatch(path, p) for p in allowed_scope):
                out.append(Violation("CHG-003", "change_control", ESCALATE,
                                     "change outside approved impact scope", path))
        for path in deleted:
            if path.startswith("tests/"):
                out.append(Violation("CHG-004", "change_control", BLOCK, "test deletion is not permitted", path))
        authored = [p for p in set(changed) | set(deleted)
                    if not any(fnmatch.fnmatch(p, g) for g in cfg.generated_paths)]
        if authored and (len(authored) > cfg.max_changed_files or lines_changed > cfg.max_changed_lines):
            out.append(Violation("CHG-005", "change_control", ESCALATE,
                                 f"large change ({len(authored)} authored files, {lines_changed} lines)"))
        return out

    # ---------------- document compliance ----------------
    @staticmethod
    def check_requirements(req: dict[str, Any]) -> list[Violation]:
        out = [Violation("CMP-002", "compliance", BLOCK, "user story without acceptance criteria", s["id"])
               for s in req.get("stories", []) if not s.get("acceptance_criteria")]
        if not req.get("stories"):
            out.append(Violation("CMP-002", "compliance", BLOCK, "no user stories produced"))
        return out

    @staticmethod
    def check_design(design: dict[str, Any]) -> list[Violation]:
        required = {"threat_model", "data_retention", "api", "decisions"}
        missing = sorted(required - set(design))
        return [Violation("CMP-003", "compliance", BLOCK, f"design missing required section '{m}'") for m in missing]

    @staticmethod
    def report(*groups: list[Violation]) -> PolicyReport:
        return PolicyReport([v for g in groups for v in g])
