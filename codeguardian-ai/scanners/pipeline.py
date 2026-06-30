"""Static analysis pipeline orchestrator.

Runs Semgrep, Bandit, and Ruff against the changed files (respecting
the ``enable_*`` config flags) and merges all findings into a single
:class:`ScannerResult`.  This is the single entry point the LangGraph
workflow calls before invoking AI agents.

Usage::

    from scanners.pipeline import run_static_analysis

    result = run_static_analysis("/path/to/checkout", ["src/auth.py"])
    context = format_as_context(result)  # inject into agent prompts
"""

from __future__ import annotations

import logging

from config import settings
from scanners.parser import ScannerResult, format_as_context, merge_results

__all__ = ["run_static_analysis"]

_log = logging.getLogger("codeguardian.scanners.pipeline")


def run_static_analysis(
    repo_path: str,
    changed_files: list[str],
) -> ScannerResult:
    """Run all enabled static-analysis scanners and merge results.

    Scanners are executed sequentially (they are fast subprocesses).
    Each scanner is independent — if one fails, the others still run.

    Parameters
    ----------
    repo_path:
        Path to the repository checkout.
    changed_files:
        List of file paths (relative to *repo_path*) to scan.

    Returns
    -------
    ScannerResult
        Merged result from all enabled scanners, sorted by severity.
    """
    results: list[ScannerResult] = []

    if settings.enable_semgrep:
        _log.info("Running Semgrep scanner...")
        from scanners.semgrep_runner import run_semgrep

        results.append(run_semgrep(repo_path, changed_files))
    else:
        _log.debug("Semgrep disabled — skipping.")

    if settings.enable_bandit:
        _log.info("Running Bandit scanner...")
        from scanners.bandit_runner import run_bandit

        results.append(run_bandit(repo_path, changed_files))
    else:
        _log.debug("Bandit disabled — skipping.")

    if settings.enable_ruff:
        _log.info("Running Ruff scanner...")
        from scanners.ruff_runner import run_ruff

        results.append(run_ruff(repo_path, changed_files))
    else:
        _log.debug("Ruff disabled — skipping.")

    merged = merge_results(results)
    _log.info(
        "Static analysis complete: %d total finding(s) from %d scanner(s).",
        merged.total_findings,
        len(results),
    )
    return merged


def get_context_block(result: ScannerResult) -> str:
    """Convenience wrapper to format a :class:`ScannerResult` for prompts.

    Parameters
    ----------
    result:
        The merged scanner result.

    Returns
    -------
    str
        Markdown-formatted context block for agent prompts.
    """
    return format_as_context(result)
