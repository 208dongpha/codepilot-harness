# Phase 4 — Implementation Report: Verification & Recovery

> **Scope:** CP-017 through CP-021 only.
> **Status:** Implementation and local verification complete; awaiting Architect / Reviewer review.
> **Phase acceptance:** Not asserted. Only the Project Owner can approve the phase.

## 1. Summary

Phase 4 adds evidence-based verification and bounded self-correction to CodePilot Harness:

1. The configured test command runs inside the existing Docker sandbox, never on the host.
2. The sandbox workspace is compared against a baseline captured before the task, with
   approved patch hashes used to distinguish authorized from unauthorized changes.
3. A verification engine returns `PASS`, `FAIL`, or `INCONCLUSIVE` from tests, diff evidence,
   and policy checks.
4. Test failures are sent back to the model as bounded, redacted evidence, with a hard maximum
   of three repair retries.
5. Each task produces a report containing changed files, executed tests, verdict, retry count,
   and remaining issues.

Phase 1–3 behavior is preserved when no verifier is supplied to the agent loop. Phase 5–8
features were not implemented: there is no host patch application, no copied-workspace sandbox
hardening, and no session persistence.

## 2. Tasks Completed

| Task | Status | Delivered behavior |
|---|---|---|
| CP-017 — Test Runner | Implemented | Runs the configured command in a disposable Docker container, collects exit code/stdout/stderr, enforces timeout and output limits, and verifies cleanup. |
| CP-018 — Git Diff Verification | Implemented | Captures a baseline, detects added/modified/deleted/binary/sensitive changes, validates changed content against approved patch hashes, and emits a bounded git-style unified diff. |
| CP-019 — Verification Engine | Implemented | Returns `PASS`, `FAIL`, or `INCONCLUSIVE` from test results, diff evidence, and policy violations. Unauthorized/policy changes stop immediately. |
| CP-020 — Self-Correction | Implemented | Sends redacted test evidence back to the model, tracks repair attempts, allows at most three retries, preserves verification history, and stops on exhausted budget or unsafe changes. |
| CP-021 — Task Report | Implemented | Reports verdict, changed files, test executions, verification attempts, retry count, and remaining issues; the CLI renders a bounded summary. |

## 3. Features Implemented

### 3.1 Docker test execution (CP-017)

- `DockerRuntime.run_workspace()` creates a disposable container with:
  - `--network none`
  - non-root user (`_sandbox_user()`; fixed `10001:10001` when no non-root host UID exists)
  - `--cpus 1`, `--memory 512m`, `--pids-limit 128`
  - `--cap-drop ALL`, `--security-opt no-new-privileges`
  - `--read-only` root filesystem with a bounded `/tmp` tmpfs
  - only the isolated workspace bind-mounted at `/workspace`
  - non-secret environment variables only
- The command is executed as an argv list, never through a shell.
- Output is drained through bounded buffers so an unbounded test cannot exhaust harness memory.
- Timeout kills the container/process and cleanup is verified by exact-name container lookup.
- Docker/image failures raise safe errors and are mapped to `SANDBOX_UNAVAILABLE`,
  `SANDBOX_IMAGE_MISSING`, `DOCKER_TIMEOUT`, or `SANDBOX_CLEANUP_FAILED`.
- `docker/Dockerfile.sandbox` now installs `pytest>=8,<9` so the default command
  `python -m pytest -q` works.

### 3.2 Baseline and diff verification (CP-018)

- A text baseline is captured from the isolated workspace before the task starts.
- `GitDiffVerifier` compares the final workspace to that baseline and reports:
  - added, modified, and deleted files
  - binary/unreadable files without exposing their contents
  - sensitive-content policy violations
  - incomplete evidence when enumeration or size limits are reached
- The diff is a git-style unified diff generated in Python. The isolated workspace intentionally
  excludes `.git`, so no repository git hooks or external git process are involved.
- Authorized changes are matched against the SHA-256 of the content written by an approved
  `PatchTool` operation. A changed path with no matching approval, or a hash mismatch, is
  unauthorized.
- Diff output and change lists are bounded; incomplete comparisons fail closed as
  `INCONCLUSIVE`.

### 3.3 Verification verdicts (CP-019)

Evaluation order:

1. Policy violations → `FAIL`, not retryable.
2. Unauthorized changes → `FAIL`, not retryable.
3. Incomplete diff evidence → `INCONCLUSIVE`, not retryable.
4. Sandbox/runner error → `INCONCLUSIVE`, not retryable.
5. Test timeout → `INCONCLUSIVE`, not retryable.
6. Nonzero test exit code → `FAIL`, retryable.
7. All checks pass → `PASS`.

The model's text alone never produces a `PASS`; the harness verdict is required.

### 3.4 Bounded self-correction (CP-020)

- On a retryable test failure, the agent receives a JSON feedback message containing:
  - verdict and reason codes
  - test command and exit code
  - bounded, redacted test output labeled `untrusted_test_output`
  - changed-file summary and unauthorized paths
  - current repair attempt and maximum attempts
- The agent can propose further patches, which still require exact-action human approval.
- At most three repair retries are allowed (`agent.max_verification_retries`, capped at 3).
- The loop stops on unauthorized changes, policy violations, cancellation, inconclusive
  infrastructure evidence, exhausted retry budget, or exhausted step budget.
- Verification history is retained in `AgentRunResult.verifications`; the latest verdict is
  exposed as `AgentRunResult.verification`.

### 3.5 Task report (CP-021)

`TaskReport` contains:

- `verdict`
- `files_changed` (including authorization status and added/deleted line counts)
- `tests_executed` (command, exit code, timeout, duration, bounded output)
- `retry_count`
- `verification_attempts`
- `remaining_issues`

If a task fails before verification runs (for example a provider error after an approved patch),
the report falls back to a diff-only snapshot of the workspace.

## 4. Architecture Decisions

- **Verification is a separate package** (`codepilot.verification`) with test runner, diff,
  engine, recovery, report, and shared models. The CLI and agent loop depend on the engine; the
  engine does not depend on the CLI.
- **Docker execution remains in `execution/docker_runtime.py`.** Verification-specific policy
  and result mapping live in `verification/test_runner.py`.
- **No host fallback.** A Docker failure returns a safe error and an `INCONCLUSIVE` or failed
  verification; the test command is never retried on the host.
- **Baseline scope is per task.** The baseline is the isolated workspace state at task start,
  which includes prior approved task changes but not changes made during the current task.
- **Authorization is content-addressed.** Approved patch results contribute
  `path -> SHA-256` (or `None` for deletion); verification checks final content against these
  hashes rather than trusting the model's claim that a change was approved.
- **Diff is generated without git.** This avoids running repository-controlled git
  configuration/hooks and works in the isolated copy where `.git` is intentionally excluded.
- **Test output is untrusted data.** It is bounded, credential-pattern-redacted before storage
  or feedback, and explicitly labeled as untrusted in the model message.
- **Reports are in-memory handoff objects.** Persistence is Phase 6 scope.

## 5. Files Created or Modified

### Created

- `src/codepilot/verification/__init__.py`
- `src/codepilot/verification/models.py`
- `src/codepilot/verification/redaction.py`
- `src/codepilot/verification/test_runner.py`
- `src/codepilot/verification/diff.py`
- `src/codepilot/verification/engine.py`
- `src/codepilot/verification/recovery.py`
- `src/codepilot/verification/report.py`
- `tests/unit/test_test_runner.py`
- `tests/unit/test_verification_diff.py`
- `tests/unit/test_verification_engine.py`
- `tests/unit/test_task_report.py`
- `tests/integration/test_verification_docker.py`
- `docs/PHASE_4_IMPLEMENTATION_REPORT.md` (this report)

### Modified

- `src/codepilot/execution/docker_runtime.py` — sandboxed workspace command execution with
  bounded capture and cleanup.
- `src/codepilot/config.py` — `[verification]` configuration and command validation.
- `codepilot.toml` — default verification command, timeout, and output limit.
- `docker/Dockerfile.sandbox` — install pytest for the default test command.
- `src/codepilot/agent/loop.py` — verification handoff, authorized patch tracking, retry budget,
  verification history, and task report.
- `src/codepilot/cli/main.py` — verifier construction and report rendering.
- `src/codepilot/telemetry/logging.py` — safe Docker/test-runner event names.
- `tests/unit/test_docker_runtime.py` — sandbox command execution tests.
- `tests/unit/test_config.py` — verification configuration tests.
- `tests/unit/test_agent_loop.py` — verification pass/fail/retry/unauthorized/report tests.
- `tests/unit/test_cli.py` — report rendering test.

### Not included

The pre-existing working-tree changes to `AGENTS.md`, `IMPLEMENTATION_PLAN.md`, and `README.md`
were not created by this implementation and were left uncommitted.

## 6. Tests Executed

### 6.1 Final commands

| Command | Result | Evidence |
|---|---|---|
| `uv sync` | PASS | Resolved 41 packages, checked 41 packages |
| `uv run pytest -q` | PASS with SKIPPED | **242 passed, 13 skipped** in 7.62s |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `git diff --check` | PASS | No whitespace errors; only CRLF conversion warnings |
| `uv run codepilot --help` | PASS | CLI entry point renders |
| `uv run codepilot sandbox smoke-test` | PASS | Real Docker: available, sandbox image ready, network disabled, non-root, `codepilot-smoke-ok` |

The 13 final skips are:

- 6 ripgrep integration tests (`ripgrep` is not installed in this environment).
- 1 Docker smoke integration test and 6 Docker verification integration tests, skipped because
  Docker Desktop's Linux engine failed to start after an environment-level restart.

### 6.2 Docker-backed evidence obtained while Docker was available

| Check | Result |
|---|---|
| `uv run pytest -q tests/integration/test_verification_docker.py` | **6 passed** in 12.45s (real Docker) |
| `uv run codepilot sandbox smoke-test` | **PASS** (real Docker) |
| Earlier full suite with Docker available | **248 passed, 6 skipped** (ripgrep only), before the final recovery integration test was added |

The Docker verification integration suite covers:

- security flags and mount scope inspection
- non-root user, network disabled, read-only root, cap drop, no-new-privileges
- CPU/memory/process limits
- default pytest command execution
- nonzero exit-code propagation
- timeout handling and cleanup
- end-to-end authorized diff verification
- real fail → feedback → retry → pass recovery workflow

### 6.3 Docker environment limitation

During the final verification window Docker Desktop stopped responding and then failed to start
its Linux engine (`Docker Desktop is unable to start`). WSL was stopped/restarted and the engine
was restarted, but it remained stuck in `starting`. This is an external environment failure, not
a test assertion failure in the changed code. The successfully executed Docker runs above are
preserved as evidence; the final full-suite run records Docker integration tests as `SKIPPED`
rather than pretending they passed.

## 7. Security Verification

| Control | Result | Evidence |
|---|---|---|
| No host test execution | PASS | `run_workspace()` only invokes `docker`; Docker failure raises `DockerError`; unit test asserts no `Popen`/host fallback |
| Network disabled | PASS | `--network none`; Docker inspect assertion |
| Non-root execution | PASS | `_sandbox_user()` avoids `0:0`; Docker inspect assertion |
| Docker socket not mounted | PASS | Only `/workspace` bind; unit/integration assertions |
| Host home not mounted | PASS | Only isolated workspace bind |
| Resource limits | PASS | `--cpus 1`, `--memory 512m`, `--pids-limit 128`, timeout |
| Read-only root filesystem | PASS | `--read-only` plus bounded `/tmp` tmpfs |
| Container cleanup | PASS in stable Docker runs | Exact-name absence verification; cleanup failure is reported |
| Secret exposure to container | PASS | Only `HOME`, `TMPDIR`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`; no API key or host env |
| Secret exposure in output/report | PASS with pattern-based limitation | `SECRET_CONTENT` redaction, bounded output, no logging of raw output |
| Unauthorized sandbox changes | PASS | Baseline comparison and approved content hashes; unsafe changes stop the task |
| Permission boundary preserved | PASS | Patches still require exact-action approval; verification never self-approves writes |
| No Phase 5 host patch application | PASS | No host-write path added |

## 8. Git Status and Diff Summary

Working tree at report time:

```text
 M AGENTS.md                 (pre-existing owner change, not staged)
 M IMPLEMENTATION_PLAN.md    (pre-existing owner change, not staged)
 M README.md                 (pre-existing owner change, not staged)
```

Functional commits created (before the report commit):

```text
3196047 feat(verification): run configured tests in docker sandbox
4147a0b feat(verification): detect sandbox changes against trusted baseline
60db827 feat(verification): evaluate test diff and policy evidence
d6e500f feat(report): summarize task verification evidence
4016b66 feat(agent): retry bounded repairs from verification feedback
628b4de feat(cli): display verification verdict and task report
b7741df test(verification): cover docker execution and recovery workflow
```

The implementation adds 8 source modules under `src/codepilot/verification/` plus focused
changes to the agent loop, CLI, configuration, Docker runtime, Dockerfile, telemetry event list,
and tests. No files outside the Phase 4 scope were modified by this work.

## 9. Acceptance Criteria

| Criterion | Status | Notes |
|---|---|---|
| Tests run in Docker | PASS | Real Docker integration passed while the engine was available |
| Test failures analyzed | PASS | Bounded test evidence is returned to the model for repair |
| Agent can retry | PASS | Unit and real-Docker recovery tests |
| Retry limit enforced | PASS | Maximum 3 repairs; exhaustion test |
| Unauthorized changes detected | PASS | Diff/engine/loop tests |
| Verification does not rely on LLM claims | PASS | Harness runs tests and computes diff/verdict |
| Timeout enforced | PASS | Unit and real-Docker timeout test |
| Output limits enforced | PASS | Bounded buffer and redaction tests |
| Container cleanup verified | PASS in stable Docker runs | Cleanup failure returns an error, never success |
| No host fallback | PASS | Unit test asserts only `docker` commands |
| Task report produced | PASS | Model, builder, CLI rendering, and fallback report tests |
| Docker integration in final full-suite run | INCONCLUSIVE | Docker Desktop engine failed externally; tests skipped with explicit reason |
| Phase 4 accepted | PENDING | Requires Project Owner approval after review |

## 10. Known Limitations and Unresolved Issues

1. **Docker Desktop instability in this environment.** The final full-suite run could not run
   Docker integration tests because the engine failed to start. Earlier real-Docker runs passed.
   Reviewers should rebuild the image and rerun the Docker module if their engine is healthy:
   `docker build -f docker/Dockerfile.sandbox -t codepilot-sandbox:phase1 docker`.
2. **Sandbox image dependencies.** The image contains Python and pytest, not a project's runtime
   dependencies. Test commands requiring third-party packages need a purpose-built image or
   preinstalled dependencies; network is disabled during tests.
3. **Writable workspace bind mount.** Phase 4 mounts the isolated workspace read/write so tests
   can run normally and net changes can be detected. Phase 5 is still required for copied
   workspace isolation and stronger filesystem-race/symlink-swap hardening. Transient changes
   that are reverted before verification are not observable.
4. **Baseline size policy.** Baseline/diff evidence is bounded by `repository.max_total_bytes`,
   `max_files`, and scan limits. Larger repositories produce `INCONCLUSIVE` instead of an
   unbounded read.
5. **Diff implementation.** CP-018 produces a git-style unified diff in Python rather than
   invoking `git diff`. This is intentional because the isolated workspace excludes `.git` and
   avoids executing repository-controlled git configuration. A reviewer requiring literal
   `git diff` behavior should raise a finding.
6. **Test output redaction is pattern-based.** Known credential shapes are redacted, but an
   unrecognized secret printed by a test could still appear in bounded evidence. The container
   receives no API keys or host environment secrets, and excluded files are not copied.
7. **No test-count sanity check.** A test command that exits 0 without running any tests is
   treated as a pass by the generic runner. The configured command is trusted harness
   configuration.
8. **Live Gemini verification not executed.** Verification/recovery tests use deterministic
   scripted providers. No live Gemini call was needed for Phase 4 unit/integration evidence.
9. **Cancellation/denial reports.** Task reports are produced for completed and failed terminal
   states with a verifier; cancellation and denial display their existing CLI messages without a
   final report object.
10. **Linux root-host permissions.** On POSIX hosts running the harness as root, the container
    uses UID 10001 while the isolated workspace is private to root. This may require permission
    adjustment; the current Windows/Docker Desktop environment passed. This is a known
    portability limitation, not a host-fallback path.

## 11. Review Request

Please review:

1. `src/codepilot/execution/docker_runtime.py` for sandbox flags, bounded output, timeout,
   cleanup, and no-host-fallback behavior.
2. `src/codepilot/verification/diff.py` and `engine.py` for baseline trust, unauthorized-change
   detection, policy precedence, and `INCONCLUSIVE` handling.
3. `src/codepilot/agent/loop.py` for retry budget, state transitions, approved-hash tracking,
   and evidence preservation.
4. `src/codepilot/verification/report.py` and `cli/main.py` for report completeness and safe
   rendering.
5. Test coverage and the Docker environment limitation recorded above.

The implementation stops here for Architect / Reviewer review. It does not begin Phase 5, apply
patches to the host, or claim phase approval.
