"""Semgrep scanner runner.

Runs ``semgrep`` as a subprocess against the changed files and parses
the JSON output into :class:`ScannerFinding` objects.

Semgrep is a fast, pattern-based multi-language static analysis tool.
It is best at detecting known vulnerability patterns and framework
misconfigurations.

Usage::

    from scanners.semgrep_runner import run_semgrep

    result = run_semgrep("/path/to/checkout", ["src/auth.py", "src/db.py"])
    for finding in result.findings:
        print(finding.severity, finding.rule_id, finding.message)
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Optional

from scanners.parser import ScannerFinding, ScannerResult

__all__ = ["run_semgrep"]

_log = logging.getLogger("codeguardian.scanners.semgrep")

# Map Semgrep severity strings to our canonical levels.
_SEVERITY_MAP = {
    "error": "HIGH",
    "warning": "MEDIUM",
    "info": "INFO",
}


def run_semgrep(
    repo_path: str,
    changed_files: list[str],
    *,
    config: str = "auto",
    timeout: int = 120,
) -> ScannerResult:
    """Run Semgrep against the given files and return parsed findings.

    Parameters
    ----------
    repo_path:
        Path to the repository checkout (working directory for semgrep).
    changed_files:
        List of file paths (relative to *repo_path*) to scan.
    config:
        Semgrep config to use — ``"auto"`` (default) uses the registry,
        or a path to rules file.
    timeout:
        Maximum seconds to wait for semgrep before killing it.

    Returns
    -------
    ScannerResult
        Parsed findings.  If semgrep is not installed or fails, an empty
        result is returned with the error captured in ``raw_outputs``.
    """
    if not changed_files:
        _log.debug("No changed files provided — skipping semgrep.")
        return ScannerResult()

    cmd = [
        "semgrep",
        "--json",
        "--quiet",
        "--config",
        config,
        *changed_files,
    ]

    _log.info("Running semgrep on %d file(s) in %s", len(changed_files), repo_path)

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
        _log.error("semgrep binary not found — is it installed?")
        return ScannerResult(raw_outputs={"semgrep_error": "semgrep not found"})
    except subprocess.TimeoutExpired:
        _log.error("semgrep timed out after %ds", timeout)
        return ScannerResult(raw_outputs={"semgrep_error": f"timeout after {timeout}s"})

    raw_json = proc.stdout
    findings = _parse_semgrep_json(raw_json)

    _log.info(
        "semgrep completed with %d finding(s) (exit code %d)",
        len(findings),
        proc.returncode,
    )

    return ScannerResult(
        findings=findings,
        raw_outputs={"semgrep": raw_json},
    )


def _parse_semgrep_json(raw_json: str) -> list[ScannerFinding]:
    """Parse Semgrep JSON output into :class:`ScannerFinding` list.

    Parameters
    ----------
    raw_json:
        The raw JSON string from ``semgrep --json``.

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
        _log.error("Failed to parse semgrep JSON: %s", exc)
        return []

    findings: list[ScannerFinding] = []
    for result in data.get("results", []):
        check_id: str = result.get("check_id", "unknown")
        raw_severity: str = result.get("extra", {}).get("severity", "info")
        message: str = result.get("extra", {}).get("message", "")
        file_path: str = result.get("path", "")
        line: int = result.get("start", {}).get("line", 0)

        findings.append(
            ScannerFinding(
                scanner="semgrep",
                rule_id=check_id,
                severity=_SEVERITY_MAP.get(raw_severity.lower(), "INFO"),
                file=file_path,
                line=line,
                message=message,
            )
        )

    return findings
