"""Evaluation layer: metrics, evaluator, and curated datasets.

Public API
----------
- :mod:`evaluation.metrics` — 7 rule-based quality metrics
- :mod:`evaluation.evaluator` — orchestration + database persistence
- :mod:`evaluation.datasets` — curated eval cases for testing
"""

from evaluation.metrics import (
    MetricResult,
    hallucination_rate,
    issue_relevance,
    duplicate_rate,
    severity_consistency,
    completeness,
    markdown_formatting,
    overall_confidence,
    compute_all_metrics,
)
from evaluation.evaluator import (
    scanner_findings_to_dicts,
    evaluate_review,
    evaluate_from_state,
    evaluate_and_store,
)
from evaluation.datasets import (
    EvalCase,
    EVAL_DATASETS,
    get_dataset,
    get_dataset_names,
)

__all__ = [
    # metrics
    "MetricResult",
    "hallucination_rate",
    "issue_relevance",
    "duplicate_rate",
    "severity_consistency",
    "completeness",
    "markdown_formatting",
    "overall_confidence",
    "compute_all_metrics",
    # evaluator
    "scanner_findings_to_dicts",
    "evaluate_review",
    "evaluate_from_state",
    "evaluate_and_store",
    # datasets
    "EvalCase",
    "EVAL_DATASETS",
    "get_dataset",
    "get_dataset_names",
]
