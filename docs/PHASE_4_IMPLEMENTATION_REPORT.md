# Báo cáo triển khai Phase 4 — Verification & Recovery

> **Phạm vi:** chỉ CP-017 đến CP-021.
> **Trạng thái:** Đã bổ sung TaskReport cho `/cancel` và `/deny`; các fix CP-018 fail-closed đã hoàn tất,
> gồm excluded entries (`.env`, `secrets/`, `.ssh/`) và vòng re-review xử lý symlink ẩn trong excluded
> directory cùng signature bị giả mạo bằng `os.utime`; xác minh độc lập trước đó ghi nhận 3 lỗi Docker
> CLI timeout; đang chờ Architect / Reviewer review lại.
> **Phê duyệt phase:** Không tự khẳng định. Chỉ Project Owner được phê duyệt phase.

## 1. Tóm tắt

Phase 4 bổ sung xác minh dựa trên bằng chứng và tự sửa lỗi có giới hạn cho CodePilot Harness:

1. Lệnh test đã cấu hình chạy bên trong Docker sandbox hiện có, không bao giờ chạy trên Host.
2. Workspace sandbox được so sánh với baseline được chụp trước task; hash của các patch đã được
   phê duyệt được dùng để phân biệt thay đổi được phép và thay đổi không được phép.
3. Verification Engine trả về `PASS`, `FAIL` hoặc `INCONCLUSIVE` từ kết quả test, bằng chứng diff
   và kiểm tra policy.
4. Lỗi test được gửi lại cho model dưới dạng bằng chứng có giới hạn, đã redact, với tối đa cứng
   3 lần repair retry.
5. Mỗi task tạo báo cáo gồm files changed, tests executed, verdict, retry count và remaining
   issues. `/cancel` và `/deny` cũng tạo `TaskReport` terminal với verdict `INCONCLUSIVE` và
   reason an toàn.

Hành vi Phase 1–3 được giữ nguyên khi không truyền verifier vào agent loop. Không triển khai
Phase 5–8: không có host patch application, không có hardening sandbox bằng copied workspace và
không có session persistence.

## 2. Các task đã hoàn thành

| Task | Trạng thái triển khai | Hành vi đã giao |
|---|---|---|
| CP-017 — Test Runner | Đã triển khai | Chạy lệnh đã cấu hình trong Docker container dùng một lần, thu thập exit code/stdout/stderr, áp dụng timeout và giới hạn output, rồi xác minh cleanup. |
| CP-018 — Git Diff Verification | Đã triển khai | Chụp baseline, phát hiện thay đổi added/modified/deleted/binary/sensitive, đối chiếu nội dung thay đổi với hash của patch đã phê duyệt và tạo bounded git-style unified diff. |
| CP-019 — Verification Engine | Đã triển khai | Trả về `PASS`, `FAIL` hoặc `INCONCLUSIVE` từ kết quả test, bằng chứng diff và policy violations. Thay đổi unauthorized/policy dừng ngay. |
| CP-020 — Self-Correction | Đã triển khai | Gửi bằng chứng test đã redact lại cho model, theo dõi repair attempts, cho phép tối đa 3 lần retry, lưu verification history và dừng khi hết budget hoặc gặp thay đổi không an toàn. |
| CP-021 — Task Report | Đã triển khai | Báo cáo verdict, changed files, test executions, verification attempts, retry count và remaining issues; CLI render bản tóm tắt có giới hạn. |

Ghi chú: phần triển khai code của CP-017 đến CP-021 đã hoàn tất; các finding CP-018 về symlink
và skipped entry đã được sửa (mục 6.5, 6.6); CP-021 follow-up bổ sung report cho `/cancel` và
`/deny` (mục 6.7); kết quả xác minh Docker độc lập của Reviewer bị lỗi timeout và được ghi nhận
riêng tại mục 6, không được tính là PASS.

## 3. Các chức năng đã triển khai

### 3.1 Thực thi test trong Docker (CP-017)

- `DockerRuntime.run_workspace()` tạo container dùng một lần với:
  - `--network none`
  - người dùng non-root (`_sandbox_user()`; cố định `10001:10001` khi không có host UID
    non-root)
  - `--cpus 1`, `--memory 512m`, `--pids-limit 128`
  - `--cap-drop ALL`, `--security-opt no-new-privileges`
  - `--read-only` root filesystem cùng `/tmp` tmpfs có giới hạn
  - chỉ bind-mount isolated workspace tại `/workspace`
  - chỉ các biến môi trường không chứa secret
- Lệnh được thực thi dưới dạng argv list, không bao giờ qua shell.
- Output được đọc qua bounded buffer để test sinh output vô hạn không làm cạn bộ nhớ của
  Harness.
- Timeout sẽ kill container/process; cleanup được xác minh bằng truy vấn container theo tên
  chính xác.
- Lỗi Docker/image trả về safe error và được map thành `SANDBOX_UNAVAILABLE`,
  `SANDBOX_IMAGE_MISSING`, `DOCKER_TIMEOUT` hoặc `SANDBOX_CLEANUP_FAILED`.
- `docker/Dockerfile.sandbox` hiện cài `pytest>=8,<9` để lệnh mặc định
  `python -m pytest -q` hoạt động.

### 3.2 Baseline và xác minh diff (CP-018)

- Baseline dạng text được chụp từ isolated workspace trước khi task bắt đầu.
- `GitDiffVerifier` so sánh workspace cuối với baseline và báo cáo:
  - file added, modified và deleted
  - file binary/unreadable mà không để lộ nội dung
  - policy violation do sensitive content
  - bằng chứng không đầy đủ khi chạm giới hạn enumeration hoặc size
- Diff là git-style unified diff được tạo bằng Python. Isolated workspace chủ động loại trừ
  `.git`, nên không có git hook của repository và không có tiến trình git bên ngoài tham gia.
- Thay đổi được phép được đối chiếu với SHA-256 của nội dung do thao tác `PatchTool` đã được
  phê duyệt ghi ra. Đường dẫn bị thay đổi mà không có approval khớp, hoặc hash không khớp, được
  coi là unauthorized.
- Diff output và change list đều có giới hạn; so sánh không đầy đủ fail closed thành
  `INCONCLUSIVE`.
- **Review fix CP-018:** `Workspace.list_files` ghi nhận symlink/reparse point vào
  `FileListing.linked_paths` mà không follow; `WorkspaceSnapshot.capture` đánh dấu baseline
  incomplete nếu có linked path; `GitDiffVerifier` thêm policy `LINKED_ENTRY_DETECTED`, đánh dấu
  diff incomplete và báo path link như unauthorized binary change; `VerificationEngine` trả
  `FAIL` non-retryable, không thể PASS với symlink không được báo cáo.
- **Review fix 2 CP-018:** `WorkspaceSnapshot.capture` và `GitDiffVerifier.compare` coi
  `listing.skipped > 0` là bằng chứng incomplete; không follow path không chắc chắn và không thể
  PASS khi enumeration/read bị skip.

### 3.3 Các verdict xác minh (CP-019)

Thứ tự đánh giá:

1. Policy violation → `FAIL`, không retryable.
2. Unauthorized change → `FAIL`, không retryable.
3. Bằng chứng diff không đầy đủ → `INCONCLUSIVE`, không retryable.
4. Lỗi sandbox/runner → `INCONCLUSIVE`, không retryable.
5. Test timeout → `INCONCLUSIVE`, không retryable.
6. Test exit code khác 0 → `FAIL`, retryable.
7. Mọi kiểm tra đều đạt → `PASS`.

Chỉ text của model không bao giờ tạo ra `PASS`; verdict của Harness là bắt buộc.

### 3.4 Tự sửa lỗi có giới hạn (CP-020)

- Khi test failure có thể retry, agent nhận một JSON feedback message gồm:
  - verdict và reason code
  - test command và exit code
  - test output có giới hạn, đã redact, được gắn nhãn `untrusted_test_output`
  - tóm tắt changed file và unauthorized path
  - repair attempt hiện tại và số attempt tối đa
- Agent có thể đề xuất thêm patch; các patch này vẫn cần approval chính xác của con người.
- Tối đa 3 repair retry (`agent.max_verification_retries`, giới hạn cứng 3).
- Loop dừng khi có unauthorized change, policy violation, cancellation, bằng chứng hạ tầng
  inconclusive, hết retry budget hoặc hết step budget.
- Verification history được giữ trong `AgentRunResult.verifications`; verdict mới nhất được
  expose qua `AgentRunResult.verification`.

### 3.5 Task report (CP-021)

`TaskReport` gồm:

- `verdict`
- `files_changed` (gồm trạng thái authorization và số dòng added/deleted)
- `tests_executed` (command, exit code, timeout, duration, bounded output)
- `retry_count`
- `verification_attempts`
- `remaining_issues`

Nếu task thất bại trước khi verification chạy (ví dụ provider error sau một patch đã được
approve), report fallback sang snapshot diff-only của workspace.

- **CP-021 follow-up:** `/cancel` và `/deny` trả terminal `AgentRunResult` với `TaskReport`
  verdict `INCONCLUSIVE`, reason `TASK_CANCELLED` hoặc `PENDING_ACTION_DENIED`; CLI render report
  sau message tương ứng. Denial/no-write và approval semantics được giữ nguyên.

## 4. Các quyết định kiến trúc

- **Verification là một package riêng** (`codepilot.verification`) gồm test runner, diff, engine,
  recovery, report và shared models. CLI và agent loop phụ thuộc vào engine; engine không phụ
  thuộc vào CLI.
- **Docker execution vẫn nằm trong `execution/docker_runtime.py`.** Policy và result mapping
  riêng cho verification nằm trong `verification/test_runner.py`.
- **Không có host fallback.** Lỗi Docker trả về safe error và verification `INCONCLUSIVE` hoặc
  failed; lệnh test không bao giờ được retry trên Host.
- **Baseline có phạm vi theo task.** Baseline là trạng thái isolated workspace tại thời điểm task
  bắt đầu, bao gồm các thay đổi đã được approve từ task trước nhưng không gồm thay đổi phát sinh
  trong task hiện tại.
- **Authorization gắn với nội dung.** Kết quả patch đã approve đóng góp `path -> SHA-256` (hoặc
  `None` cho deletion); verification kiểm tra nội dung cuối với các hash này thay vì tin vào lời
  model rằng thay đổi đã được approve.
- **Diff được tạo không cần git.** Cách này tránh chạy git configuration/hook do repository kiểm
  soát và hoạt động trong isolated copy nơi `.git` bị loại trừ có chủ đích.
- **Test output là dữ liệu không đáng tin.** Output được giới hạn, redact theo credential pattern
  trước khi lưu hoặc gửi feedback, và được gắn nhãn untrusted rõ ràng trong message gửi model.
- **Report là object bàn giao in-memory.** Persistence thuộc phạm vi Phase 6.

## 5. Các file đã tạo hoặc sửa

### Đã tạo

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
- `docs/PHASE_4_IMPLEMENTATION_REPORT.md` (báo cáo này)

### Đã sửa

- `src/codepilot/execution/docker_runtime.py` — thực thi lệnh trong workspace sandbox với
  bounded capture và cleanup.
- `src/codepilot/config.py` — cấu hình `[verification]` và validate command.
- `codepilot.toml` — verification command, timeout và output limit mặc định.
- `docker/Dockerfile.sandbox` — cài pytest cho test command mặc định.
- `src/codepilot/agent/loop.py` — verification handoff, theo dõi patch đã approve, retry budget,
  verification history và task report.
- `src/codepilot/cli/main.py` — tạo verifier và render report.
- `src/codepilot/telemetry/logging.py` — tên event an toàn cho Docker/test-runner.
- `tests/unit/test_docker_runtime.py` — test thực thi lệnh trong sandbox.
- `tests/unit/test_config.py` — test cấu hình verification.
- `tests/unit/test_agent_loop.py` — test verification pass/fail/retry/unauthorized/report.
- `tests/unit/test_cli.py` — test render report.

### Không đưa vào commit

Các thay đổi có sẵn trong working tree đối với `AGENTS.md`, `IMPLEMENTATION_PLAN.md` và
`README.md` không do lượt triển khai này tạo ra và được giữ nguyên, không stage/commit.

## 6. Các kiểm thử đã thực hiện

### 6.1 Xác minh độc lập của Reviewer

| Lệnh | Kết quả | Bằng chứng |
|---|---|---|
| `uv run pytest -q` | **FAIL** | **3 failed / 246 passed / 6 skipped** |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `git diff --check` | PASS | Không có lỗi whitespace; chỉ có cảnh báo CRLF |

3 lỗi là các Docker integration test bị timeout trong các lệnh Docker CLI. Theo kết quả xác minh
độc lập này, Docker integration được ghi nhận là **environment-blocked/failed verification**;
báo cáo **không tuyên bố PASS** cho `uv run pytest -q`. Trong bộ test hiện tại, 6 skipped tương
ứng với các integration test ripgrep (ripgrep không được cài trong môi trường).

### 6.2 Retest của Pane 4 — Software Engineer

| Lệnh / phạm vi | Kết quả | Ghi chú |
|---|---|---|
| `uv sync` | PASS | Resolved 41 packages, checked 41 packages |
| `uv run pytest -q` (full suite, trước review fix CP-018) | **249 passed, 6 skipped** | Hai lần chạy: 99.88s và 74.25s; 6 skipped là ripgrep; Docker integration chạy và pass trong các lần này |
| `uv run pytest -q` (full suite, sau symlink fix) | **253 passed, 6 skipped** trong 29.11s | 6 skipped là ripgrep; 4 test symlink mới đã chạy và pass; Docker integration pass trong lần này |
| `uv run pytest -q` (full suite, sau skipped-entry fix) | **255 passed, 6 skipped** trong 25.97s | 6 skipped là ripgrep; 2 test skipped-entry mới đã chạy và pass; Docker integration pass trong lần này |
| `uv run pytest -q` (full suite, sau CP-021 cancel/deny follow-up) | **260 passed, 6 skipped** trong 19.22s | 6 skipped là ripgrep; 5 test cancel/deny/report mới đã chạy và pass; Docker integration pass trong lần này |
| Focused CP-021 tests: `tests/unit/test_agent_loop.py`, `tests/unit/test_cli.py`, `tests/unit/test_task_report.py` | **36 passed** trong 1.69s | Bao gồm cancel/deny report và workspace/host unchanged |
| Focused verification tests sau skipped-entry fix: `tests/unit/test_verification_diff.py`, `tests/unit/test_verification_engine.py` | **23 passed** trong 0.98s | Bao gồm symlink và skipped baseline/current tests |
| Focused regression set rộng hơn | **97 passed** trong 1.89s | Gồm verification, file tools, context builder và repository map |
| Docker-focused retest: `tests/integration/test_verification_docker.py` + `tests/integration/test_docker.py` | **3 failed / 4 passed** trong 139.19s | Tái hiện 3 lỗi timeout; gồm `test_real_test_runner_runs_in_docker_with_security_limits`, `test_real_recovery_workflow_fails_then_repairs_and_passes` và `test_real_docker_smoke` |
| `uv run codepilot sandbox smoke-test` | PASS (lần chạy trước) | Docker available, image ready, network disabled, non-root |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `git diff --check` | PASS | Không có lỗi whitespace; chỉ có cảnh báo CRLF |
| `uv run codepilot --help` | PASS | CLI entry point render được |

Lần full suite **249 passed, 6 skipped** xảy ra ngay sau lần Docker-focused retest thất bại; lần
chạy lại sau khi cập nhật báo cáo cũng cho **249 passed, 6 skipped** trong 74.25s. Sau symlink fix,
full suite cho **253 passed, 6 skipped** trong 29.11s; sau skipped-entry fix, full suite cho
**255 passed, 6 skipped** trong 25.97s; sau CP-021 follow-up, full suite cho **260 passed,
6 skipped** trong 19.22s (thêm 11 regression test CP-018/CP-021). Các kết quả này cho thấy lỗi
timeout của Docker CLI không ổn định theo môi trường; chúng **không thay thế** kết quả FAIL độc lập
của Reviewer và không được dùng để tuyên bố PASS cho xác minh Docker.

### 6.3 Phạm vi của Docker integration suite

Bộ test Docker bao phủ:

- kiểm tra security flag và phạm vi mount
- người dùng non-root, network disabled, read-only root, cap drop, no-new-privileges
- CPU/memory/process limit
- thực thi pytest command mặc định
- truyền exit code khác 0
- xử lý timeout và cleanup
- xác minh diff end-to-end với thay đổi đã được approve
- workflow recovery thực tế: fail → feedback → retry → pass

### 6.4 Giới hạn môi trường Docker

Docker Desktop/Docker CLI trong môi trường này có lúc timeout ở `docker create`, `docker inspect`
hoặc `docker start --attach`, kể cả với smoke test có từ Phase 1. Reviewer ghi nhận
**3 failed / 246 passed / 6 skipped**; implementer tái hiện **3 failed / 4 passed** trong
Docker-focused retest. Sau các fix CP-018/CP-021, full suite pass **260 passed / 6 skipped**
trong 19.22s.

Các timeout Docker không tái hiện được như một lỗi logic triển khai; các finding CP-018/CP-021 là
lỗi logic tái lập được và đã được sửa riêng (mục 6.5, 6.6 và 6.7). Trạng thái Docker integration
vẫn được ghi nhận là **environment-blocked/failed**, không phải PASS. Khi Docker engine ổn định,
cần chạy lại:

```powershell
docker build -f docker/Dockerfile.sandbox -t codepilot-sandbox:phase1 docker
uv run pytest -q tests/integration/test_verification_docker.py tests/integration/test_docker.py
```

### 6.5 Bản sửa review CP-018 — fail closed khi có symlink

**Finding:** CP-018 bỏ sót persistent symlink. `Workspace.list_files` silently bỏ qua linked
entries, nên verifier có thể PASS với một symlink không được báo cáo.

**Fix:**

- `FileListing` có thêm `linked_paths`; `Workspace.list_files` ghi nhận symlink/reparse point và
  **không follow** chúng.
- `WorkspaceSnapshot.capture` đánh dấu baseline incomplete nếu baseline có linked path.
- `GitDiffVerifier.compare` thêm policy `LINKED_ENTRY_DETECTED`, đánh dấu diff incomplete và báo
  từng linked path như unauthorized binary change.
- `VerificationEngine` trả `FAIL` non-retryable do policy violation; symlink không thể bị bỏ qua
  để tạo `PASS`. Symlink cũng không bị follow trong quá trình scan/diff.
- Regression tests:
  - `test_workspace_list_files_reports_linked_entries_without_following`
  - `test_added_symlink_is_reported_and_fails_closed`
  - `test_replaced_file_with_symlink_is_reported_and_fails_closed`
  - `test_added_symlink_fails_verification_without_following`
- Test symlink chỉ skip nếu OS không tạo được symlink; trên môi trường hiện tại các test này đã
  chạy và pass.

**Evidence:** focused regression set **97 passed** trong 1.89s; full suite sau symlink fix
**253 passed, 6 skipped** trong 29.11s; sau skipped-entry fix **255 passed, 6 skipped** trong
25.97s; commit `84afb04 fix(verification): fail closed on linked sandbox entries`.

### 6.6 Bản sửa review 2 CP-018 — skipped entries fail closed

**Finding:** `Workspace.list_files` expose `skipped`, nhưng `WorkspaceSnapshot.capture` và
`GitDiffVerifier.compare` không fail closed khi `listing.skipped > 0`. Enumeration/read race hoặc
một entry không truy cập được có thể làm evidence incomplete trong khi verifier vẫn PASS.

**Fix:**

- `WorkspaceSnapshot.capture` đánh dấu baseline `complete=False` khi `listing.skipped > 0`.
- `GitDiffVerifier.compare` đánh dấu `enumeration_complete=False` khi `listing.skipped > 0`.
- Không follow path không chắc chắn; baseline/current diff incomplete dẫn tới
  `VerificationEngine` trả `INCONCLUSIVE` với `DIFF_INCOMPLETE` (hoặc policy/unauthorized
  `FAIL` nếu có bằng chứng khác), không thể PASS.
- Regression tests:
  - `test_skipped_baseline_entries_are_inconclusive`
  - `test_skipped_current_entries_are_inconclusive`
- Các test mô phỏng `FileListing(..., skipped=1)` ở baseline và ở lần verification hiện tại,
  với fake runner trả test exit code 0; kết quả vẫn là `INCONCLUSIVE`, không phải `PASS`.

**Evidence:** focused verification tests **23 passed** trong 0.98s; broader focused set
**97 passed** trong 1.89s; full suite sau fix **255 passed, 6 skipped** trong 25.97s; commit
`cf3de36 fix(verification): fail closed on skipped sandbox entries`.

### 6.7 CP-021 follow-up — TaskReport cho `/cancel` và `/deny`

**Finding:** CLI `/cancel` và `/deny` trước đây chỉ hiển thị message và bỏ loop hiện tại; không có
`TaskReport` cuối cho terminal outcome, nên thiếu bằng chứng report cho cancellation/denial.

**Fix:**

- `AgentLoop.cancel()` và `AgentLoop.deny()` trả `AgentRunResult` terminal thay vì `None`.
- State là `CANCELLED`; reason an toàn là `TASK_CANCELLED` hoặc `PENDING_ACTION_DENIED`.
- `build_task_report(..., forced_reason=...)` buộc verdict `INCONCLUSIVE` và reason tương ứng,
  kể cả khi có verification failure trước đó; files/tests evidence cũ vẫn được giữ nếu có.
- CLI `/cancel` và `/deny` render report ngay sau message tương ứng.
- Denial/no-write và approval semantics được giữ nguyên: không patch nào được apply, approval vẫn
  single-use và bị cancel/deny đúng cách.
- Regression tests:
  - `test_deny_returns_inconclusive_report_and_no_writes`
  - `test_cancel_returns_inconclusive_report_and_no_writes`
  - `test_deny_emits_inconclusive_task_report`
  - `test_cancel_emits_inconclusive_task_report`
  - `test_forced_reason_makes_terminal_cancel_deny_inconclusive`
- Test cancel/deny xác nhận `host/app.py` và isolated workspace vẫn giữ nguyên nội dung ban đầu;
  test deny còn xác nhận pending action bị denied/cancelled, test cancel xác nhận cancelled và
  không denied.

**Evidence:** focused CP-021 tests **36 passed** trong 1.69s; full suite sau fix
**260 passed, 6 skipped** trong 19.22s; commit
`1d4cb2d feat(report): emit inconclusive reports for cancel and deny`.

## 7. Kiểm tra bảo mật

| Kiểm soát | Kết quả | Bằng chứng |
|---|---|---|
| Không thực thi test trên Host | PASS | `run_workspace()` chỉ gọi `docker`; lỗi Docker raise `DockerError`; unit test xác nhận không có `Popen`/host fallback |
| Network disabled | PASS (cấu hình/unit); Docker runtime NOT VERIFIED trong lần Reviewer | `--network none`; Docker inspect assertion trong lần chạy thành công trước đó |
| Non-root execution | PASS (cấu hình/unit); Docker runtime NOT VERIFIED trong lần Reviewer | `_sandbox_user()` tránh `0:0`; Docker inspect assertion trong lần chạy thành công trước đó |
| Không mount Docker socket | PASS (cấu hình/unit) | Chỉ bind `/workspace`; unit/integration assertion |
| Không mount host home | PASS (cấu hình/unit) | Chỉ bind isolated workspace |
| Resource limits | PASS (cấu hình/unit); Docker runtime NOT VERIFIED trong lần Reviewer | `--cpus 1`, `--memory 512m`, `--pids-limit 128`, timeout |
| Read-only root filesystem | PASS (cấu hình/unit); Docker runtime NOT VERIFIED trong lần Reviewer | `--read-only` cùng bounded `/tmp` tmpfs |
| Container cleanup | PASS (unit); Docker runtime NOT VERIFIED trong lần Reviewer | Xác minh container vắng mặt theo tên; cleanup failure được báo lỗi, không báo success |
| Secret không đi vào container | PASS (cấu hình/unit) | Chỉ `HOME`, `TMPDIR`, `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`; không có API key hoặc host env |
| Secret không lộ trong output/report | PASS (unit) với giới hạn redaction theo pattern | `SECRET_CONTENT` redaction, bounded output, không log raw output |
| Phát hiện thay đổi sandbox unauthorized | PASS (unit) | Baseline comparison và approved content hash; thay đổi không an toàn dừng task |
| Symlink/reparse point không bị bỏ qua | PASS (unit) | `FileListing.linked_paths`; `LINKED_ENTRY_DETECTED`; regression tests symlink added/replaced và engine fail-closed |
| Skipped entry không tạo PASS | PASS (unit) | `listing.skipped > 0` làm baseline/current diff incomplete; regression tests skipped baseline/current trả `INCONCLUSIVE` |
| Cancel/deny không ghi file | PASS (unit) | `AgentLoop.cancel()/deny()` trả report `INCONCLUSIVE`; test xác nhận host và isolated workspace unchanged |
| Giữ nguyên permission boundary | PASS | Patch vẫn cần approval chính xác; verification không tự approve ghi |
| Không triển khai host patch application của Phase 5 | PASS | Không thêm host-write path |

Ghi chú: các kiểm tra Docker runtime từng được xác nhận bằng Docker inspect trong một lần chạy
thành công trước đó, nhưng lần xác minh độc lập của Reviewer bị timeout. Vì vậy báo cáo không
đánh dấu PASS cho Docker runtime trong lần Reviewer; các kiểm soát cấu hình/unit vẫn được đánh
giá riêng.

## 8. Git status và tóm tắt diff

Working tree tại thời điểm cập nhật báo cáo:

```text
 M AGENTS.md                 (thay đổi có sẵn của Project Owner, không stage)
 M IMPLEMENTATION_PLAN.md    (thay đổi có sẵn của Project Owner, không stage)
 M README.md                 (thay đổi có sẵn của Project Owner, không stage)
 M docs/PHASE_4_IMPLEMENTATION_REPORT.md (báo cáo này, sẽ được commit)
```

Các commit chức năng đã tạo:

```text
3196047 feat(verification): run configured tests in docker sandbox
4147a0b feat(verification): detect sandbox changes against trusted baseline
60db827 feat(verification): evaluate test diff and policy evidence
d6e500f feat(report): summarize task verification evidence
4016b66 feat(agent): retry bounded repairs from verification feedback
628b4de feat(cli): display verification verdict and task report
b7741df test(verification): cover docker execution and recovery workflow
362fecb docs(report): record phase 4 verification and recovery
84afb04 fix(verification): fail closed on linked sandbox entries
cf3de36 fix(verification): fail closed on skipped sandbox entries
1d4cb2d feat(report): emit inconclusive reports for cancel and deny
10db3d8 fix(verification): fail closed on excluded sandbox entries
250a6c7 fix(verification): fail closed on hidden links and forged metadata
```

Commit cập nhật báo cáo tiếng Việt này sẽ được tạo sau khi hoàn tất kiểm tra; hash sẽ được nêu
trong handoff cuối. Lượt cập nhật báo cáo chỉ sửa `docs/PHASE_4_IMPLEMENTATION_REPORT.md`; các
code fix CP-018 nằm trong commit `84afb04`, `cf3de36`, `10db3d8` và `250a6c7`; CP-021 follow-up nằm
trong commit `1d4cb2d`; không có thay đổi application code nào khác.

Việc triển khai thêm 8 module source dưới `src/codepilot/verification/` cùng các thay đổi tập
trung ở agent loop, CLI, configuration, Docker runtime, Dockerfile, telemetry event list và test.
Không sửa file ngoài phạm vi Phase 4.

## 9. Tiêu chí chấp nhận

| Tiêu chí | Trạng thái | Ghi chú |
|---|---|---|
| Tests chạy trong Docker | **FAIL / BLOCKED (môi trường Docker)** | Reviewer: 3 Docker integration test timeout trong Docker CLI; implementer tái hiện 3 failed; không tuyên bố PASS |
| Test failures được phân tích | PASS (unit) | Bằng chứng test có giới hạn được gửi lại cho model để repair |
| Agent có thể retry | PASS (unit) / NOT VERIFIED (Docker integration) | Unit test retry pass; Docker recovery integration thất bại trong lần Reviewer |
| Retry limit được áp dụng | PASS (unit) | Tối đa 3 repair; có test exhaustion |
| Unauthorized changes được phát hiện | PASS (unit) | Diff/engine/loop tests |
| Symlink/reparse point không bị bỏ qua | PASS (unit) | `LINKED_ENTRY_DETECTED`; symlink added/replaced tests; engine FAIL non-retryable |
| Skipped/inaccessible entry không tạo PASS | PASS (unit) | Baseline/current `listing.skipped > 0` -> `INCONCLUSIVE`/`DIFF_INCOMPLETE`; regression tests |
| Excluded entry mới/sửa không tạo PASS | PASS (unit) | `.env`, `secrets/`, `.ssh/` mới hoặc thay đổi -> policy `EXCLUDED_ENTRY_ADDED`/`REMOVED`/`MODIFIED`, FAIL non-retryable; metadata-only, không đọc nội dung; baseline ổn định vẫn PASS |
| Symlink trong excluded sensitive dir | PASS (unit) | `.ssh/config` là link được ghi vào `linked_paths`; `LINKED_ENTRY_DETECTED` kể cả khi link có từ baseline; FAIL non-retryable, không follow target |
| Signature chống forged mtime | PASS (unit) | `size` + `mtime_ns` + `ctime_ns` + `dev`/`ino`/`nlink` + attributes + NTFS change-time; rewrite cùng kích thước rồi `os.utime` -> `EXCLUDED_ENTRY_MODIFIED` |
| Verification không dựa hoàn toàn vào lời LLM | PASS (unit/architecture) | Harness chạy test và tự tính diff/verdict |
| Timeout được áp dụng | PASS (unit) / NOT VERIFIED (Docker runtime) | Unit timeout pass; Docker timeout test bị CLI timeout trong lần Reviewer |
| Output limit được áp dụng | PASS (unit) | Bounded buffer và redaction tests |
| Container cleanup được xác minh | PASS (unit) / NOT VERIFIED (Docker runtime) | Unit cleanup pass; Docker runtime timeout trong lần Reviewer |
| Không fallback sang Host | PASS (unit) | Unit test xác nhận chỉ có lệnh `docker` |
| Task report được tạo | PASS (unit/CLI) | Model, builder, CLI rendering và fallback report tests |
| TaskReport cho `/cancel` và `/deny` | PASS (unit/CLI) | Verdict `INCONCLUSIVE`, reason `TASK_CANCELLED`/`PENDING_ACTION_DENIED`; host/isolated unchanged |
| Docker integration trong full suite của Reviewer | **FAIL** | **3 failed / 246 passed / 6 skipped**; full suite sau các fix CP-018/CP-021 của implementer pass **260 passed / 6 skipped** nhưng không thay thế kết quả Reviewer |
| Phase 4 được chấp nhận | PENDING | Cần Project Owner phê duyệt sau review |

## 10. Giới hạn đã biết và vấn đề chưa giải quyết

1. **Docker Desktop/Docker CLI không ổn định trong môi trường này.** Reviewer ghi nhận
   `uv run pytest -q` FAIL với **3 failed / 246 passed / 6 skipped**; 3 lỗi là Docker integration
   test timeout trong Docker CLI. Implementer tái hiện **3 failed / 4 passed** trong
   Docker-focused retest; sau các fix CP-018/CP-021, full suite pass **260 passed / 6 skipped**. Vì
   lỗi không tất định và smoke test có từ Phase 1 cũng từng timeout, trạng thái Docker integration
   vẫn được ghi là environment-blocked/failed, không phải PASS. Khi Docker engine ổn định, cần
   rebuild image và chạy lại:
   `docker build -f docker/Dockerfile.sandbox -t codepilot-sandbox:phase1 docker`.
2. **Dependency của sandbox image.** Image có Python và pytest, không có runtime dependency của
   project. Test command cần package bên thứ ba phải dùng image được build riêng hoặc dependency
   được cài sẵn; network bị tắt trong lúc test.
3. **Bind mount workspace writable.** Phase 4 mount isolated workspace read/write để test chạy
   bình thường và phát hiện net change. Phase 5 vẫn cần cho copied workspace isolation và
   hardening filesystem race/symlink-swap mạnh hơn. Thay đổi tạm thời bị revert trước verification
   sẽ không quan sát được.
4. **Chính sách kích thước baseline.** Baseline/diff evidence bị giới hạn bởi
   `repository.max_total_bytes`, `max_files` và scan limit. Repository lớn hơn sẽ cho
   `INCONCLUSIVE` thay vì đọc không giới hạn.
5. **Cách triển khai diff.** CP-018 tạo git-style unified diff bằng Python thay vì gọi
   `git diff`. Đây là chủ đích vì isolated workspace loại trừ `.git` và tránh chạy git
   configuration do repository kiểm soát. Reviewer yêu cầu hành vi `git diff` literal cần nêu
   finding.
6. **Redaction test output dựa trên pattern.** Các credential shape đã biết được redact, nhưng
   secret không khớp pattern do test in ra vẫn có thể xuất hiện trong bounded evidence. Container
   không nhận API key hoặc host environment secret, và file bị loại trừ không được copy.
7. **Không có kiểm tra sanity về số lượng test.** Test command exit 0 mà không chạy test nào vẫn
   được generic runner coi là pass. Command đã cấu hình là trusted harness configuration.
8. **Chưa chạy xác minh Gemini live.** Verification/recovery tests dùng deterministic scripted
   provider. Không cần gọi Gemini live cho bằng chứng unit/integration của Phase 4.
9. **Report cancel/deny không có test evidence.** `/cancel` và `/deny` tạo `TaskReport` với verdict
   `INCONCLUSIVE` và reason `TASK_CANCELLED`/`PENDING_ACTION_DENIED`; report không có test run mới
   vì verification chưa chạy cho terminal outcome đó.
10. **Quyền trên Linux root-host.** Trên POSIX host chạy Harness bằng root, container dùng UID
    10001 trong khi isolated workspace chỉ root truy cập được. Có thể cần điều chỉnh permission;
    môi trường Windows/Docker Desktop hiện tại có lúc chạy được nhưng lần Reviewer bị timeout.
    Đây là giới hạn portability/môi trường, không phải host-fallback path.
11. **Fail closed khi có skipped entry.** Bất kỳ entry nào bị skip do permission, race hoặc giới
    hạn enumeration đều làm verification `INCONCLUSIVE`. Đây là hành vi an toàn theo yêu cầu,
    nhưng có thể giảm tỷ lệ PASS trên filesystem có entry không truy cập được.

## 11. Yêu cầu review

Vui lòng review:

1. `src/codepilot/execution/docker_runtime.py` về sandbox flag, bounded output, timeout, cleanup
   và hành vi không fallback sang Host.
2. `src/codepilot/verification/diff.py`, `src/codepilot/tools/workspace.py` và `engine.py` về
   baseline trust, phát hiện thay đổi unauthorized, thứ tự ưu tiên policy, xử lý `INCONCLUSIVE`
   và các fix CP-018 fail-closed cho symlink/reparse point (kể cả link ẩn trong `.ssh/`,
   `secrets/`), skipped entry, excluded entry (`.env`, `secrets/`, `.ssh/`) và chống giả mạo
   metadata signature.
3. `src/codepilot/agent/loop.py` về retry budget, state transition, theo dõi approved hash, lưu
   giữ bằng chứng và terminal report cho `cancel()`/`deny()`.
4. `src/codepilot/verification/report.py` và `cli/main.py` về độ đầy đủ của report, forced
   `INCONCLUSIVE` reason và render an toàn cho `/cancel`/`/deny`.
5. Regression tests symlink added/replaced/stable-trong-baseline, skipped baseline/current,
   excluded file/directory added/modified/stable, rewrite cùng size + restored mtime, signature
   không khả dụng, engine fail-closed và cancel/deny report/workspace-unchanged; bảo đảm test skip
   chỉ khi OS không tạo được symlink.
6. Test coverage và giới hạn môi trường Docker đã ghi nhận ở trên, đặc biệt 3 lỗi Docker CLI
   timeout trong lần xác minh độc lập của Reviewer.

## 12. Fix review — excluded entries lọt khỏi CP-018

### 12.1. Finding và phạm vi

`Workspace.list_files` bỏ qua hoàn toàn các entry khớp exclusion (`.env`, `secrets/`, `.ssh/`,
credentials, khóa riêng tư...). Vì vậy khi một excluded entry mới xuất hiện trong sandbox, nó
không có trong baseline lẫn listing hiện tại; `GitDiffVerifier` không thấy thay đổi và CP-018 có
thể trả `PASS` dù workspace đã có thêm secret. Fix này chỉ xử lý finding HIGH đó trong CP-018,
không đổi exclusion hiện có, không đọc nội dung excluded entry và không triển khai Phase 5.

### 12.2. Thay đổi

- `src/codepilot/tools/workspace.py`
  - Thêm `ExcludedEntry` và trường `FileListing.excluded` để ghi **metadata** của entry bị loại
    trừ: đường dẫn tương đối, loại (`dir`/`link`) và với file là `size` + `mtime_ns`.
  - Phân loại exclusion: `sensitive()` nhận diện các đường dẫn credentials (`secrets/`, `.ssh/`,
    `.aws`, `.kube`, `.env*`, `*.pem`, `*.key`, `id_rsa`...). Chỉ các entry này được duyệt metadata
    (không đọc nội dung, không theo symlink); thư mục tooling/cache (`.git`, `.venv`,
    `__pycache__`, `.pytest_cache`, `node_modules`, `dist`, `build`...) vẫn được loại trừ như cũ vì
    test run tạo ra chúng một cách hợp lệ.
  - Enumeration metadata chịu cùng giới hạn `max_scan_entries`/`timeout_seconds`; lỗi hoặc vượt
    giới hạn tăng `skipped`/`truncated` để verification fail closed.
- `src/codepilot/verification/diff.py`
  - `WorkspaceSnapshot` lưu map `path -> signature` của excluded entry tại baseline.
  - `GitDiffVerifier.compare` so sánh baseline/current và thêm policy violation không retryable:
    `EXCLUDED_ENTRY_ADDED`, `EXCLUDED_ENTRY_REMOVED`, `EXCLUDED_ENTRY_MODIFIED`.
  - Entry bị thay đổi được báo là binary change, `authorized=False`, nằm trong `unauthorized`;
    nội dung không bao giờ được đưa vào diff.
  - Entry đã tồn tại ổn định trong baseline (cùng signature) không tạo violation; đây là trường
    hợp "đã có baseline" được xử lý riêng để không phá hành vi PASS bình thường.
  - Bất kỳ `skipped`/`truncated`/`linked` nào vẫn giữ nguyên hành vi fail closed trước đó.

### 12.3. Bằng chứng kiểm thử

- RED trước fix (stash source, giữ tests): 6 failed / 26 passed; các test excluded file, excluded
  directory và modified-baseline fail đúng như finding.
- GREEN sau fix: `uv run pytest -q tests/unit/test_verification_diff.py
  tests/unit/test_verification_engine.py` -> **32 passed**.
- Regression tests mới:
  - `test_excluded_entries_are_reported_as_metadata_only` (listing chỉ metadata, `read_text`
    vẫn bị `ACCESS_DENIED`).
  - `test_new_excluded_file_fails_closed` (`.env` mới -> `EXCLUDED_ENTRY_ADDED`, diff không chứa
    giá trị synthetic).
  - `test_new_excluded_directory_fails_closed` (`secrets/token.txt` mới).
  - `test_modified_baseline_excluded_entry_fails_closed` (file trong `secrets/` bị sửa ->
    `EXCLUDED_ENTRY_MODIFIED`).
  - `test_stable_baseline_excluded_entry_stays_clean` (entry có từ baseline, không đổi -> sạch).
  - `test_tooling_cache_churn_is_not_a_policy_violation` (`.pytest_cache` không gây nhiễu).
  - Engine-level: `.env` mới và `.ssh/id_ed25519` mới -> verdict `FAIL` non-retryable; baseline
    excluded ổn định -> `PASS`.
- Full suite: `uv run pytest -q` -> **269 passed, 6 skipped**.
- Docker integration: `docker version` server **29.8.2**;
  `uv run pytest -q tests/integration/test_verification_docker.py -rs` -> **6 passed** trong
  14.84s. Đây là kết quả của môi trường hiện tại, không thay thế các lần timeout lịch sử của
  Reviewer.
- `uv run ruff check .` -> All checks passed.
- `git diff --check` -> không có whitespace error (chỉ còn cảnh báo LF/CRLF của Git).

### 12.4. Giới hạn của fix

1. **Metadata signature, không phải hash nội dung.** Phát hiện thay đổi dựa trên metadata của file;
   vòng re-review ở mục 12.6 đã tăng cường bằng `ctime`/NTFS change-time, `st_dev`/`st_ino`,
   `nlink` và attributes. Không đọc nội dung là yêu cầu bảo mật nên không dùng hash.
2. **Thư mục tooling/cache không được metadata-track.** `.git`, `.venv`, `__pycache__`,
   `.pytest_cache`, `node_modules`, `dist`, `build`... vẫn nằm ngoài phạm vi như trước; nếu track
   chúng, mọi test run tạo `.pytest_cache` sẽ làm verification fail. Đây là giới hạn có chủ đích,
   không phải nới exclusion.
3. **Enumeration cap.** `secrets/` cực lớn hoặc filesystem chậm có thể chạm `max_scan_entries`/
   timeout; khi đó verification trả `INCONCLUSIVE`, không PASS.

### 12.5. Trạng thái

- Commit fix: `10db3d8 fix(verification): fail closed on excluded sandbox entries`.
- Fix finding HIGH: **PASS (unit + Docker integration trong lần chạy này)**.
- Phase 4 acceptance: **PENDING** — chỉ Project Owner phê duyệt sau review.
- Không bắt đầu Phase 5; không merge; không apply patch lên Host.

### 12.6. Re-review vòng 2 — symlink ẩn và forged metadata

**Finding 1 (HIGH):** symlink/reparse point nằm dưới excluded sensitive directory bị che. Baseline
`.ssh/config` là symlink được ghi `ExcludedEntry(..., "link")` nhưng không vào `linked_paths`, nên
`compare` thấy signature không đổi, `policy_violations` rỗng, `incomplete=False` và engine có thể
`PASS`.

- Fix: mọi link/reparse point trong nhánh metadata của `list_files` được thêm vào `linked_paths`;
  `WorkspaceSnapshot` lưu `linked`; khi baseline có link, `GitDiffVerifier.compare` trả
  `LINKED_ENTRY_DETECTED` cùng binary `FileChange` unauthorized thay vì chỉ `incomplete`.
- Không follow target, không mở/đọc nội dung. Hệ quả có chủ đích: link có sẵn từ baseline cũng
  `FAIL` non-retryable (trước đây baseline link chỉ cho `INCONCLUSIVE`).

**Finding 2 (HIGH):** signature `file:size:mtime_ns` bị giả mạo bằng cách ghi nội dung khác cùng
kích thước rồi `os.utime` khôi phục mtime.

- Fix: `file_signature()` dùng `size`, `mtime_ns`, `ctime_ns`, `st_dev`, `st_ino`, `st_nlink` và
  file attributes; trên Windows đọc thêm NTFS ChangeTime qua `GetFileInformationByHandleEx` trên
  handle metadata (không đọc byte nội dung). Trên POSIX dùng `st_ctime` (change time).
- Nếu không lấy được signature (mở file/API lỗi), entry tăng `skipped`; baseline/current không đầy
  đủ nên verification trả `INCONCLUSIVE`, không `PASS`.
- Không log, copy hoặc đưa nội dung secret vào report; test chỉ dùng giá trị synthetic.

**Portability:**

- Windows: `st_ctime` là creation time nên không đủ; ChangeTime của NTFS là bằng chứng bổ sung.
  Nếu API/handle không khả dụng, fail closed như trên.
- POSIX: `st_ctime` là inode change time; `os.utime` làm thay đổi ctime nên phát hiện được forged
  mtime.
- Filesystem không duy trì change time đáng tin cậy (một số network/FAT mount) có thể làm bằng
  chứng yếu đi; khuyến nghị workspace local NTFS/ext4/APFS và ghi nhận đây là residual limitation.

**Bằng chứng:**

- RED trên code trước fix (`250a6c7^`): 4 failed, trong đó engine trả `PASS` cho cả baseline
  symlink và forged mtime.
- GREEN focused: `tests/unit/test_verification_diff.py` + `tests/unit/test_verification_engine.py`
  -> **37 passed**.
- Regression tests mới:
  - `test_stable_baseline_sensitive_symlink_fails_closed` (`.ssh/config` link có từ baseline ->
    `LINKED_ENTRY_DETECTED`, unauthorized, không lộ target content).
  - `test_stable_excluded_symlink_fails_verification` (engine `FAIL` non-retryable).
  - `test_same_size_rewrite_with_restored_mtime_is_detected` (cùng size + restored mtime ->
    `EXCLUDED_ENTRY_MODIFIED`).
  - `test_same_size_rewrite_with_restored_mtime_fails_verification` (engine `FAIL`).
  - `test_unavailable_sensitive_signature_is_inconclusive` (signature API lỗi -> `INCONCLUSIVE`,
    không PASS).
- Full suite: **274 passed, 6 skipped**.
- Integration: `uv run pytest -q tests/integration -rs` -> **7 passed, 6 skipped** (6 skip do
  ripgrep không có trong môi trường); `tests/integration/test_verification_docker.py` -> **6 passed**.
- `uv run ruff check .` -> All checks passed; `git diff --check` -> không có whitespace error.

**Commit fix:** `250a6c7 fix(verification): fail closed on hidden links and forged metadata`.
Commit cập nhật báo cáo là commit follow-up ngay sau đó. Fix cả hai finding: **PASS (unit +
Docker integration trong lần chạy này)**; Phase 4 acceptance vẫn **PENDING**.

Triển khai dừng tại đây để Architect / Reviewer review. Không bắt đầu Phase 5, không apply patch
lên Host và không tự khẳng định phase đã được phê duyệt.
