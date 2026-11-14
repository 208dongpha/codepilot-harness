# Báo cáo triển khai Phase 5 — Sandbox Hardening

> **Phạm vi:** chỉ CP-022 đến CP-025, theo thứ tự và checkpoint đã duyệt ngày 2026-11-14.
> **Trạng thái:** Đã triển khai xong CP-022–CP-025 và regression tests; đã xử lý finding HIGH
> re-review về parent-swap TOCTOU trong host apply; Docker integration **BLOCKED** vì daemon không
> khả dụng trong môi trường hiện tại; đang chờ security review độc lập.
> **Phê duyệt phase:** Không tự khẳng định. Chỉ Project Owner được phê duyệt Phase 5.

## 1. Tóm tắt

Phase 5 tăng cường cô lập workspace, giới hạn thực thi, áp dụng thay đổi đã verify lên Host với
approval riêng, và bổ sung security regression tests:

1. **CP-022 — Workspace Isolation:** bản copy cô lập fail closed khi enumeration không đầy đủ,
   entry không đọc/copy an toàn, path escape, hoặc vượt byte budget; chỉ giữ executable bit, loại
   bỏ setuid/setgid/sticky; xác minh hash và tập path sau copy.
2. **CP-023 — Execution Restrictions:** xác minh các Docker control đã có; bổ sung `--memory-swap`
   bằng `--memory` và `--ulimit core=0:0`; chuyển AST parsing sang worker process riêng có giới hạn
   CPU, memory/address-space, process count và timeout do OS cưỡng chế.
3. **CP-024 — Host Patch Application:** plan chỉ từ verified final diff; approval riêng, exact,
   single-use; recheck Host theo snapshot đầu task; từ chối conflict/symlink swap; apply qua
   temporary file cùng thư mục, verify hash, rollback toàn bộ nếu một bước thất bại.
4. **CP-025 — Security Testing:** regression cho traversal, symlink escape, secret leakage,
   command injection, prompt injection, unauthorized host writes, Docker controls, timeout,
   cleanup và AST worker limits.

Phase 1–4 được giữ nguyên. Không triển khai Phase 6, không merge, không tự động apply lên Host.

## 2. Các task đã hoàn thành

| Task | Trạng thái | Hành vi đã giao |
|---|---|---|
| CP-022 — Workspace Isolation | Đã triển khai | Copy text-only vào temp workspace; fail closed với enumeration/entry/path/byte không an toàn; metadata mode an toàn; verify hash + path set; cleanup khi lỗi. |
| CP-023 — Execution Restrictions | Đã triển khai | Giữ network none, non-root, cap-drop ALL, no-new-privileges, read-only, pids/memory/CPU/timeout/cleanup; thêm memory-swap và core=0; AST worker process có OS limits + parent timeout. |
| CP-024 — Host Patch Application | Đã triển khai | Host snapshot đầu task; plan ID exact; approval single-use; conflict/symlink rejection; transactional write + rollback; CLI `/apply <plan-id>`, `/discard`. |
| CP-025 — Security Testing | Đã triển khai | `tests/security/` cho isolation, host apply và execution boundaries; Docker checks skip BLOCKED trung thực. |

### Checkpoint

- **Checkpoint A (sau CP-022):** `uv run pytest -q` -> **273 passed, 14 skipped** (1 skip POSIX
  mode trên Windows, 13 skip Docker/ripgrep). PASS cho regression isolation/filesystem.
- **Checkpoint B (sau CP-023, trước CP-024):** unit suite -> **287 passed, 14 skipped**; Docker
  integration không chạy được vì daemon unavailable -> **BLOCKED**, không fallback Host.

## 3. Các chức năng đã triển khai

### 3.1. CP-022 — Workspace Isolation

- `IsolatedWorkspace.create` từ chối `ISOLATION_INCOMPLETE` khi listing bị truncate/skip, khi đọc
  file không thuộc nhóm omit an toàn (binary/quá lớn/sensitive), khi path không hợp lệ, hoặc khi
  `ISOLATION_TOO_LARGE`.
- Copy qua exclusive/no-follow write; parent directory được kiểm tra không phải link và nằm trong
  root; fsync trước khi hoàn tất.
- Chỉ giữ executable bit (`0755` hoặc `0644`); bỏ setuid/setgid/sticky.
- Sau copy, verify từng SHA-256 và tập path của isolated workspace; lỗi bất kỳ -> cleanup temp.
- CLI bắt `PatchError` khi isolation thất bại và không rò rỉ provider.

### 3.2. CP-023 — Execution Restrictions

- Docker controls đã có được giữ nguyên: `--network none`, non-root `--user`, `--cap-drop ALL`,
  `--security-opt no-new-privileges`, `--read-only`, `--tmpfs /tmp`, `--pids-limit`, `--cpus`,
  `--memory`, timeout bounded, cleanup có verify absence, không mount Docker socket, không env
  secret, không host fallback.
- Bổ sung: `--memory-swap` bằng `--memory` để swap không nới effective memory; `--ulimit core=0:0`
  để không ghi core dump vào workspace.
- AST worker:
  - `ast_worker_main.py` chạy bằng `python -I -B`, nhận source qua stdin, chỉ trả symbol metadata.
  - POSIX: `RLIMIT_AS` + `RLIMIT_CPU`; Windows: Job Object với process memory limit, job user-time
    limit, active-process limit, kill-on-close.
  - Parent enforce timeout, giới hạn input/output, validate protocol; lỗi/limits unavailable ->
    map incomplete (`ast_timeout`, `ast_limits`, `ast_failure`, `input_incomplete`), không fallback
    parse unbounded trong process chính.
  - Worker nhận environment tối thiểu, không có `GEMINI_API_KEY`.
- Cấu hình mới: `repository.ast_timeout_seconds`, `repository.ast_memory_mb`,
  `repository.ast_cpu_seconds`.

### 3.3. CP-024 — Host Patch Application

- `HostPatchApplier.begin_task()` chụp Host snapshot text-only trước task; snapshot không đầy đủ ->
  host apply unavailable (fail closed), task vẫn chạy sandbox.
- `prepare(diff, baseline_hashes, authorized_hashes)` chỉ nhận diff complete, authorized, không
  policy violation, không binary/truncated; yêu cầu baseline isolated khớp Host snapshot và nội
  dung isolated khớp hash đã authorize.
- Plan ID = hash của diff + các entry (path/change/expected/new); hiển thị diff trước approval.
- `approve(plan_id)` exact; `apply(plan_id)` chỉ dùng một lần, recheck Host ngay trước ghi, từ chối
  conflict hoặc symlink/reparse, không ghi nếu bằng chứng không đầy đủ.
- Apply tuần tự qua temp file cùng thư mục + `os.replace`, verify hash sau ghi; lỗi giữa chừng ->
  rollback file gốc, xóa file mới và thư mục đã tạo; rollback thất bại -> `HOST_ROLLBACK_FAILED`.
- CLI: `/apply <plan-id>` và `/discard`; task mới bị chặn khi còn pending host plan; Host không đổi
  trước approval.

### 3.4. CP-025 — Security Testing

- `tests/security/test_isolation_security.py`: traversal/excluded path, symlink không follow/copy,
  secret không vào isolated copy, prompt injection là data.
- `tests/security/test_host_apply_security.py`: Host không đổi trước approval, conflict từ external
  edit, symlink swap bị từ chối.
- `tests/security/test_execution_security.py`: Docker flags, command không qua shell, cleanup
  verified, AST worker isolated/time-bounded, Docker live check skip BLOCKED khi daemon unavailable.

## 4. Quyết định kiến trúc

1. **Fail closed thay vì copy một phần:** thiếu listing/entry an toàn thì không tạo isolated
   workspace; tránh test evidence dựa trên workspace thiếu file.
2. **Metadata-only cho secret:** giữ nguyên exclusion của CP-018; không đọc nội dung secret.
3. **AST worker tách process:** đây là cách duy nhất cưỡng chế được CPU/memory/timeout mà không
   chạy repository code trên Host; worker chỉ dùng stdlib, không import project code.
4. **Host apply là quyền riêng:** không tái sử dụng `PermissionEngine` của sandbox patch; plan ID
   bind diff + snapshot + authorized hash, tránh approve một đằng apply một nẻo.
5. **Rollback thay vì “best effort”:** multi-file apply không thể atomic ở OS level; rollback +
   verify hash là mức bảo đảm thực tế, phần còn lại ghi trong limitations.

## 5. Các file đã tạo hoặc sửa

Tạo mới:

- `src/codepilot/context/ast_worker.py`
- `src/codepilot/context/ast_worker_main.py`
- `src/codepilot/patch/guard.py`
- `src/codepilot/patch/host.py`
- `tests/unit/test_ast_worker.py`
- `tests/unit/test_host_apply.py`
- `tests/security/test_isolation_security.py`
- `tests/security/test_host_apply_security.py`
- `tests/security/test_execution_security.py`
- `docs/PHASE_5_IMPLEMENTATION_REPORT.md`

Sửa:

- `src/codepilot/patch/tool.py` (copy isolation, safe_parts)
- `src/codepilot/cli/main.py` (isolation error, host apply commands)
- `src/codepilot/config.py` (AST worker limits)
- `src/codepilot/context/repository_map.py`, `src/codepilot/context/builder.py` (worker integration,
  omission reasons)
- `src/codepilot/execution/docker_runtime.py` (swap/core limits)
- `src/codepilot/verification/engine.py` (baseline hashes)
- `src/codepilot/agent/loop.py` (authorized hashes)
- `README.md` (host apply commands)
- `tests/unit/test_patch_tool.py`, `tests/unit/test_repository_map.py`,
  `tests/unit/test_docker_runtime.py`, `tests/unit/test_cli.py`,
  `tests/integration/test_verification_docker.py`

`AGENTS.md`, `IMPLEMENTATION_PLAN.md`, `.env`, và `sandbox-demo/` (untracked) không bị sửa/stage.

## 6. Kết quả kiểm thử

| Lệnh | Kết quả |
|---|---|
| `uv sync` | PASS — 41 packages resolved/checked |
| `uv run pytest -q` | **318 passed, 15 skipped** |
| `uv run pytest -q tests/security -rs` | **20 passed, 1 skipped** (Docker BLOCKED) |
| `uv run pytest -q tests/integration -rs` | **13 skipped** — Docker daemon unavailable và ripgrep unavailable |
| `uv run ruff check .` | PASS |
| `git diff --check` | PASS (chỉ cảnh báo LF/CRLF của Git) |
| Docker `docker info` | **FAIL/BLOCKED** — không kết nối được daemon; không start/stop Docker Desktop |

Skip breakdown: 13 test cần Docker integration, 1 test POSIX mode bits trên Windows, 1 Docker
security live check.

## 7. Kiểm tra bảo mật

- **Workspace escape/traversal:** `Workspace.resolve` + `safe_parts` chặn `..`, absolute path,
  drive, excluded name, control character; regression traversal PASS.
- **Symlink/reparse:** không follow; copy bỏ qua link; Host apply từ chối link; verification
  Phase 4 vẫn fail closed.
- **Secret protection:** `.env`, `secrets/`, credentials không được đọc/copy; AST worker không nhận
  API key; report không chứa nội dung secret.
- **Command injection:** command được `shlex.split` thành list và truyền trực tiếp cho Docker,
  không dùng shell; regression PASS.
- **Unauthorized host writes:** Host chỉ đổi sau approval exact; mọi thay đổi ngoài snapshot bị
  conflict; apply dùng directory-handle guard chống parent symlink/junction swap; rollback không
  ghi đè external edit; regression PASS.
- **Resource limits:** Docker flags được unit-test; AST worker dùng OS limits thực và parse thành
  công trong môi trường hiện tại.
- **Docker runtime enforcement:** **BLOCKED** — không có bằng chứng chạy thật trong lần này; không
  tuyên bố PASS cho network/memory/timeout/cleanup ở runtime.

## 8. Git status và commit

Commit chức năng:

```text
14ad9fa feat(sandbox): harden isolated workspace copy
d4eb583 feat(context): run AST parsing in a bounded worker
11427d4 feat(sandbox): cap swap and disable core dumps
59b7625 feat(host): apply verified diffs with exact single-use approval
6e384e9 test(security): cover phase 5 isolation and execution boundaries
6c124be fix(host): prevent parent-swap escapes during patch apply
0055a71 fix(host): leave created directories instead of unsafe rollback rmdir
```

Commit báo cáo này là commit follow-up. `sandbox-demo/` vẫn untracked và không được stage.

## 9. Tiêu chí chấp nhận

| Tiêu chí | Trạng thái | Ghi chú |
|---|---|---|
| Host không thay đổi trước approval | **PASS (unit)** | `test_prepare_is_side_effect_free...`, security host tests |
| Approved patches được áp dụng | **PASS (unit)** | Modify/add/delete + nested directory; hash verified |
| Patch conflicts bị từ chối | **PASS (unit)** | External edit, stale isolated baseline, symlink swap |
| Parent-directory swap race | **PASS (unit)** | POSIX dir_fd, Windows no-delete directory handles; outside paths untouched |
| Rollback dọn directory race | **PASS (unit)** | Không dùng pathname rmdir; empty dirs được giữ lại, outside tree untouched |
| Rollback không ghi đè external edit | **PASS (unit)** | Concurrent edit -> `HOST_ROLLBACK_CONFLICT`, file giữ nguyên |
| Nền tảng không hỗ trợ guard | **PASS (unit)** | `HOST_UNSUPPORTED` trước mutation |
| Dangerous commands bị chặn | **PASS (unit/architecture)** | List args, no shell, no host fallback; live Docker BLOCKED |
| Secrets được bảo vệ | **PASS (unit)** | Excluded paths không đọc/copy; worker env tối thiểu |
| Resource limits hoạt động | **PASS (unit/AST live)** / **BLOCKED (Docker runtime)** | Docker flags unit-tested; daemon unavailable |
| Không có unrestricted host execution | **PASS (unit)** | `run_workspace` fail closed, no fallback |
| Workspace isolation | **PASS (unit)** | Incomplete listing/read/path/byte -> fail closed |
| Docker integration | **BLOCKED** | 13 tests skipped; không có bằng chứng runtime |
| Phase 5 được chấp nhận | **PENDING** | Cần security review độc lập và Project Owner approval |

## 10. Giới hạn đã biết và vấn đề chưa giải quyết

1. **Docker daemon unavailable trong môi trường hiện tại.** Toàn bộ Docker integration (network,
   memory/CPU limits, timeout, cleanup, image) là **BLOCKED**; unit tests chỉ chứng minh cấu hình
   flag, không chứng minh runtime enforcement. Không start/stop Docker Desktop theo yêu cầu.
2. **Host apply không atomic ở mức đa-file.** Rollback được thực hiện khi một bước lỗi, nhưng nếu
   process bị kill/power loss giữa apply, rollback có thể không chạy. Created parent directories
   được giữ lại sau rollback thay vì rmdir pathname, để tránh parent-swap escape; empty directories
   còn lại là side effect chấp nhận được. Đây là giới hạn của filesystem API hiện tại; cần
   journal/checkpoint ở phase sau nếu muốn crash-consistency tuyệt đối.
3. **TOCTOU còn lại ở final file.** Parent-directory swap đã được loại bỏ bằng handle/dir_fd
   guard; tuy nhiên giữa lần đọc content cuối và `rename`/`replace`, một writer khác có thể thay
   chính file đích. Không có portable compare-and-swap cho filesystem; rollback phát hiện content
   khác và trả `HOST_ROLLBACK_CONFLICT` thay vì ghi đè, nhưng apply vẫn có thể thắng race hiếm này.
   Mitigation: content check trong cùng guard ngay trước write, atomic rename, rollback conflict
   detection.
4. **Line ending:** Host apply ghi nội dung đã normalize LF (do `read_text` chuẩn hóa), nên file
   CRLF có thể đổi sang LF. Không ảnh hưởng verification hash nhưng có thể tạo diff lớn trên repo
   Windows.
5. **Snapshot/plan bị giới hạn bởi `max_total_bytes`** (mặc định 4 MB) và chỉ text; repo lớn hơn sẽ
   không tạo được host plan (fail closed).
6. **Host apply từ chối file chứa SECRET_CONTENT** (do `read_text` bảo vệ); đây là fail closed có
   chủ đích, có thể chặn apply với code chứa chuỗi giống secret.
7. **AST worker limits phụ thuộc OS.** POSIX dùng rlimit; Windows dùng Job Object. Nếu môi trường
   chặn job assignment hoặc rlimit, map được đánh dấu incomplete (`ast_limits`) thay vì parse
   unbounded. Input batch > 8 MB -> `input_incomplete`.
8. **New file mode:** Host apply tạo file mới với mode `0644`; executable bit của file mới không
   được suy ra từ diff.
9. **Docker image/flag:** các flag mới (`--memory-swap`, `--ulimit core=0:0`) là runtime flags,
   không cần rebuild image, nhưng chưa được xác minh trên daemon thật.

## 11. Yêu cầu review

Vui lòng review:

1. `src/codepilot/patch/tool.py` — fail-closed copy, metadata/mode, hash verification.
2. `src/codepilot/context/ast_worker.py` + `ast_worker_main.py` — protocol, OS limits,
   portability và fail-closed khi limits unavailable.
3. `src/codepilot/patch/host.py` — plan binding, approval exact/single-use, snapshot recheck,
   conflict/no-partial-write/rollback.
4. `src/codepilot/cli/main.py` — luồng `/apply <plan-id>`, `/discard`, chặn task mới, không đổi
   Host trước approval.
5. `tests/security/` — coverage và các skip BLOCKED trung thực.
6. Docker integration khi daemon khả dụng: chạy lại `tests/integration` và xác nhận network,
   memory/swap, pids, core=0, timeout, cleanup trước khi coi Phase 5 hoàn tất.

## 12. Fix re-review — Host apply parent-swap TOCTOU

### 12.1. Finding và phạm vi

Reviewer xác nhận `_recheck()` chỉ validate bằng pathname; sau đó `_commit()`/`_write_entry()`
resolve lại component và `mkstemp`/`os.replace` dùng path string. Một local writer có thể đổi
parent directory thành symlink/reparse point trong cửa sổ validation-to-mutation và chuyển hướng
ghi ra ngoài `host.root`. Rollback cũng có thể ghi đè external edit. Fix chỉ trong CP-024/CP-025.

### 12.2. Thay đổi

- Thêm `src/codepilot/patch/guard.py`:
  - **POSIX:** mở directory chain bằng `os.open` với `O_DIRECTORY|O_NOFOLLOW`, giữ parent fd và
    dùng `os.open/mkdir/unlink/rename(..., dir_fd=...)`; rename và ghi không phụ thuộc path string,
    nên parent bị rename/replace cũng không chuyển hướng.
  - **Windows:** mở handle cho mọi component bằng `CreateFileW` với
    `FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT`, share mode không có
    `FILE_SHARE_DELETE`, kiểm tra `FILE_ATTRIBUTE_DIRECTORY` và từ chối
    `FILE_ATTRIBUTE_REPARSE_POINT`. Handle được giữ suốt mutation, nên rename/delete parent bị OS
    chặn; reparse/junction bị từ chối. `os.replace` chỉ thay entry cuối, không follow symlink;
    read/write mở target với `FILE_FLAG_OPEN_REPARSE_POINT`.
  - Nền tảng khác hoặc thiếu `dir_fd` support: `GuardUnsupported` -> host apply fail closed
    `HOST_UNSUPPORTED` trước mutation.
- `HostPatchApplier._write_entry`, `_delete_entry`, `_verify_applied`, `_restore_entry` dùng guard
  thay cho `_prepare_target`/`_atomic_write` path-based.
- Trước write, recheck content hash **trong cùng guard** đang giữ handle; sau write verify hash
  qua guard mới.
- Rollback đọc current content trước khi restore:
  - Chỉ restore khi current đúng bằng content đã ghi (hoặc đã bằng bản gốc).
  - External edit/delete -> không ghi đè, trả `HOST_ROLLBACK_CONFLICT`.
  - Guard/IO failure -> `HOST_ROLLBACK_FAILED`.
  - Parent swap trong rollback không ghi ra ngoài root.

### 12.3. Regression tests

- `test_parent_swap_after_validation_cannot_redirect_write`: rename parent + tạo symlink trong
  `GuardedPath.write`, sau validation và trước mutation; assert file ngoài root giữ nguyên, file
  unrelated giữ nguyên, nội dung apply nằm trong thư mục gốc (POSIX) hoặc apply fail closed an toàn.
- `test_rollback_does_not_overwrite_concurrent_edit`: external edit file đã ghi trong lúc rollback
  -> `HOST_ROLLBACK_CONFLICT`, external content giữ nguyên, entry còn lại được restore.
- `test_rollback_parent_swap_cannot_escape`: swap parent ngay trước rollback; assert outside path
  và unrelated file không đổi, lỗi recovery được báo chính xác.
- `test_unsupported_platform_fails_before_host_mutation`: guard unsupported -> `HOST_UNSUPPORTED`,
  Host giữ nguyên.

### 12.4. Bằng chứng

- Focused host/guard/security tests: **16 passed** (7 unit host + 1 guard + 8 security) sau fix.
- Full suite: `uv run pytest -q` -> **318 passed, 15 skipped**.
- Security suite: `tests/security` -> **20 passed, 1 skipped** (Docker BLOCKED).
- `uv run ruff check .` -> PASS; `git diff --check` -> PASS.
- Docker vẫn **BLOCKED**; không start/stop Docker Desktop, không Host execution fallback.
- Commit fix: `6c124be fix(host): prevent parent-swap escapes during patch apply`.

### 12.5. Giới hạn

- Final-file race giữa content check và rename vẫn tồn tại như mục 10.3; không có portable CAS.
  Parent-directory escape đã được loại bỏ trên POSIX và Windows local NTFS.
- Windows guarantee phụ thuộc NTFS/OS cho phép mở directory handle không share delete; nếu môi
  trường chặn (policy/FS khác), guard fail closed `HOST_UNSUPPORTED`/`HOST_ROLLBACK_FAILED`.
- Multi-file crash consistency vẫn là rollback, không phải journal; process kill giữa apply có
  thể không rollback.
- Phase 5 acceptance vẫn **PENDING**.

### 12.6. Re-review vòng 2 — rollback rmdir race và directory ownership

**Finding:** `HostPatchApplier._rollback` vẫn dọn các directory đã tạo bằng `Path.rmdir()` dựa
trên pathname. Nếu một nested parent mới tạo bị rename và thay bằng symlink trỏ ra ngoài trước
cleanup, `rmdir` có thể đi theo parent symlink và xóa một empty child directory bên ngoài
`host.root`. Đồng thời `GuardedPath._open_windows` ghi `created_dirs` cả khi `os.mkdir` ném
`FileExistsError`, tức có thể nhận directory do writer khác tạo.

- Fix rollback: **không dùng pathname rmdir nữa**. Created parent directories được giữ lại; đây là
  thay đổi không thể ảnh hưởng file content ngoài `host.root`. Leftover empty directories là
  side effect nhỏ, được ghi nhận là giới hạn có chủ đích thay vì mở lại đường escape.
- Fix Windows ownership: chỉ append `created_dirs` khi `os.mkdir` thực sự thành công; trường hợp
  `FileExistsError` không claim directory của writer khác.
- Regression tests:
  - `test_rollback_parent_swap_does_not_remove_outside_directory`: plan tạo `aaa/sub/new.py`, lỗi
    entry thứ hai kích hoạt rollback; trước rollback `aaa` bị rename và thay bằng symlink tới
    `outside` có empty `outside/sub`; assert `outside/sub` còn nguyên, file ngoài và unrelated file
    không đổi.
  - `test_windows_guard_does_not_claim_an_existing_directory`: mô phỏng writer khác tạo directory
    giữa `lexists` và `mkdir`; assert `created_dirs == []`.
- Evidence: focused 16 passed; full **318 passed, 15 skipped**; security **20 passed, 1 skipped**;
  Ruff PASS; diff-check PASS; Docker **BLOCKED**.
- Commit fix: `0055a71 fix(host): leave created directories instead of unsafe rollback rmdir`.

Triển khai dừng tại đây để security review. Không bắt đầu Phase 6, không merge, không tự khẳng
định Phase 5 đã được phê duyệt.
