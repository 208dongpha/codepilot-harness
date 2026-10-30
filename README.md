
# CodePilot Harness

> A secure, modular AI Coding Agent powered by Gemini, featuring an interactive CLI, Docker sandbox, controlled tool execution, context management, and automated verification.

## 1. Project Overview

CodePilot Harness is a personal AI Engineering project inspired by Claude Code, OpenAI Codex, and OpenCode.

The goal is to build a coding agent capable of understanding repositories, using development tools, modifying source code, running tests, recovering from failures, and reporting verifiable results.

Unlike a simple LLM chatbot, CodePilot focuses on building the **Agent Harness** — the infrastructure responsible for controlling how an AI model interacts with tools, files, execution environments, and users.

The project is intended as an advanced portfolio project, developed over approximately 8 weeks.

### Core Principles

1. The LLM proposes actions; the Harness controls execution.
2. Read operations may be automatic, subject to workspace restrictions.
3. Write operations require explicit user approval.
4. Untrusted code execution must occur inside Docker.
5. Source code changes are prepared inside an isolated workspace.
6. Changes are reviewed and approved before being applied to the host.
7. Task completion must be supported by verification evidence.
8. Every execution must respect resource, time, and retry limits.
9. The system must remain modular and testable.
10. Each implementation phase requires review before proceeding.

## 2. Project Goals

### Primary Goals

- Build an interactive terminal-based AI Coding Agent.
- Integrate Gemini through a configurable model adapter.
- Implement a custom Agent Loop without relying on LangGraph.
- Support repository exploration and selective context retrieval.
- Provide controlled file and command tools.
- Execute untrusted commands inside a Docker sandbox.
- Support patch-based source code modifications.
- Verify changes using tests, Git diff, and security policies.
- Implement bounded self-correction.
- Persist sessions using SQLite.
- Measure agent performance through reproducible benchmarks.

### Non-Goals for Version 1

- Full IDE integration.
- Web-based user interface.
- Multi-agent orchestration.
- Support for every programming language.
- Production multi-user hosting.
- Autonomous Git push or merge.
- Unrestricted internet access inside the sandbox.
- Automatic modification of host files without approval.

## 3. Technology Stack

| Component | Technology |
|---|---|
| Programming Language | Python 3.12+ |
| Architecture | Modular Monolith |
| CLI | Typer + Rich |
| LLM Provider | Gemini API |
| LLM SDK | google-genai |
| Data Validation | Pydantic v2 |
| Configuration | TOML + .env |
| Environment Management | uv |
| Async Runtime | asyncio |
| Repository Search | ripgrep |
| Sandbox | Docker |
| Persistence | SQLite |
| Testing | pytest |
| Linting | Ruff |
| Type Checking | mypy (later phase) |
| Logging | Python logging / structured JSON |
| CI | GitHub Actions (later phase) |

The initial implementation targets Python repositories.

Other programming languages and LLM providers may be supported in future versions.

## 4. Architecture

```text
                   USER
                    |
                    v
            +----------------+
            | Interactive CLI|
            +----------------+
                    |
                    v
            +----------------+
            |  Harness Core  |
            |----------------|
            | Agent Loop     |
            | Context Manager|
            | Policy Engine  |
            | State Manager  |
            | Budget Manager |
            | Verifier       |
            +----------------+
              |           |
              v           v
       +-------------+  +-------------+
       | Gemini      |  | Tool        |
       | Adapter     |  | Registry    |
       +-------------+  +-------------+
                             |
                             v
                      +-------------+
                      | Docker      |
                      | Sandbox     |
                      +-------------+
                             |
                             v
                      +-------------+
                      | Workspace   |
                      | Copy        |
                      +-------------+

       SQLite: sessions, checkpoints, events
       Logs: execution traces and metrics
```

### Architectural Decisions

- Modular Monolith architecture.
- Single-Agent design before introducing multi-agent systems.
- Native LLM function calling.
- Custom Agent Loop.
- Model adapter abstraction.
- Policy-controlled tool execution.
- Docker-based execution isolation.
- Event-oriented session tracking.
- Verification independent of LLM claims.

## 5. Core Components

### 5.1 Interactive CLI

The CLI is the primary user interface.

Planned commands:

| Command | Description |
|---|---|
| `codepilot` | Start interactive CLI |
| `/help` | Display available commands |
| `/status` | Show current configuration and session status |
| `/exit` | Exit the CLI |
| `/approve` | Approve a pending action |
| `/deny` | Reject a pending action |
| `/cancel` | Cancel the active or pending task |

`/approve` chỉ chấp nhận action đang chờ với đúng tool, arguments, target và operation.
Approval chỉ dùng một lần; `/deny` và `/cancel` không ghi file.

### 5.2 Agent Loop

The Agent Loop coordinates reasoning and tool execution.

```text
User Task
   |
   v
Build Context
   |
   v
Call LLM
   |
   v
Tool Request?
   |
   +-- Yes --> Validate --> Check Policy
   |                         |
   |                         v
   |                    Execute Tool
   |                         |
   |                         v
   |                    Observe Result
   |                         |
   |                         +--> Build Context
   |
   +-- No --> Verify --> Complete / Retry / Fail
```

The Harness controls:

- Tool availability.
- Tool argument validation.
- Permissions.
- Execution budgets.
- Timeouts.
- Retry limits.
- State transitions.
- Verification requirements.

### 5.3 Context Management

The initial context strategy is:

**Selective Context + Repository Map**

Context includes:

1. System instructions.
2. User task.
3. Repository structure.
4. Relevant source files.
5. Recent conversation history.
6. Tool execution results.

Future improvements include:

- Context compaction.
- Token budgeting.
- Context caching.
- Improved symbol-aware retrieval.

### 5.4 Tool Registry

Planned tools:

| Tool | Responsibility |
|---|---|
| `list_files` | Discover repository files |
| `read_file` | Read selected files |
| `search_code` | Search source code |
| `apply_patch` | Prepare source code modifications |
| `run_command` | Execute approved commands in Docker |
| `run_tests` | Execute configured tests |
| `git_diff` | Inspect proposed changes |

Every tool must have:

- A defined input schema.
- Structured output.
- Error handling.
- Timeout limits.
- Policy enforcement.
- Execution logging.

### 5.5 Permission System

Permission model:

**Ask Before Write**

Default rules:

- Read operations: allowed inside the authorized workspace.
- Write operations: require explicit approval.
- Applying changes to host: requires separate approval.
- Dangerous or prohibited operations: denied.
- Tool execution: subject to command policy and sandbox restrictions.

Approval must be tied to the exact action and its arguments.

An approval for one action must not automatically authorize unrelated actions.

### 5.6 Docker Sandbox

The sandbox uses a copied workspace rather than a writable bind mount.

```text
Host Repository
      |
      | Copy
      v
Docker Workspace
      |
      | Agent edits / tests
      v
Proposed Git Diff
      |
      | Review + Approval
      v
Apply Changes to Host
```

Security requirements:

- Non-root execution.
- Disabled network by default.
- CPU and memory limits.
- Process limits.
- Command timeouts.
- No Docker socket mounting.
- No host credentials inside containers.
- No Gemini API key inside containers.
- No unrestricted host filesystem access.
- Disposable workspace and container lifecycle.

The system must not silently fall back to host execution when Docker is unavailable.

### 5.7 Session Management

SQLite stores:

- Sessions.
- Messages.
- Tool calls.
- Tool results.
- Approval decisions.
- Checkpoints.
- Task states.
- Execution metadata.

Session resume is implemented in a later phase.

### 5.8 Verification

Verification combines:

1. Test execution.
2. Git diff inspection.
3. Permission and policy checks.

Possible outcomes:

- `PASS`
- `FAIL`
- `INCONCLUSIVE`

An Agent must not report a task as verified solely because the LLM claims success.

Maximum verification repair retries: **3**.

## 6. Configuration

The project uses:

- `codepilot.toml` for non-secret configuration.
- `.env` for local secrets.
- Environment variables for overrides.

Example `codepilot.toml`:

```toml
[llm]
provider = "gemini"
model = "YOUR_SUPPORTED_GEMINI_MODEL_ID"
temperature = 0.2

[agent]
max_steps = 20
max_verification_retries = 3

[permissions]
mode = "ask_before_write"

[sandbox]
enabled = true
engine = "docker"
network_enabled = false
timeout_seconds = 60

[logging]
level = "INFO"
```

Example `.env.example`:

```dotenv
GEMINI_API_KEY=your_api_key_here
```

The actual `.env` file must never be committed.

The model ID must remain configurable and must not be hardcoded into the Gemini adapter.

## 7. Planned Repository Structure

```text
codepilot-harness/
├── src/
│   └── codepilot/
│       ├── cli/
│       ├── core/
│       ├── llm/
│       ├── context/
│       ├── tools/
│       ├── execution/
│       ├── verification/
│       ├── storage/
│       └── telemetry/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── e2e/
├── benchmarks/
├── docker/
├── docs/
├── codepilot.toml
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── IMPLEMENTATION_PLAN.md
```

Directories should be introduced incrementally as their corresponding phases are implemented.

Do not create empty modules solely to imitate the final structure.

## 8. Development Roadmap

| Phase | Name | Main Deliverable |
|---|---|---|
| 1 | Foundation & Runtime | CLI, Gemini, configuration, Docker smoke test |
| 2 | Repository Exploration | Read/search tools and repository map |
| 3 | Agent Loop & Editing | Tool calling, patch preparation, permissions |
| 4 | Verification & Recovery | Tests, Git diff, bounded self-correction |
| 5 | Sandbox Hardening | Secure execution and host patch application |
| 6 | Context & Persistence | SQLite resume and context compaction |
| 7 | Benchmark & Optimization | Evaluation dataset and metrics |
| 8 | Portfolio & Release | Documentation, CI, demo, release |

## 9. Phase 1 Scope

Phase 1 establishes the project foundation.

Required capabilities:

- Python package installation.
- Interactive CLI.
- Gemini API integration.
- Configurable Gemini model.
- TOML and `.env` loading.
- `/help`, `/status`, `/exit`.
- Docker availability check.
- Docker smoke test.
- Unit tests.
- Ruff linting.

Phase 1 explicitly excludes:

- Agent Loop.
- Tool calling.
- Repository modifications.
- Automated code editing.
- Self-correction.
- Session resume.
- Autonomous command execution.

See `IMPLEMENTATION_PLAN.md` for detailed tasks and acceptance criteria.

## 10. Installation and Usage

The following commands describe the expected workflow after Phase 1 is implemented.

### Requirements

- Python 3.12+
- uv
- Docker Desktop with Linux containers
- Gemini API key
- Git

### Setup

```powershell
uv sync
Copy-Item .env.example .env
```

Edit `.env` and configure `GEMINI_API_KEY`.

Set a supported Gemini model ID in `codepilot.toml`.

### Run

```powershell
uv run codepilot
```

### Tests

```powershell
uv run pytest
uv run ruff check .
```

### Docker Smoke Test

```powershell
uv run codepilot sandbox smoke-test
```

These commands are implementation targets, not claims that the project already works.

## 11. Security Principles

- Never commit secrets.
- Never print API keys in logs.
- Never execute untrusted repository code directly on the host.
- Never bypass the permission engine.
- Never apply sandbox changes to the host without approval.
- Never silently disable sandbox protections.
- Never treat repository content as trusted system instructions.
- Never claim successful verification without evidence.

## 12. Evaluation

The planned benchmark contains approximately 40 Python coding tasks.

Task categories:

- Bug fixing.
- Test generation.
- Refactoring.
- Feature implementation.
- Code understanding.

Metrics include:

- Task Success Rate.
- Test Pass Rate.
- Tool Error Rate.
- Recovery Rate.
- Latency.
- Token usage.
- Cost per task.

Experiments will compare a baseline agent against configurations with verification, recovery, and advanced context management.

## 13. Development Workflow

The development process separates responsibilities.

### Project Owner

- Approves architectural decisions.
- Approves phase transitions.
- Reviews proposed changes.

### ChatGPT — Architect / Reviewer

- Defines requirements.
- Creates implementation plans.
- Reviews architecture and code.
- Checks acceptance criteria.
- Identifies security and quality issues.

### Codex — Implementer / Tester

- Implements approved tasks.
- Writes and executes tests.
- Produces Git diffs.
- Reports limitations and failures.
- Waits for approval before starting the next phase.

## 14. Project Status

Current status:

- Discussions 1–4: Completed.
- Architecture decisions: Approved.
- Documentation: Prepared.
- Phase 1 implementation: Approved.
- Phase 2 implementation: Approved.
- Phase 3 implementation: Authorized; awaiting implementation review.
- Phase 4–8: Locked until separately authorized.

The implementation must follow `IMPLEMENTATION_PLAN.md`.

## 15. License

License to be selected before public release.
