"""Curated evaluation datasets for CodeGuardian AI.

Each dataset is a self-contained test case with a known code diff,
expected issue categories, and (optionally) expected scanner findings.
These are used to evaluate review quality without requiring a live LLM
or GitHub connection.

Usage::

    from evaluation.datasets import EVAL_DATASETS, get_dataset

    for ds in EVAL_DATASETS:
        print(ds["name"], ds["expected_categories"])

    ds = get_dataset("sql_injection")
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "EvalCase",
    "EVAL_DATASETS",
    "get_dataset",
    "get_dataset_names",
]


# ════════════════════════════════════════════════════════════════════
#  Type alias
# ════════════════════════════════════════════════════════════════════

#: A single evaluation case dict.
EvalCase = dict[str, Any]


# ════════════════════════════════════════════════════════════════════
#  Curated datasets
# ════════════════════════════════════════════════════════════════════

_SQL_INJECTION_DIFF = """\
--- a/app.py
+++ b/app.py
@@ -10,6 +10,12 @@
 def get_user(username):
+    query = "SELECT * FROM users WHERE name = '" + username + "'"
+    cursor.execute(query)
+    return cursor.fetchall()
"""

_SQL_INJECTION_SCANNER = [
    {
        "scanner": "bandit",
        "rule_id": "B608",
        "severity": "HIGH",
        "file": "app.py",
        "line": 12,
        "message": "Possible SQL injection vector through string-based query construction",
    },
]

_SQL_INJECTION_REPORT = """\
# Code Review Report

## Security Issues

- **SQL Injection** in `app.py:12` — user input concatenated directly into query
  - Severity: HIGH
  - Recommendation: Use parameterized queries

```python
# Bad
query = "SELECT * FROM users WHERE name = '" + username + "'"

# Good
cursor.execute("SELECT * FROM users WHERE name = ?", (username,))
```

## Summary

- 1 security finding (HIGH)
- Verdict: REQUEST_CHANGES
"""

_N_PLUS_1_DIFF = """\
--- a/models.py
+++ b/models.py
@@ -20,6 +20,14 @@
 def list_orders():
+    orders = Order.query.all()
+    for order in orders:
+        print(order.customer.name)
+        print(order.items)
"""

_N_PLUS_1_SCANNER = [
    {
        "scanner": "semgrep",
        "rule_id": "python.performance.n-plus-one",
        "severity": "MEDIUM",
        "file": "models.py",
        "line": 23,
        "message": "N+1 query detected: accessing relationship inside loop",
    },
]

_N_PLUS_1_REPORT = """\
# Code Review Report

## Performance Issues

- **N+1 Query** in `models.py:23` — accessing `order.customer.name` inside a loop
  - Severity: MEDIUM
  - Recommendation: Use eager loading (`joinedload` or `selectinload`)

```python
# Bad
for order in orders:
    print(order.customer.name)

# Good
orders = Order.query.options(joinedload(Order.customer)).all()
```

## Summary

- 1 performance finding (MEDIUM)
- Verdict: APPROVE
"""

_HARDcoded_SECRET_DIFF = """\
--- a/config.py
+++ b/config.py
@@ -5,6 +5,8 @@
-DEBUG = False
+DEBUG = True
+API_KEY = "sk-1234567890abcdef"
+SECRET = "my-super-secret-key"
"""

_HARDcoded_SECRET_SCANNER = [
    {
        "scanner": "bandit",
        "rule_id": "B105",
        "severity": "HIGH",
        "file": "config.py",
        "line": 7,
        "message": "Possible hardcoded password assigned to variable",
    },
    {
        "scanner": "semgrep",
        "rule_id": "generic.secrets.security.detected",
        "severity": "HIGH",
        "file": "config.py",
        "line": 8,
        "message": "Hardcoded secret detected in source code",
    },
]

_HARDcoded_SECRET_REPORT = """\
# Code Review Report

## Security Issues

- **Hardcoded Secret** in `config.py:7-8` — API key and secret stored in source
  - Severity: HIGH
  - Recommendation: Use environment variables or a secrets manager

```python
# Bad
API_KEY = "sk-1234567890abcdef"

# Good
import os
API_KEY = os.environ["API_KEY"]
```

## Summary

- 2 security findings (HIGH)
- Verdict: BLOCK_MERGE
"""

_COMPLEX_FUNCTION_DIFF = """\
--- a/processor.py
+++ b/processor.py
@@ -1,3 +1,45 @@
 def process(data):
+    result = []
+    if data and len(data) > 0:
+        for item in data:
+            if item and isinstance(item, dict):
+                if "type" in item:
+                    if item["type"] == "A":
+                        if "value" in item:
+                            result.append(item["value"] * 2)
+                        else:
+                            result.append(0)
+                    elif item["type"] == "B":
+                        if "value" in item:
+                            result.append(item["value"] * 3)
+                        else:
+                            result.append(0)
+                    else:
+                        result.append(None)
+                else:
+                    result.append(None)
+            else:
+                result.append(None)
+    return result
"""

_COMPLEX_FUNCTION_SCANNER = [
    {
        "scanner": "ruff",
        "rule_id": "C901",
        "severity": "MEDIUM",
        "file": "processor.py",
        "line": 2,
        "message": "Function is too complex (complexity 12)",
    },
]

_COMPLEX_FUNCTION_REPORT = """\
# Code Review Report

## Quality Issues

- **High Complexity** in `processor.py:2` — function has deeply nested conditionals
  - Severity: MEDIUM
  - Recommendation: Extract nested logic into helper functions

```python
# Bad — deeply nested if/else
def process(data):
    result = []
    for item in data:
        if item and isinstance(item, dict):
            if "type" in item:
                ...

# Good — use dispatch dict
def process(data):
    handlers = {"A": _handle_a, "B": _handle_b}
    return [handlers.get(item.get("type"), _default)(item) for item in data]
```

## Summary

- 1 quality finding (MEDIUM)
- Verdict: REQUEST_CHANGES
"""

_NEW_MODULE_DIFF = """\
--- /dev/null
+++ b/services/payment.py
@@ -0,0 +1,20 @@
+import stripe
+from database import db
+from models import Order
+
+class PaymentService:
+    def charge(self, order_id):
+        order = Order.query.get(order_id)
+        stripe.Charge.create(
+            amount=order.total,
+            currency="usd",
+        )
+        order.status = "paid"
+        db.session.commit()
"""

_NEW_MODULE_SCANNER = [
    {
        "scanner": "semgrep",
        "rule_id": "python.lang.security.audit.dangerous-subprocess",
        "severity": "LOW",
        "file": "services/payment.py",
        "line": 5,
        "message": "Service class without error handling",
    },
]

_NEW_MODULE_REPORT = """\
# Code Review Report

## Architecture Issues

- **Missing Error Handling** in `services/payment.py:5` — payment service has no try/except
  - Severity: LOW
  - Recommendation: Wrap external API calls in error handling

```python
# services/payment.py
def charge(amount):
    return api.charge(amount)
```

## Security Issues

- **No Input Validation** — `order_id` is not validated before use
  - Severity: MEDIUM
  - Recommendation: Validate order_id is a positive integer

## Summary

- 1 architecture finding (LOW)
- 1 security finding (MEDIUM)
- Verdict: REQUEST_CHANGES
"""

_CLEAN_DIFF = """\
--- a/utils.py
+++ b/utils.py
@@ -10,6 +10,8 @@
 def format_name(name):
-    return name.strip()
+    name = name.strip()
+    if not name:
+        return "Unknown"
+    return name.title()
"""

_CLEAN_SCANNER: list[dict[str, Any]] = []

_CLEAN_REPORT = """\
# Code Review Report

## Summary

No issues found. The changes are clean and well-structured.

```python
# utils.py
def format_name(name):
    name = name.strip()
    if not name:
        return "Unknown"
    return name.title()
```

- Verdict: APPROVE
"""


# ════════════════════════════════════════════════════════════════════
#  Dataset registry
# ════════════════════════════════════════════════════════════════════

EVAL_DATASETS: list[EvalCase] = [
    {
        "name": "sql_injection",
        "description": "String-concatenated SQL query — should trigger security finding",
        "code_diff": _SQL_INJECTION_DIFF,
        "scanner_findings": _SQL_INJECTION_SCANNER,
        "expected_categories": ["security"],
        "expected_min_findings": 1,
        "sample_report": _SQL_INJECTION_REPORT,
    },
    {
        "name": "n_plus_one_query",
        "description": "ORM relationship access inside a loop — should trigger performance finding",
        "code_diff": _N_PLUS_1_DIFF,
        "scanner_findings": _N_PLUS_1_SCANNER,
        "expected_categories": ["performance"],
        "expected_min_findings": 1,
        "sample_report": _N_PLUS_1_REPORT,
    },
    {
        "name": "hardcoded_secret",
        "description": "API key and secret in source code — should trigger security finding",
        "code_diff": _HARDcoded_SECRET_DIFF,
        "scanner_findings": _HARDcoded_SECRET_SCANNER,
        "expected_categories": ["security"],
        "expected_min_findings": 1,
        "sample_report": _HARDcoded_SECRET_REPORT,
    },
    {
        "name": "complex_function",
        "description": "Deeply nested function — should trigger quality/complexity finding",
        "code_diff": _COMPLEX_FUNCTION_DIFF,
        "scanner_findings": _COMPLEX_FUNCTION_SCANNER,
        "expected_categories": ["quality"],
        "expected_min_findings": 1,
        "sample_report": _COMPLEX_FUNCTION_REPORT,
    },
    {
        "name": "new_module",
        "description": "New module with service class — should trigger architecture finding",
        "code_diff": _NEW_MODULE_DIFF,
        "scanner_findings": _NEW_MODULE_SCANNER,
        "expected_categories": ["architecture", "security"],
        "expected_min_findings": 1,
        "sample_report": _NEW_MODULE_REPORT,
    },
    {
        "name": "clean_code",
        "description": "Clean, well-structured change — should produce no findings",
        "code_diff": _CLEAN_DIFF,
        "scanner_findings": _CLEAN_SCANNER,
        "expected_categories": [],
        "expected_min_findings": 0,
        "sample_report": _CLEAN_REPORT,
    },
]


# ════════════════════════════════════════════════════════════════════
#  Lookup helpers
# ════════════════════════════════════════════════════════════════════


def get_dataset(name: str) -> EvalCase | None:
    """Return the eval case with the given name, or ``None``."""
    for ds in EVAL_DATASETS:
        if ds["name"] == name:
            return ds
    return None


def get_dataset_names() -> list[str]:
    """Return a list of all dataset names."""
    return [ds["name"] for ds in EVAL_DATASETS]
