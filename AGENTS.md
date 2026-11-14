# AGENTS.md — CodePilot Harness

## 1. Purpose and Roles

This file defines the repository-wide engineering instructions, development standards, security requirements, Git workflow, and verification rules for AI coding agents working on CodePilot Harness.

These instructions apply to all development phases, tasks, features, bug fixes, refactoring activities, and future enhancements.

### Responsibilities

**Project Owner**
- Defines project requirements and priorities.
- Authorizes development phases.
- Approves architectural changes.
- Controls commit and push permissions.
- Makes final decisions on phase acceptance.

**Architect / Reviewer**
- Defines scope and implementation plans for owner-authorized work.
- Delegates implementation to the Software Engineer agent in pane 4.
- Reviews implementation, tests, security, documentation, and handoff evidence.
- Sends actionable findings back to pane 4 for correction and reviews the fixes.
- Reports whether acceptance criteria are met; only the Project Owner approves a phase.

**Software Engineer**
- Implements only the task and phase explicitly assigned by the coordinator/Project Owner.
- Writes and runs tests, performs security checks, and updates the implementation report.
- Reports changed files, commands/results, limitations, and findings requiring a decision.
- Stops after handoff and waits for review; does not expand scope or approve a phase.

The implementer must not independently approve a development phase or expand its authorized scope.

### Sequential Pane Handoff (Mandatory)

- Work on one task at a time; do not run tasks or agents in parallel. Only one role may actively implement, test, or review project changes at any moment.
- All role-to-role coordination happens through Herdr. Do not rely on an undocumented side channel, a terminal transcript, or a `done`/`idle` badge as the handoff itself.
- Sequence: (1) Architect / Reviewer writes the authorized plan and sends one implementation prompt to the Software Engineer through Herdr; (2) Software Engineer implements, tests, writes the report/docs, and stops; (3) Software Engineer sends a Herdr handoff asking for review, including report path, commits, verification evidence, and limitations; (4) Architect / Reviewer receives that handoff, then starts review while Software Engineer remains stopped; (5) if fixes are required, Architect / Reviewer sends one consolidated fix prompt through Herdr; (6) Software Engineer resumes only after that prompt, fixes/tests/docs, stops, and sends another Herdr handoff; repeat until review is clear.
- Do not repeatedly poll, resend assignments, or run duplicate/background work while waiting. Use one Herdr wait for the requested handoff; once the Software Engineer is done, review without overlap.
- The Architect / Reviewer reports completion and evidence to the Project Owner only after implementation and review are complete. Only the Project Owner approves phase acceptance.

### Language

- Write implementation reports, review findings, fix requests, and internal handoffs in Vietnamese.
- Write public/product documentation intended for GitHub (for example README and user guides) in English. Source identifiers and commit messages follow the project's existing English conventions.
- Follow the Project Owner's explicit language choice if they specify otherwise.

---

## 2. Required Project Context

Before implementing any task, read and understand:

1. `README.md` — Project overview, goals, architecture, and setup instructions.
2. `IMPLEMENTATION_PLAN.md` — Development phases, dependencies, tasks, and acceptance criteria.
3. `AGENTS.md` — Repository-wide engineering rules.
4. Relevant source code, configuration, and existing tests.

### Instruction Priority

Follow this priority order:

1. Explicit instructions from the Project Owner.
2. Security and safety requirements.
3. This `AGENTS.md` file.
4. `IMPLEMENTATION_PLAN.md`.
5. `README.md`.
6. Existing code conventions.

Higher-priority instructions must not be interpreted as permission to bypass mandatory security protections.

If requirements conflict or are ambiguous, ask for clarification before making significant changes.

Do not silently change established architectural decisions.

---

## 3. Development Phase Authorization

CodePilot Harness follows an eight-phase implementation plan.

### Current Authorization

**Phase 1 — Foundation and Runtime: APPROVED**

**Phase 2 — Repository Exploration: APPROVED**

**Phase 3 — Agent Loop & Editing: IMPLEMENTED (Project Owner reports phase complete); review status is not asserted here**

Phase 3 delivered tasks (historical):

- CP-012: Agent State Machine.
- CP-013: Agent Loop.
- CP-014: Permission Engine.
- CP-015: Patch Tool.
- CP-016: CLI Approval.

**Phase 4 — Verification & Recovery: APPROVED by Project Owner (2026-11-14)**

Phase 4 tasks delivered and reviewed:

- CP-017: Test Runner.
- CP-018: Git Diff Verification.
- CP-019: Verification Engine.
- CP-020: Bounded Self-Correction (maximum 3 repair retries).
- CP-021: Task Report.

**Phase 5 — Sandbox Hardening: AUTHORIZED by Project Owner (2026-11-14)**

Authorized Phase 5 tasks:

- CP-022: Workspace Isolation.
- CP-023: Execution Restrictions, including bounded AST worker isolation.
- CP-024: Host Patch Application after separate explicit approval.
- CP-025: Security Testing.

**Phase 6–8: LOCKED**

### Scope Restrictions

Phase 4 is complete and approved. Its historical scope restrictions remain documented in
`IMPLEMENTATION_PLAN.md`.

During Phase 5:

- Implement only CP-022–CP-025, preserving Phases 1–4 behavior.
- Keep untrusted test execution in Docker; never fall back to Host execution.
- Do not apply changes to Host before a separate, exact approval of the verified final diff.
- Revalidate Host state and reject stale snapshots, conflicts, traversal, symlinks, and partial writes.
- Fail closed when workspace isolation or security evidence is incomplete.
- Reuse existing Docker restrictions where verified; do not duplicate controls unnecessarily.
- Do not implement Phase 6 persistence or unrelated features.
- Stop for security review before Phase 6; Critical or High findings must be resolved.

Phase-specific authorization must be updated when the Project Owner approves the next phase.

All repository-wide engineering and Git policies remain applicable across every phase.

---

## 4. Technology Stack and Architecture

### Core Technology

- Language: Python 3.12 or later.
- Package and environment management: uv.
- CLI framework: Typer.
- Terminal rendering: Rich.
- Data validation: Pydantic.
- Configuration: TOML and `.env`.
- LLM provider: Google Gemini through `google-genai`.
- Sandbox runtime: Docker.
- Testing: pytest.
- Linting: Ruff.
- Session persistence in later phases: SQLite.

### Architectural Principles

- Use a Modular Monolith architecture.
- Maintain clear module boundaries.
- Prefer explicit dependencies and interfaces.
- Separate business logic from CLI presentation.
- Keep the Gemini adapter independent from tool implementations.
- Keep tool registration separate from tool execution.
- Use dependency injection where it improves testability.
- Avoid unnecessary abstractions and premature optimization.

### Framework Restrictions

Do not introduce LangChain, LangGraph, or another agent orchestration framework without Project Owner approval.

The Agent Loop will be implemented as a custom component in its authorized phase.

### Docker Policy

Docker is required for executing untrusted repository code.

Sandbox execution must follow these principles:

- Use an isolated container.
- Run as a non-root user.
- Disable network access by default.
- Apply resource and execution time limits.
- Do not mount the host home directory.
- Do not expose the Docker socket inside the sandbox.
- Do not expose API keys or other secrets.
- Do not silently fall back to host execution when Docker fails.

Read-only repository exploration may execute in the Harness process only when workspace boundaries and resource limits are enforced.

---

## 5. Implementation Standards

### General Coding Rules

- Follow the existing `src/codepilot/` package structure.
- Use Python type hints.
- Write readable, maintainable, and testable code.
- Follow established naming conventions.
- Keep functions and classes focused on a single responsibility.
- Prefer standard library functionality when appropriate.
- Handle expected errors explicitly.
- Avoid unnecessary dependencies.
- Do not introduce unrelated changes.
- Preserve backward compatibility unless a breaking change is authorized.

### Tool Development

Tool implementations must:

- Expose clear input and output contracts.
- Validate arguments using Pydantic where appropriate.
- Return structured success and error results.
- Reject unknown or unauthorized tools.
- Enforce workspace boundaries.
- Apply execution timeouts and output limits.
- Avoid leaking sensitive information.

### File Access

File operations must:

- Restrict access to the configured workspace.
- Reject path traversal attempts.
- Prevent symlink escapes.
- Exclude sensitive files and directories.
- Handle binary files safely.
- Handle encoding and permission errors.
- Enforce file size and output limits.

### Code Search

Code search must:

- Use ripgrep for repository text search.
- Support literal and regex search where required.
- Return relative file paths and line numbers.
- Enforce timeouts and result limits.
- Avoid `shell=True`.
- Handle invalid patterns and missing ripgrep gracefully.

### Repository Analysis

Repository mapping must:

- Prefer static analysis.
- Use Python AST where appropriate.
- Identify relevant modules, classes, and functions.
- Avoid importing or executing target repository code.
- Exclude irrelevant and sensitive files.
- Enforce repository map size limits.

### Context Construction

Context building must:

- Select relevant repository content.
- Preserve source references where available.
- Respect configured context budgets.
- Avoid including secrets or unrelated content.
- Never access files outside the authorized workspace.

---

## 6. Security and Permission Boundaries

Security requirements are mandatory for every development phase.

### Secrets Management

- Never hardcode credentials or API keys.
- Never commit `.env` files containing secrets.
- Never print secrets in logs, reports, or test output.
- Never expose credentials to Docker sandbox containers.
- Redact sensitive configuration values.
- Use environment variables for sensitive configuration.

### Workspace Protection

- Enforce configured workspace boundaries.
- Prevent path traversal.
- Prevent symlink-based workspace escapes.
- Do not read or modify files outside the authorized workspace.
- Do not overwrite existing user changes without permission.

### Execution Safety

- Do not execute untrusted repository code directly on the host.
- Do not bypass sandbox restrictions.
- Do not silently disable security checks.
- Do not use unrestricted shell execution.
- Apply timeouts and resource limits to external processes.

### Change Control

- Do not implement unauthorized features.
- Do not modify unrelated modules unnecessarily.
- Do not remove security controls to make tests pass.
- Do not perform destructive operations without explicit approval.

If a security requirement cannot be satisfied, report the limitation and stop the affected operation.

---

## 7. Testing and Verification

Every feature must be verified before being reported as complete.

### Testing Requirements

- Write unit tests for new functionality.
- Add integration tests when components interact.
- Test normal behavior and failure cases.
- Include security-related edge cases.
- Maintain existing regression coverage.
- Do not modify tests merely to hide implementation defects.

### Required Verification Commands

```powershell
uv sync
uv run pytest -q
uv run ruff check .
git diff --check
```

Run Docker smoke tests when Docker is available and the changes affect relevant functionality.

### Test Reporting

Use accurate verification statuses:

- `PASS`: Successfully executed and passed.
- `FAIL`: Executed and failed.
- `SKIPPED`: Intentionally skipped with a reason.
- `BLOCKED`: Could not execute due to an external dependency or environment limitation.
- `NOT VERIFIED`: Not executed or independently confirmed.

Never claim tests passed without actual execution evidence.

When a test fails, investigate the root cause rather than suppressing the failure.

---

## 8. Git Workflow

### 8.1 Branch Policy

- Work directly on the `main` branch.
- Do not automatically create or switch branches.
- Commit và push để bàn giao review theo quyền thường trực tại mục 8.6. Không tự merge.
- Never force push.
- Never commit secrets.
- Never overwrite existing user changes.
- Do not use destructive Git commands such as `git reset --hard` or `git clean -fd` without explicit permission.
- Inspect the working tree before making changes.

### 8.2 Conventional Commits — Project-Wide Policy

All commits throughout the entire CodePilot Harness project MUST follow the Conventional Commits specification.

This policy applies to:

- All development phases.
- All features and capabilities.
- All bug fixes.
- All refactoring activities.
- All testing changes.
- All documentation updates.
- All future enhancements.

Commit organization must follow logical functionality, not phase numbers or CP task identifiers.

### Commit Format

`<type>(<scope>): <description>`

Supported commit types:

- `feat`: Introduces a new feature or capability.
- `fix`: Corrects a bug or incorrect behavior.
- `refactor`: Restructures code without changing its external behavior.
- `test`: Adds or updates automated tests.
- `docs`: Updates documentation.
- `chore`: Performs maintenance or configuration changes.
- `perf`: Improves performance.
- `ci`: Changes continuous integration configuration.
- `build`: Changes build tooling or dependencies.

### 8.3 Feature-Based Commit Granularity

- Each independent logical feature SHOULD have its own focused commit.
- Do not group unrelated features into a single commit.
- Do not create one large commit for an entire phase when it contains multiple independent features.
- A single CP task may produce multiple feature commits.
- Multiple related changes may belong to one commit when they form one cohesive feature.
- Include directly related tests in the same feature commit when appropriate.
- Use the commit type that accurately represents the change.
- Commit descriptions must clearly communicate the functionality being introduced or modified.
- Avoid vague commit messages such as `update code`, `fix stuff`, or `phase 2 done`.
- Keep commits small enough to review and revert independently when practical.

### Conventional Commit Examples

```text
feat(registry): add tool registration and discovery
feat(registry): validate tool input schemas
feat(files): implement workspace file listing
feat(files): add secure text file reading
feat(search): support literal code search
feat(search): support regex code search
feat(repo-map): extract Python classes and functions
feat(context): add relevance-based file selection
feat(context): enforce context token budget
fix(files): prevent symlink traversal outside workspace
fix(docker): verify container cleanup after timeout
refactor(tools): simplify structured result handling
test(search): cover ripgrep timeout scenarios
docs: update implementation guide
chore(config): update development tooling
ci: add automated test workflow
```

These examples are illustrative, not a fixed list of allowed scopes.

Choose scopes based on the actual module or functionality.

### 8.4 Commit Authorization

- Chuẩn bị và xác minh thay đổi trước khi commit theo quyền tại mục 8.6.
- Only create commits when the Project Owner has authorized committing.
- Quyền commit/push thường trực tại mục 8.6 áp dụng cho công việc đã được phép; không tự cấp quyền triển khai phase hoặc tính năng mới.
- When commit authorization is granted, create separate Conventional Commits for independent logical features.
- Stage only files relevant to the intended commit.
- Avoid `git add .` when unrelated or sensitive files may be present.
- Do not amend, rewrite, or squash existing commits without permission.

### 8.5 Verification Before Commit

Before creating a commit, run:

```powershell
git status
git diff --check
uv run pytest -q
uv run ruff check .
```

Review the staged diff before finalizing the commit.

### 8.6 Push Policy

Project Owner đã cấp quyền thường trực cho **toàn bộ dự án**, áp dụng cho mọi phase, chức năng và lượt sửa review đã được phép:

- Khi hoàn tất công việc và các kiểm tra bắt buộc, tạo Conventional Commits theo từng chức năng rồi push lên `main` để reviewer đọc được trên GitHub.
- Không dừng ở commit local để chờ một yêu cầu push lặp lại. Không cần hỏi lại quyền commit/push cho mỗi lần bàn giao review trong phạm vi này.
- Chỉ đưa vào commit các thay đổi thuộc công việc được phép và tài liệu liên quan; giữ nguyên thay đổi khác của người dùng.
- Sau khi push, báo commit hash, kết quả xác minh, giới hạn còn lại và link review, rồi dừng chờ review.
- Quyền này không cho phép tự merge, force push, viết lại lịch sử hoặc bắt đầu phase chưa được phê duyệt. Nếu Project Owner có chỉ thị mới như “chỉ local” hoặc “không push”, tuân theo chỉ thị mới đó.
- Do not force push.
- Do not merge branches automatically.
- Report the local commit hashes when commits have been created.
- Stop and wait for review when the authorized work is complete.

---

## 9. Delivery and Reporting

At the end of each authorized phase, produce an implementation report.

For the active phase, create the matching report, including:

- `docs/PHASE_4_IMPLEMENTATION_REPORT.md` for Phase 4.
- `docs/PHASE_5_IMPLEMENTATION_REPORT.md` for Phase 5.

### Required Report Sections

1. Summary of completed tasks.
2. Features implemented.
3. Architecture decisions.
4. Files created or modified.
5. Unit and integration test results.
6. Security verification results.
7. Git status and diff summary.
8. Commit history, if authorized.
9. Acceptance criteria with PASS, FAIL, or INCONCLUSIVE status.
10. Known limitations and unresolved issues.
11. Review request.

### Reporting Rules

- Report only work that was actually completed.
- Distinguish implementation results from assumptions.
- Clearly identify blocked or unverified checks.
- Do not claim phase approval.
- Do not hide failing tests or known security issues.
- Do not start the next phase automatically.

---

## 10. Completion Checklist

Before reporting an authorized task or phase as complete, verify:

- [ ] The implementation stays within the authorized scope.
- [ ] Existing architecture and module boundaries are respected.
- [ ] New functionality has appropriate automated tests.
- [ ] Relevant security boundaries are enforced.
- [ ] No secrets are exposed or committed.
- [ ] Regression tests have been executed.
- [ ] Ruff checks have been executed.
- [ ] Git diff has been inspected.
- [ ] Existing user changes have been preserved.
- [ ] Commits, if authorized, follow Conventional Commits.
- [ ] Independent features are organized into focused commits.
- [ ] No unauthorized push or merge has occurred.
- [ ] The implementation report is complete.
- [ ] Software Engineer has stopped and is awaiting review.

---

## Final Rule

**Implement only what is authorized.**

**Use Conventional Commits for every logical change throughout the project.**

**Work on `main`, but never commit or push without the required authorization.**

**Verify changes, report results accurately, and wait for Project Owner review before proceeding to the next phase.**
