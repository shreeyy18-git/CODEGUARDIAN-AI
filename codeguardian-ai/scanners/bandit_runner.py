"""Bandit scanner runner.

Runs ``bandit`` as a subprocess against the changed Python files and
parses the JSON output into :class:`ScannerFinding` objects.

Bandit is a Python-specific AST-based security scanner.  It detects
common security issues such as ``exec()``, hardcoded passwords, weak
crypto, and shell injection.

Usage::

    from scanners.bandit_runner import run_bandit

    result = run_bandit("/path/to/checkout", ["src/auth.py", "src/db.py"])
    for finding in result.findings:
        print(finding.severity, finding.rule_id, finding.message)
"""

from __future__ import annotations

import json
import logging
import subprocess

from scanners.parser import ScannerFinding, ScannerResult

__all__ = ["run_bandit"]

_log = logging.getLogger("codeguardian.scanners.bandit")

# Map Bandit severity levels (LOW/MEDIUM/HIGH) to canonical levels.
_SEVERITY_MAP = {
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
}


def run_bandit(
    repo_path: str,
    changed_files: list[str],
    *,
    timeout: int = 60,
) -> ScannerResult:
    """Run Bandit against the given Python files and return parsed findings.

    Only ``.py`` files are scanned — Bandit does not understand other
    languages.

    Parameters
    ----------
    repo_path:
        Path to the repository checkout (working directory for bandit).
    changed_files:
        List of file paths (relative to *repo_path*) to scan.
    timeout:
        Maximum seconds to wait for bandit before killing it.

    Returns
    -------
    ScannerResult
        Parsed findings.  If bandit is not installed or fails, an empty
        result is returned with the error captured in ``raw_outputs``.
    """
    # Bandit only scans Python files.
    py_files = [f for f in changed_files if f.endswith(".py")]
    if not py_files:
        _log.debug("No Python files to scan — skipping bandit.")
        return ScannerResult()

    cmd = [
        "bandit",
        "--format",
        "json",
        "--quiet",
        "--skip",
        "B101",  # Skip "assert" warnings (common in tests)
        *py_files,
    ]

    _log.info("Running bandit on %d Python file(s) in %s", len(py_files), repo_path)

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
        _log.error("bandit binary not found — is it installed?")
        return ScannerResult(raw_outputs={"bandit_error": "bandit not found"})
    except subprocess.TimeoutExpired:
        _log.error("bandit timed out after %ds", timeout)
        return ScannerResult(raw_outputs={"bandit_error": f"timeout after {timeout}s"})

    # Bandit exits with non-zero when it finds issues — that's expected.
    raw_json = proc.stdout
    findings = _parse_bandit_json(raw_json)

    _log.info(
        "bandit completed with %d finding(s) (exit code %d)",
        len(findings),
        proc.returncode,
    )

    return ScannerResult(
        findings=findings,
        raw_outputs={"bandit": raw_json},
    )


def _parse_bandit_json(raw_json: str) -> list[ScannerFinding]:
    """Parse Bandit JSON output into :class:`ScannerFinding` list.

    Parameters
    ----------
    raw_json:
        The raw JSON string from ``bandit --format json``.

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
        _log.error("Failed to parse bandit JSON: %s", exc)
        return []

    findings: list[ScannerFinding] = []
    for result in data.get("results", []):
        test_id: str = result.get("test_id", "unknown")
        issue_severity: str = result.get("issue_severity", "LOW")
        issue_text: str = result.get("issue_text", "")
        filename: str = result.get("filename", "")
        line_number: int = result.get("line_number", 0)

        findings.append(
            ScannerFinding(
                scanner="bandit",
                rule_id=test_id,
                severity=_SEVERITY_MAP.get(issue_severity.lower(), "LOW"),
                file=filename,
                line=line_number,
                message=issue_text,
            )
        )

    return findings
