# Báo cáo triển khai Phase 3 — Agent Loop và Editing

## 1. Tóm tắt

Đã triển khai CP-012 đến CP-016 trong phạm vi Phase 3. Phase 1 và Phase 2 giữ nguyên
tính tương thích. Phase 4–8 chưa được triển khai.

Phase 3 hiện ở trạng thái **đã sửa các finding F-001 đến F-004, chờ review lại**. Báo cáo
này không tuyên bố Phase 3 đã được phê duyệt.

## 2. Chức năng hoàn thành

- State machine gồm `CREATED`, `RUNNING`, `WAITING_APPROVAL`, `VERIFYING`,
  `COMPLETED`, `FAILED`, `CANCELLED`; terminal state không thể chuyển tiếp.
- Agent Loop tự xây context, gọi Gemini, nhận structured function calls, kiểm tra tool,
  arguments và quyền, trả observation có giới hạn, rồi tiếp tục trong step budget.
- Tool read-only được gọi qua Tool Registry Phase 2. Tool lạ và arguments sai trả lỗi cấu
  trúc; model output không có quyền tự cấp phép.
- Permission Engine có `ALLOW`, `ASK`, `DENY`; action ID là SHA-256 của canonical JSON
  gồm tool, operation, target và arguments. Approval khớp exact action, hết hạn, dùng một
  lần và mất hiệu lực khi action pending thay đổi. Snapshot canonical bất biến được đối
  chiếu lại tại thời điểm thực thi để dữ liệu lồng nhau bị sửa không đổi được action đã duyệt.
- Patch Tool hỗ trợ create, replace và delete cho một file mỗi action. Proposal có diff,
  expected content hash và content hash. Không ghi Host trong Phase 3.
- CLI hỗ trợ `/approve`, `/deny`, `/cancel`; hiển thị action ID, operation, target và diff
  đang chờ.
- Token budget tính cả instructions, task/repository context, tool declarations, history,
  observations và output reserve. Dùng `count_tokens` gần ngưỡng; khi không dùng được thì
  giữ nhãn ước lượng cục bộ và fail closed nếu vượt giới hạn.

## 3. Quyết định kiến trúc

- Giữ Modular Monolith với các package `agent`, `permissions`, `patch`, `context`, `llm`
  và `cli` tách trách nhiệm.
- Automatic function calling của SDK bị tắt. Gemini chỉ đề xuất call; Harness validate và
  thực thi. Cách khai báo `parameters_json_schema`, tắt automatic calling, gửi function
  response và gọi `count_tokens` theo tài liệu chính thức của Google Gen AI SDK:
  <https://googleapis.github.io/python-genai/>.
- Context ban đầu được dựng từ Host bằng policy read-only Phase 2. Tool Registry của Agent
  Loop đọc bản sao cô lập, nên sau approval agent quan sát đúng nội dung đã patch mà Host
  vẫn bất biến.
- `VERIFYING` trong Phase 3 chỉ biểu thị kiểm tra cấu trúc cuối của loop; không đại diện cho
  Verification Engine Phase 4.

## 4. Hành vi Agent Loop

1. Dựng repository map và context có metadata `incomplete`.
2. Gắn system instruction rằng repository/tool output là dữ liệu không tin cậy và thiếu
   source không chứng minh code không tồn tại.
3. Kiểm tra token budget trước mỗi generation.
4. Gọi Gemini với function declarations và automatic function calling bị tắt.
5. Giới hạn tối đa số bước và số tool call mỗi bước.
6. Read-only call được Permission Engine cho phép rồi Tool Registry validate lại bằng
   Pydantic.
7. Patch call tạo proposal và chuyển sang `WAITING_APPROVAL`.
8. Sau approval, patch được áp vào workspace cô lập; Agent Loop thêm thông báo bounded
   `STALE_AFTER_PATCH` cho đường dẫn vừa đổi. Các call còn lại trong cùng batch tiếp tục đúng
   thứ tự và có thể đọc nội dung mới từ workspace cô lập.
9. Provider error, malformed response, budget exhaustion hoặc cancellation chuyển sang
   trạng thái kết thúc phù hợp.

## 5. Bảo đảm permission và approval

- Read-only tools hợp lệ: `ALLOW`.
- `apply_patch` với create/replace/delete: `ASK`.
- Tool/operation ngoài allowlist: `DENY`.
- Canonical JSON sort key, UTF-8 và từ chối NaN/Infinity.
- Approval chỉ áp dụng cho pending action hiện tại, có TTL mặc định 300 giây và single-use.
- `request()` và thuộc tính `pending` chỉ trả deep copy; engine giữ action, action ID và
  canonical JSON riêng. `consume()` đối chiếu đồng thời ID và canonical JSON tại execution.
- Patch Tool tạo snapshot proposal hiệu lực trước khi consume approval; mutation xảy ra sau
  consume không thể đổi content thực tế được ghi.
- Replay cùng approval bị từ chối. Một yêu cầu mới của con người vẫn có thể approve lại
  cùng exact action.
- Model text, repository content và tool observation không đi vào API approve.
- Deny/cancel không gọi Patch Tool và không tạo side effect.

## 6. An toàn của isolated patch

- Workspace cô lập được tạo bằng thư mục tạm riêng và chỉ chép text file vượt qua
  boundary, size, encoding và secret policy của Phase 2.
- Không chép `.env`, key material, file nhạy cảm, symlink, reparse point hoặc hardlink.
- Target từ chối absolute path, drive/UNC, `..`, ADS, alias Windows, control character và
  excluded path.
- Create hỗ trợ file trong chuỗi thư mục con chưa tồn tại. Từng segment được tạo và kiểm tra
  lại boundary, loại file, symlink/reparse point trước khi ghi.
- Proposal lưu expected SHA-256; stale source và create conflict bị từ chối.
- Trước replace, file được mở với `O_NOFOLLOW` khi nền tảng hỗ trợ; kiểm tra regular file,
  link count, inode/device và content hash. Hash được kiểm tra lại sau khi file tạm đã fsync,
  ngay trước `os.replace`.
- Lock trong tiến trình chặn hai lần apply đồng thời qua cùng Patch Tool.
- Test so sánh Host trước/sau create, replace, delete và approval denial; Host không đổi.
- Không có Host patch API, writable Host bind mount hoặc fallback ghi Host.

## 7. Token budget

- Cấu hình mới: `agent.max_input_tokens=32768` và
  `agent.reserved_output_tokens=4096`.
- Character limits Phase 2 vẫn là lớp giới hạn bổ sung.
- Ước lượng fallback dùng số byte UTF-8 chia ba và luôn ghi nhận `exact=false`. Heuristic này
  không phản ánh chính xác tokenizer và có thể sai khác đáng kể với Unicode, chuỗi escape,
  source code hoặc phiên bản model khác nhau.
- Chỉ gọi token-count API khi estimate vượt 75% input capacity để giảm request không cần
  thiết.
- Exact count nếu có được ghi `exact=true`. Khi estimate đạt từ 75% input capacity mà exact
  counter thiếu, lỗi hoặc không trả kết quả, request bị từ chối bằng
  `TOKEN_COUNT_UNAVAILABLE_NEAR_LIMIT`; không tin vào estimate ở vùng sát giới hạn.
- Oversize bị từ chối trước generation; Phase 3 chưa làm session compaction/accounting đầy
  đủ của Phase 6.

## 8. Tệp thêm hoặc sửa

- `AGENTS.md`, `IMPLEMENTATION_PLAN.md`, `README.md`, `codepilot.toml`.
- `src/codepilot/agent/__init__.py`, `src/codepilot/agent/state.py`,
  `src/codepilot/agent/loop.py`.
- `src/codepilot/permissions/__init__.py`, `src/codepilot/permissions/engine.py`.
- `src/codepilot/patch/__init__.py`, `src/codepilot/patch/tool.py`.
- `src/codepilot/context/token_budget.py`.
- `src/codepilot/llm/base.py`, `src/codepilot/llm/gemini.py`.
- `src/codepilot/tools/registry.py`, `src/codepilot/cli/main.py`,
  `src/codepilot/config.py`.
- `tests/unit/test_agent_state.py`, `tests/unit/test_agent_loop.py`,
  `tests/unit/test_permissions.py`, `tests/unit/test_patch_tool.py`,
  `tests/unit/test_token_budget.py`.
- `tests/unit/test_gemini.py`, `tests/unit/test_tool_registry.py`,
  `tests/unit/test_cli.py`, `tests/unit/test_config.py`.
- `docs/PHASE_3_IMPLEMENTATION_REPORT.md`.

## 9. Kết quả kiểm tra thực tế

| Kiểm tra | Trạng thái | Kết quả |
|---|---|---|
| `uv sync` | PASS | Resolve 41 package, check 41 package, không đổi dependency |
| `uv run pytest -q -rs` | PASS | **196 passed in 4.63s**, 0 fail, 0 skip |
| `uv run ruff check .` | PASS | `All checks passed!` |
| `git diff --check` | PASS | Không có lỗi whitespace |
| Docker smoke lần cuối | PASS | Docker thật, non-root, network disabled, `codepilot-smoke-ok` |
| Isolated patch tests | PASS | traversal, symlink, stale/conflict, denial/replay, concurrent apply, Host unchanged |
| Gemini mocked function calls | PASS | tool selection, structured calls/responses, malformed/unknown call, step budget |
| Gemini live CLI | FAIL | `PROVIDER_ERROR`; provider không trả turn thành công |
| Gemini live function calling | NOT VERIFIED | CLI live lỗi `PROVIDER_ERROR` trước khi quan sát được tool call |
| Secret scan của changed diff | PASS | Không phát hiện chuỗi credential-like; không có `.env` thật được track |

Docker daemon ban đầu có một lần unavailable và smoke trả FAIL đúng chính sách, không chạy
Host fallback. Lần chạy cuối trả `codepilot-smoke-ok`, xác nhận non-root và network disabled;
integration test Docker trong full suite cũng PASS.

Không đọc hoặc in API key. Provider error/log chỉ chứa mã lỗi an toàn.

### 9.1. Bằng chứng sửa review F-001 đến F-004

- **F-001:** test hồi quy trước bản sửa chứng minh có thể mutate nested
  `PendingAction.action.arguments` để làm approval lệch khỏi content thực thi; test thứ hai
  chứng minh mutation sau `consume()` từng có thể đổi nội dung ghi. Snapshot canonical và
  snapshot proposal hiệu lực hiện chặn cả hai đường này.
- **F-002:** sau patch, message `STALE_AFTER_PATCH` chỉ rõ path bị stale thay vì ngầm dùng
  context cũ. Integration test xác nhận call `read_file` kế tiếp thấy source mới.
- **F-003:** test Unicode zero-width joiner, CJK, escape-heavy input và tool schema lớn xác
  nhận fail closed khi exact counter không khả dụng gần giới hạn; exact count vượt giới hạn
  vẫn bị từ chối.
- **F-004:** test tạo thành công `new/package/module.py`, Host không đổi; traversal hiện có
  vẫn bị chặn và symlink được chèn sau proposal bị từ chối bằng `UNSAFE_TARGET`.

## 10. Git status và commits

Làm trực tiếp trên `main`, không tạo branch, không force push và không rewrite history.
Các commit chức năng đã tạo:

```text
d271004 feat(agent): implement bounded state transitions
1931d7d feat(permissions): enforce single-use exact action approvals
f05bc17 feat(patch): apply approved changes in isolated workspaces
d1ecb7d feat(context): enforce token-aware generation budgets
e97cafd feat(llm): expose validated Gemini function calls
4d7781b feat(agent): orchestrate bounded tool calling
d4ebab2 feat(cli): support interactive patch approval
ad0a77f docs(agent): document editing workflow and review evidence
abca9f5 docs(report): record functional commit history
8a24bcc fix(patch): harden approved nested patch application
59bfd3f fix(agent): mark patched repository context stale
8189fe0 fix(context): fail closed near uncertain token limits
```

Commit cập nhật metadata báo cáo sau danh sách trên được ghi trong phần bàn giao cuối.

## 11. Acceptance criteria

| Tiêu chí | Trạng thái |
|---|---|
| Gemini chọn và gọi registered tools | PASS bằng mock; live NOT VERIFIED do timeout |
| Invalid tool calls bị từ chối | PASS |
| Agent step budget được cưỡng chế | PASS |
| State transitions chính xác | PASS |
| Write luôn cần exact-action approval | PASS |
| Approval replay bị chặn | PASS |
| Approved patch chỉ đổi isolated workspace | PASS |
| Host repository giữ nguyên | PASS |
| Patch conflict/stale source bị từ chối | PASS |
| Token-aware context limit được cưỡng chế | PASS |
| Docker smoke | PASS ở lần chạy cuối |
| Phase 3 được Project Owner phê duyệt | Chưa có; chờ review |

## 12. Giới hạn đã biết

- Live Gemini tool calling chưa xác minh. Lần smoke trước kết thúc bằng `TIMEOUT_ERROR`; lần
  smoke cho review fix trả `PROVIDER_ERROR` trước tool call. Không báo PASS và không suy đoán
  quota/model/provider state.
- Fallback token count là ước lượng bảo thủ cục bộ, không phải exact tokenizer measurement;
  request gần giới hạn cần exact count và sẽ fail closed nếu provider không đếm được.
- Phase 3 chỉ tạo một action cho một file; chưa hỗ trợ transaction nhiều file.
- Bản sao cô lập gồm text UTF-8/UTF-8 BOM được policy cho phép; binary và file bị loại không
  được patch.
- Python standard library không cung cấp primitive handle-relative atomic replace hoàn chỉnh
  trên mọi platform. Phase 3 giảm TOCTOU bằng workspace private, no-follow, inode/hash
  revalidation và process lock; race đối kháng sau lần kiểm tra cuối chưa được chứng minh loại
  bỏ hoàn toàn. Hardening filesystem race/symlink-swap đã được hoãn rõ sang Phase 5.
- Chưa có Docker execution cho source không tin cậy trong Agent Loop, automated verification,
  self-correction, Host patch application, SQLite/session resume hoặc semantic retrieval; các
  phần đó thuộc Phase 4–7 và vẫn khóa.

## 13. Yêu cầu review

Nhờ reviewer kiểm tra lại F-001 đến F-004, đặc biệt snapshot approval/proposal, stale-context
notification, fail-closed token budget và nested create. Phase 3 dừng tại đây để chờ code
review và quyết định của Project Owner; không bắt đầu Phase 4.
