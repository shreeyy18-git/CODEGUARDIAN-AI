"""Ruff scanner runner.

Runs ``ruff`` as a subprocess against the changed Python files and
parses the JSON output into :class:`ScannerFinding` objects.

Ruff is an extremely fast Python linter (written in Rust) that
replaces flake8, isort, and pyupgrade.  It catches style violations,
unused imports, and common anti-patterns.

Usage::

    from scanners.ruff_runner import run_ruff

    result = run_ruff("/path/to/checkout", ["src/auth.py", "src/db.py"])
    for finding in result.findings:
        print(finding.severity, finding.rule_id, finding.message)
"""

from __future__ import annotations

import json
import logging
import subprocess

from scanners.parser import ScannerFinding, ScannerResult

__all__ = ["run_ruff"]

_log = logging.getLogger("codeguardian.scanners.ruff")

# Ruff rule prefixes → canonical severity.
# Ruff doesn't have a severity field, so we infer from the rule code.
_RULE_SEVERITY = {
    "E": "LOW",      # pycodestyle errors (style)
    "W": "LOW",      # pycodestyle warnings
    "F": "MEDIUM",   # pyflakes (unused imports, undefined names)
    "S": "HIGH",     # flake8-bandit (security)
    "B": "MEDIUM",   # flake8-bugbear
    "C4": "LOW",     # flake8-comprehensions
    "UP": "LOW",     # pyupgrade
    "I": "LOW",      # isort
    "N": "LOW",      # pep8-naming
    "D": "LOW",      # pydocstyle
    "ANN": "LOW",    # flake8-annotations
    "PL": "MEDIUM",  # pylint
    "RUF": "MEDIUM", # ruff-specific
}


def _infer_severity(rule_id: str) -> str:
    """Infer a canonical severity from a Ruff rule code.

    Parameters
    ----------
    rule_id:
        Ruff rule code (e.g. ``"F401"``, ``"S101"``, ``"E501"``).

    Returns
    -------
    str
        One of ``CRITICAL``, ``HIGH``, ``MEDIUM``, ``LOW``, ``INFO``.
    """
    for prefix, severity in _RULE_SEVERITY.items():
        if rule_id.startswith(prefix):
            return severity
    return "INFO"


def run_ruff(
    repo_path: str,
    changed_files: list[str],
    *,
    timeout: int = 30,
) -> ScannerResult:
    """Run Ruff against the given Python files and return parsed findings.

    Only ``.py`` files are scanned — Ruff is Python-only.

    Parameters
    ----------
    repo_path:
        Path to the repository checkout (working directory for ruff).
    changed_files:
        List of file paths (relative to *repo_path*) to scan.
    timeout:
        Maximum seconds to wait for ruff before killing it.

    Returns
    -------
    ScannerResult
        Parsed findings.  If ruff is not installed or fails, an empty
        result is returned with the error captured in ``raw_outputs``.
    """
    py_files = [f for f in changed_files if f.endswith(".py")]
    if not py_files:
        _log.debug("No Python files to scan — skipping ruff.")
        return ScannerResult()

    cmd = [
        "ruff",
        "check",
        "--output-format",
        "json",
        "--no-cache",
        *py_files,
    ]

    _log.info("Running ruff on %d Python file(s) in %s", len(py_files), repo_path)

    try:
        proc = subprocess.run(  # noqa: S603 — trusted binary path
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        _log.error("ruff binary not found — is it installed?")
        return ScannerResult(raw_outputs={"ruff_error": "ruff not found"})
    except subprocess.TimeoutExpired:
        _log.error("ruff timed out after %ds", timeout)
        return ScannerResult(raw_outputs={"ruff_error": f"timeout after {timeout}s"})

    # Ruff exits with 1 when issues are found — that's expected.
    raw_json = proc.stdout
    findings = _parse_ruff_json(raw_json)

    _log.info(
        "ruff completed with %d finding(s) (exit code %d)",
        len(findings),
        proc.returncode,
    )

    return ScannerResult(
        findings=findings,
        raw_outputs={"ruff": raw_json},
    )


def _parse_ruff_json(raw_json: str) -> list[ScannerFinding]:
    """Parse Ruff JSON output into :class:`ScannerFinding` list.

    Parameters
    ----------
    raw_json:
        The raw JSON string from ``ruff check --output-format json``.

    Returns
    -------
    list[ScannerFinding]
        Parsed findings.  Returns an empty list on parse failure.
    """
    if not raw_json.strip():
        return []

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        _log.error("Failed to parse ruff JSON: %s", exc)
        return []

    # Ruff JSON output is a list of finding objects.
    if isinstance(data, dict):
        data = data.get("results", [])

    findings: list[ScannerFinding] = []
    for result in data:
        code: str = result.get("code", "unknown")
        message: str = result.get("message", "")
        filename: str = result.get("filename", "")
        location = result.get("location", {})
        line: int = location.get("row", 0) if isinstance(location, dict) else 0

        findings.append(
            ScannerFinding(
                scanner="ruff",
                rule_id=code,
                severity=_infer_severity(code),
                file=filename,
                line=line,
                message=message,
            )
        )

    return findings
