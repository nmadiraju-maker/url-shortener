"""Policy-as-code guardrail tests (security, compliance, change control)."""
from __future__ import annotations

import pytest

from sdlc.policy import ALLOW, BLOCK, ESCALATE, PolicyConfig, PolicyEngine

P = PolicyEngine()


@pytest.mark.parametrize("src,rule", [
    ("eval('1')", "SEC-001"), ("import os\nos.system('ls')", "SEC-001"),
    ("import subprocess\nsubprocess.run('ls', shell=True)", "SEC-001"),
    ("api_key = 'abcdefghijklmnop'", "SEC-002"), ("x = 'AKIAABCDEFGHIJKLMNOP'", "SEC-002"),
    ("c.execute(f'SELECT {x}')", "SEC-003"), ("c.execute('SELECT ' + x)", "SEC-003"),
    ("c.executescript('DROP {}'.format(t))", "SEC-003"), ("log.info('ip', extra={'client_ip': ip})", "CMP-001"),
    ("def broken(:\n", "QLT-001"),
])
def test_scan_code_blocks(src, rule):
    v = P.scan_code({"m.py": src})
    assert any(x.rule == rule and x.outcome == BLOCK for x in v), v


@pytest.mark.parametrize("src", [
    "c.execute('SELECT * FROM t WHERE a = ?', (a,))", "subprocess.run(['ls'], shell=False)", "c.execute()",
    "log.info('created', extra={'code': c})", "print(eval)", "(lambda: 1)()", "obj.method()",
])
def test_scan_code_allows_safe_code(src):
    assert P.scan_code({"m.py": src}) == []


def test_secret_scan_applies_to_non_python():
    assert P.scan_code({"config.env": "password = 'supersecretvalue1'"})[0].rule == "SEC-002"
    assert P.scan_code({"README.md": "eval('x')"}) == []


def test_change_control():
    v = P.check_changes({"sdlc/engine.py": "", "urlshort/storage.py": "", "urlshort/other.py": ""},
                        deleted=["tests/t.py"], allowed_scope=["urlshort/storage.py", "tests/*"], lines_changed=10)
    rules = {(x.rule, x.outcome) for x in v}
    assert ("CHG-001", BLOCK) in rules and ("CHG-002", ESCALATE) in rules and ("CHG-003", ESCALATE) in rules
    assert ("CHG-004", BLOCK) in rules
    big = PolicyEngine(PolicyConfig(max_changed_files=1)).check_changes({"a.py": "", "b.py": ""})
    assert big[0].rule == "CHG-005"
    assert P.check_changes({"docs/generated/x.json": "", "CHANGELOG.md": ""}, lines_changed=99999) == []


def test_document_compliance_and_report():
    assert P.check_requirements({"stories": []})[0].rule == "CMP-002"
    assert P.check_requirements({"stories": [{"id": "US-1", "acceptance_criteria": []}]})[0].location == "US-1"
    assert P.check_requirements({"stories": [{"id": "US-1", "acceptance_criteria": [1]}]}) == []
    missing = P.check_design({"api": []})
    assert {m.message.split("'")[1] for m in missing} == {"threat_model", "data_retention", "decisions"}
    assert P.report().outcome == ALLOW
    r = P.report(P.check_changes({"pyproject.toml": ""}))
    assert r.outcome == ESCALATE and r.to_dict()["violations"][0]["rule"] == "CHG-002" and r.feedback()


def test_deleting_non_test_file_is_not_a_test_deletion():
    assert P.check_changes({}, deleted=["urlshort/old.py"]) == []


@pytest.mark.parametrize("path", ["urlshort/storage.py", "urlshort/config.py", "urlshort/web/clientip.py",
                                  "urlshort/web/middleware.py", "Dockerfile", ".github/workflows/ci.yml",
                                  "pyproject.toml", "requirements-dev.txt"])
def test_security_boundaries_of_this_codebase_need_approval(path: str) -> None:
    (v,) = P.check_changes({path: ""})
    assert (v.rule, v.outcome) == ("CHG-002", ESCALATE)


@pytest.mark.parametrize("path", ["sdlc/policy.py", "sdlc/agents/review.py", ".git/config"])
def test_agents_cannot_touch_their_own_guardrails(path: str) -> None:
    assert any(v.rule == "CHG-001" and v.outcome == BLOCK for v in P.check_changes({path: ""}))
