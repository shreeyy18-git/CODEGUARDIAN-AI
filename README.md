# CodeGuardian AI

> **Multi-Agent AI Code Review Platform with Automated GitHub PR Gatekeeping.**

CodeGuardian AI listens for GitHub pull-request webhooks, runs a multi-agent
LLM review pipeline (powered by Groq + Gemini with automatic fallback),
cross-references findings with static-analysis scanners (Semgrep, Bandit,
Ruff), and posts a structured review comment + Check Run back to GitHub —
all in seconds.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Key Features](#key-features)
3. [Project Structure](#project-structure)
4. [Quick Start](#quick-start)
5. [Configuration](#configuration)
6. [API Reference](#api-reference)
7. [Docker Deployment](#docker-deployment)
8. [GitHub Webhook & Branch Protection Setup](#github-webhook--branch-protection-setup)
9. [Testing](#testing)
10. [Migration Notes (src/ → codeguardian-ai/)](#migration-notes-src--codeguardian-ai)

---

## Architecture Overview

```
┌──────────────┐     webhook      ┌──────────────────────────────────────────┐
│   GitHub     │ ───────────────► │           FastAPI Application            │
│  Pull Request│                  │  ┌────────────────────────────────────┐  │
│    Event     │ ◄─────────────── │  │  POST /github/webhook              │  │
└──────────────┘  comment +       │  │  (HMAC-SHA256 signature verify)    │  │
                  check run       │  └───────────────┬────────────────────┘  │
                                  │                  │ BackgroundTask         │
                                  │                  ▼                       │
                                  │  ┌────────────────────────────────────┐  │
                                  │  │      run_review_pipeline()         │  │
                                  │  │                                    │  │
                                  │  │  1. Start Check Run (in_progress)   │  │
                                  │  │  2. Fetch PR diff + changed files   │  │
                                  │  │  3. Run static analysis scanners    │  │
                                  │  │  4. LangGraph multi-agent workflow  │  │
                                  │  │     ├─ Security Agent               │  │
                                  │  │     ├─ Bug Agent                    │  │
                                  │  │     ├─ Performance Agent             │  │
                                  │  │     ├─ Quality Agent                │  │
                                  │  │     ├─ Architecture Agent            │  │
                                  │  │     ├─ Consensus Agent (merge)      │  │
                                  │  │     ├─ Risk Score Agent             │  │
                                  │  │     └─ Report Agent (markdown)      │  │
                                  │  │  5. Persist review + issues + eval  │  │
                                  │  │  6. Post / update PR comment         │  │
                                  │  │  7. Complete Check Run (pass/fail)  │  │
                                  │  └────────────────────────────────────┘  │
                                  │                                        │
                                  │  ┌──────────┐  ┌──────────────────┐     │
                                  │  │ SQLite   │  │  LangSmith       │     │
                                  │  │ Database │  │  Tracing (opt.)  │     │
                                  │  └──────────┘  └──────────────────┘     │
                                  └──────────────────────────────────────────┘
```

### LLM Routing

```
invoke_llm(system_prompt, user_prompt)
    │
    ├─ Try Groq (primary) ──► success? ──► return LLMResponse(fell_back=False)
    │
    └─ On failure ──► Try Gemini (fallback) ──► success?
                         ├─ Yes ──► return LLMResponse(fell_back=True)
                         └─ No   ──► raise RuntimeError
```

### Risk Scoring

| Overall Score | Verdict           | Check Run Conclusion |
|---------------|-------------------|----------------------|
| ≥ 0.8         | `APPROVE`         | ✅ success           |
| ≥ 0.4         | `REQUEST_CHANGES` | ⚠️ failure           |
| < 0.4         | `BLOCK_MERGE`     | ❌ failure           |

Formula: `overall = 0.5 × security + 0.3 × maintainability + 0.2 × performance`

---

## Key Features

- **8 Specialized Agents** orchestrated via LangGraph (fan-out / fan-in pattern)
- **Dual LLM Provider** with automatic Groq → Gemini fallback
- **3 Static Analysis Scanners** (Semgrep, Bandit, Ruff) cross-referenced with LLM findings
- **HMAC-SHA256 Webhook Verification** for secure GitHub integration
- **GitHub Check Runs** with pass/fail conclusions for branch-protection gatekeeping
- **Idempotent PR Comments** — updates existing comment on re-push instead of spamming
- **SQLite Persistence** — reviews, issues, evaluations, and agent logs stored for audit
- **LangSmith Tracing** — full observability of LLM calls, token usage, and latency
- **Evaluation Layer** — hallucination rate, duplicate detection, severity consistency, completeness
- **Automatic Diff Truncation** — protects LLM context windows on large PRs

---

## Project Structure

```
codeguardian-ai/
├── main.py                    # FastAPI app entry point (lifespan, /health, /ready)
├── config.py                  # Pydantic Settings (env-driven configuration)
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Container image definition
├── docker-compose.yml         # One-command deployment
├── .dockerignore              # Excludes secrets/caches from image
├── .env.example               # Environment variable template
│
├── api/
│   ├── __init__.py            # Exports the APIRouter
│   └── routes.py              # Webhook endpoint + review retrieval endpoints
│
├── agents/
│   ├── base.py                # Shared agent utilities (JSON parsing, prompt building)
│   ├── security_agent.py      # Vulnerability detection specialist
│   ├── bug_agent.py           # Logic bug detection specialist
│   ├── performance_agent.py   # Performance issue specialist
│   ├── quality_agent.py       # Code quality / style specialist
│   ├── architecture_agent.py  # Design / structural issue specialist
│   ├── consensus_agent.py     # Merges & deduplicates all specialist findings
│   ├── risk_agent.py          # Computes risk scores + merge recommendation
│   └── report_agent.py        # Generates the final markdown review report
│
├── graph/
│   ├── state.py               # CodeGuardianState TypedDict (LangGraph state)
│   ├── router.py              # Routing logic (which agents to activate)
│   ├── nodes.py               # LangGraph node functions (binds agents to state)
│   └── workflow.py            # Builds & compiles the review_graph
│
├── llm/
│   ├── router.py              # invoke_llm() with Groq → Gemini fallback
│   ├── groq_client.py         # Groq API wrapper
│   └── gemini_client.py       # Gemini API wrapper
│
├── scanners/
│   ├── parser.py              # ScannerFinding / ScannerResult dataclasses + merge
│   ├── pipeline.py            # Orchestrates Semgrep + Bandit + Ruff
│   ├── semgrep_runner.py     # Semgrep subprocess runner + JSON parser
│   ├── bandit_runner.py      # Bandit subprocess runner + JSON parser
│   └── ruff_runner.py        # Ruff subprocess runner + JSON parser
│
├── github/
│   ├── webhook.py             # Signature verification + PR event parsing
│   ├── github_api.py          # PyGithub wrapper (fetch diff, files, post comments)
│   ├── comments.py            # Markdown comment formatting + idempotent posting
│   ├── checks.py              # Check Run lifecycle (start, complete, fail)
│   └── diff.py                # Unified-diff parser (FileDiff, DiffHunk, added lines)
│
├── database/
│   ├── database.py            # Engine, SessionLocal, init_db(), get_db() dependency
│   ├── models.py              # SQLAlchemy ORM models (5 tables)
│   └── crud.py                # CRUD operations for all models
│
├── evaluation/
│   ├── metrics.py             # 6 quality metrics (hallucination, relevance, etc.)
│   ├── evaluator.py           # evaluate_review() + evaluate_and_store()
│   └── datasets.py            # Curated evaluation datasets (SQL injection, clean code, etc.)
│
├── observability/
│   ├── __init__.py            # Exports tracing helpers
│   └── langsmith.py           # LangSmith tracing config + metadata + metric extraction
│
├── prompts/
│   ├── __init__.py            # load_prompt() with lru_cache
│   ├── security.txt           # Security agent system prompt
│   ├── bug.txt                # Bug agent system prompt
│   ├── performance.txt        # Performance agent system prompt
│   ├── quality.txt            # Quality agent system prompt
│   ├── architecture.txt       # Architecture agent system prompt
│   ├── consensus.txt          # Consensus agent system prompt
│   ├── risk.txt               # Risk agent system prompt
│   └── report.txt             # Report agent system prompt
│
├── reports/                   # Generated review artifacts (runtime, volume-mounted)
│   └── .gitkeep
│
└── tests/
    ├── test_api.py            # End-to-end API tests (TestClient, webhook, reviews)
    ├── test_webhook.py        # Webhook verification, parsing, diff parser tests
    ├── test_database.py       # ORM models + CRUD operations tests
    ├── test_scanners.py       # Scanner parsers + pipeline tests
    ├── test_agents.py         # Agent finding parsing + normalization tests
    ├── test_graph.py          # LangGraph workflow + routing tests
    ├── test_evaluation.py     # Evaluation metrics + evaluator + datasets tests
    └── test_llm_router.py     # LLM router fallback logic tests
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- A GitHub Personal Access Token with `repo:status` and `checks:write` scopes
- A Groq API key (primary LLM) and/or a Gemini API key (fallback LLM)
- Optional: LangSmith API key for tracing

### Local Development

```bash
# 1. Clone and enter the directory
cd codeguardian-ai

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt
pip install -e .                 # Register the package (for pytest pythonpath)

# 4. Configure environment
cp .env.example .env
# Edit .env and fill in your API keys

# 5. Run the server
python main.py
# or: uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`.

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **Health check**: `http://localhost:8000/health`

---

## Configuration

All configuration is driven by environment variables (see [`.env.example`](.env.example)).

| Variable                   | Default                        | Description                              |
|----------------------------|--------------------------------|------------------------------------------|
| `GROQ_API_KEY`             | *(empty)*                      | Groq API key (primary LLM)              |
| `GEMINI_API_KEY`           | *(empty)*                      | Gemini API key (fallback LLM)           |
| `GROQ_MODEL`               | `llama-3.3-70b-versatile`      | Groq model name                          |
| `GEMINI_MODEL`             | `gemini-2.0-flash`             | Gemini model name                        |
| `LLM_TEMPERATURE`          | `0.0`                          | LLM sampling temperature                 |
| `LLM_TIMEOUT_SECONDS`      | `30`                           | LLM request timeout                      |
| `GITHUB_TOKEN`             | *(empty)*                      | GitHub PAT (`repo:status`, `checks:write`) |
| `GITHUB_WEBHOOK_SECRET`    | *(empty)*                      | HMAC-SHA256 webhook secret              |
| `LANGCHAIN_TRACING_V2`     | `true`                         | Enable LangSmith tracing                 |
| `LANGCHAIN_API_KEY`        | *(empty)*                      | LangSmith API key                        |
| `LANGCHAIN_PROJECT`         | `codeguardian-ai`              | LangSmith project name                   |
| `LANGCHAIN_ENDPOINT`        | `https://api.smith.langchain.com` | LangSmith API endpoint               |
| `DATABASE_URL`              | `sqlite:///./review.db`        | SQLAlchemy database URL                  |
| `MAX_DIFF_CHARS`            | `40000`                        | Max diff size sent to LLM (chars)        |
| `RISK_PASS_THRESHOLD`       | `0.8`                         | Score ≥ this → APPROVE                   |
| `RISK_WARN_THRESHOLD`       | `0.4`                         | Score ≥ this → REQUEST_CHANGES           |
| `APP_HOST`                  | `0.0.0.0`                     | Server bind address                       |
| `APP_PORT`                  | `8000`                        | Server port                               |
| `APP_LOG_LEVEL`             | `info`                        | Logging level                             |
| `ENABLE_SEMGREP`            | `true`                        | Enable Semgrep scanner                    |
| `ENABLE_BANDIT`             | `true`                        | Enable Bandit scanner                     |
| `ENABLE_RUFF`               | `true`                        | Enable Ruff scanner                       |

---

## API Reference

### `POST /github/webhook`

Receive a GitHub webhook delivery.

**Headers:**
- `X-Hub-Signature-256`: HMAC-SHA256 signature (required)
- `Content-Type`: `application/json`

**Response (200):**
```json
{
  "status": "accepted",
  "pr_number": 42,
  "commit_sha": "abc123def456789",
  "message": "Review pipeline started"
}
```

| Status   | Condition                                    | HTTP Code |
|----------|----------------------------------------------|-----------|
| accepted | Valid PR webhook with relevant action        | 200       |
| ignored  | Not a PR event, or action not in trigger set  | 200       |
| —        | Invalid signature                            | 401       |
| —        | Malformed JSON                               | 400       |

**Triggered actions:** `opened`, `synchronize`, `reopened`

---

### `GET /reviews/{review_id}`

Retrieve a single review by ID, including issues and evaluation.

**Response (200):**
```json
{
  "id": 1,
  "pr_id": 1,
  "overall_score": 0.65,
  "risk_level": "REQUEST_CHANGES",
  "summary": "Found 2 issues that should be addressed.",
  "review_time": 3.5,
  "created_at": "2026-01-15T10:30:00",
  "issues": [
    {
      "id": 1,
      "agent": "security",
      "severity": "HIGH",
      "title": "SQL injection in query",
      "file": "app.py",
      "line": 42,
      "description": "User input is concatenated directly.",
      "suggestion": "Use parameterized queries."
    }
  ],
  "evaluation": {
    "id": 1,
    "confidence": 0.85,
    "hallucination": false,
    "duplicate_rate": 0.0,
    "quality_score": 0.9
  }
}
```

**Errors:** `404` if review not found.

---

### `GET /reviews`

List recent reviews with pagination.

**Query Parameters:**
- `limit` (int, default=20, min=1, max=100)
- `offset` (int, default=0, min=0)

**Response (200):**
```json
{
  "reviews": [ /* ReviewResponse[] */ ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

---

### `GET /health`

Liveness probe — always returns 200.

```json
{"status": "ok", "service": "codeguardian-ai"}
```

### `GET /ready`

Readiness probe — checks LLM keys, GitHub token, and tracing status.

```json
{"status": "ok", "tracing": "enabled"}
```

---

## Docker Deployment

### One-Command Deploy

```bash
cd codeguardian-ai
cp .env.example .env
# Edit .env with your real API keys

docker compose up --build -d
```

The service will be available at `http://localhost:8000`.

### Manual Docker Build

```bash
docker build -t codeguardian-ai ./codeguardian-ai
docker run --env-file .env -p 8000:8000 -d codeguardian-ai
```

### Volumes

| Volume         | Mount Path       | Purpose                              |
|----------------|------------------|--------------------------------------|
| `db_data`     | `/app/review.db` | Persists SQLite database             |
| `reports_data` | `/app/reports`   | Persists generated review artifacts  |

### Health Check

The Dockerfile includes a `HEALTHCHECK` that hits `GET /health` every 30 seconds.

---

## GitHub Webhook & Branch Protection Setup

### Step 1: Create a GitHub App or PAT

Create a Personal Access Token at
[https://github.com/settings/tokens](https://github.com/settings/tokens) with:
- `repo:status` — to post review comments
- `checks:write` — to create Check Runs

Set it as `GITHUB_TOKEN` in your `.env`.

### Step 2: Configure the Webhook

In your repository settings (**Settings → Webhooks → Add webhook**):

| Field              | Value                                      |
|--------------------|--------------------------------------------|
| **Payload URL**   | `https://your-domain.com/github/webhook`   |
| **Content type**  | `application/json`                         |
| **Secret**        | *(generate with `python -c "import secrets; print(secrets.token_hex(20))"`)* |
| **Events**        | Let me select individual events → **Pull requests** |

Set the same secret as `GITHUB_WEBHOOK_SECRET` in your `.env`.

### Step 3: Enable Branch Protection

In your repository settings (**Settings → Branches → Add rule**):

1. **Branch name pattern:** `main` (or your default branch)
2. **Require status checks to pass before merging** → ✅ Enable
3. **Require branches to be up to date before merging** → ✅ Enable
4. Search for the Check Run name (e.g., `CodeGuardian AI`) and select it
5. **Require pull request reviews before merging** → ✅ Enable (optional)

Now every PR to `main` will be automatically reviewed by CodeGuardian AI, and
the Check Run must pass before merging is allowed.

---

## Testing

### Run the Full Test Suite

```bash
cd codeguardian-ai
python -m pytest tests/ -v
```

### Run a Specific Test File

```bash
python -m pytest tests/test_api.py -v
python -m pytest tests/test_webhook.py -v
python -m pytest tests/test_graph.py -v
```

### Test Files

| File                  | Tests                                                    |
|-----------------------|----------------------------------------------------------|
| `test_api.py`         | End-to-end API tests (webhook, reviews, health, ready)   |
| `test_webhook.py`     | Signature verification, PR parsing, diff parser          |
| `test_database.py`    | ORM models, CRUD operations, cascade deletes             |
| `test_scanners.py`    | Scanner parsers, merge logic, pipeline orchestration    |
| `test_agents.py`      | Agent finding parsing, normalization, prompt building  |
| `test_graph.py`       | LangGraph workflow, routing logic, node functions        |
| `test_evaluation.py`  | Metrics, evaluator, curated datasets, integration       |
| `test_llm_router.py`  | Groq → Gemini fallback logic                             |

All tests are fully self-contained — no network access or real API keys required.

---

## Migration Notes (src/ → codeguardian-ai/)

This project was migrated from a flat `src/` structure to the modular
`codeguardian-ai/` package. Below is the mapping of old → new locations:

| Old Location                    | New Location                          |
|--------------------------------|---------------------------------------|
| `src/agents/`                  | `codeguardian-ai/agents/`             |
| `src/scanners/`                | `codeguardian-ai/scanners/`           |
| `src/llm/`                     | `codeguardian-ai/llm/`                |
| `src/graph/`                   | `codeguardian-ai/graph/`              |
| `src/github/`                  | `codeguardian-ai/github/`             |
| `src/database/`                | `codeguardian-ai/database/`            |
| `src/evaluation/`              | `codeguardian-ai/evaluation/`         |
| `src/prompts/`                 | `codeguardian-ai/prompts/`            |
| `src/config.py`                | `codeguardian-ai/config.py`           |
| `src/main.py`                  | `codeguardian-ai/main.py`             |
| *(new)*                        | `codeguardian-ai/api/`                |
| *(new)*                        | `codeguardian-ai/observability/`      |
| *(new)*                        | `codeguardian-ai/tests/`              |

### Key Changes

1. **Import paths**: All imports use top-level package names (e.g., `from config import settings`,
   not `from src.config import settings`). The `pyproject.toml` sets `pythonpath = ["codeguardian-ai"]`.
2. **FastAPI application**: New `api/` package with `routes.py` providing webhook + review endpoints.
3. **Observability**: New `observability/` package with LangSmith tracing integration.
4. **Database**: Migrated from in-memory dicts to SQLite via SQLAlchemy ORM (5 tables).
5. **Evaluation layer**: New `evaluation/` package with 6 quality metrics + curated datasets.
6. **Docker**: Production-ready `Dockerfile` + `docker-compose.yml` with health checks and volumes.
7. **Tests**: Comprehensive test suite (8 test files) covering all modules.

### Removed Files

The old `src/` directory and all legacy files have been removed. The project
now lives entirely under `codeguardian-ai/`.
