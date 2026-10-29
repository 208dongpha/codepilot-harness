
# CODEPILOT HARNESS — KẾ HOẠCH TRIỂN KHAI

> **Trạng thái:** Phase 1 APPROVED; Phase 2 đã triển khai, chờ review
> **Phase được phép triển khai:** Phase 2 (CP-007 đến CP-011)
> **Kiến trúc:** Modular Monolith
> **Thời gian dự kiến:** 8 tuần
> **Người triển khai:** Codex
> **Người phê duyệt:** Project Owner
> **Người thiết kế và review:** ChatGPT

---

# 1. GIỚI THIỆU

## 1.1. Mục đích tài liệu

Tài liệu này hướng dẫn Codex triển khai dự án CodePilot Harness theo từng giai đoạn.

CodePilot Harness là một AI Coding Agent hoạt động trên terminal, có khả năng:

- Hiểu yêu cầu lập trình bằng ngôn ngữ tự nhiên.
- Khám phá và đọc source code trong repository.
- Sử dụng Gemini để phân tích và đề xuất giải pháp.
- Gọi công cụ để thực hiện tác vụ.
- Chỉnh sửa source code trong Docker Sandbox.
- Chạy test và phân tích lỗi.
- Tự sửa lỗi trong số lần thử giới hạn.
- Kiểm tra Git diff trước khi áp dụng thay đổi.
- Yêu cầu người dùng phê duyệt các thao tác ghi.
- Lưu và khôi phục session.
- Đánh giá hiệu quả thông qua benchmark.

Mục tiêu chính là tự xây dựng Agent Harness thay vì chỉ gọi LLM API.

## 1.2. Nguyên tắc triển khai

Codex PHẢI:

1. Đọc README.md và IMPLEMENTATION_PLAN.md.
2. Chỉ triển khai phase được người dùng cho phép.
3. Tuân thủ kiến trúc Modular Monolith.
4. Viết code có cấu trúc, dễ mở rộng và kiểm thử.
5. Sử dụng type hints và validation.
6. Viết unit tests cho các chức năng được triển khai.
7. Chạy test và lint trước khi báo hoàn thành.
8. Kiểm tra Git diff.
9. Báo cáo chính xác kết quả thực thi.
10. Dừng và chờ review trước khi sang phase tiếp theo.

Codex KHÔNG ĐƯỢC:

- Tự động triển khai Phase 3–8.
- Tự thay đổi các quyết định kiến trúc.
- Hardcode API key hoặc secrets.
- Bỏ qua test để báo hoàn thành.
- Tự động push hoặc merge code.
- Chạy code không đáng tin cậy trực tiếp trên Host.
- Bỏ qua Permission Engine.
- Tự ý sửa các file ngoài phạm vi task.
- Tạo module hoặc abstraction không cần thiết.

---

# 2. CÁC QUYẾT ĐỊNH KIẾN TRÚC

## 2.1. Tech Stack

| Thành phần | Công nghệ |
|---|---|
| Ngôn ngữ | Python 3.12+ |
| Kiến trúc | Modular Monolith |
| CLI | Typer + Rich |
| LLM | Gemini API |
| SDK | google-genai |
| Validation | Pydantic v2 |
| Configuration | TOML + .env |
| Package Manager | uv |
| Async Runtime | asyncio |
| Code Search | ripgrep |
| Sandbox | Docker |
| Database | SQLite |
| Unit Testing | pytest |
| Linting | Ruff |
| Type Checking | mypy ở phase sau |
| Logging | Structured Logging |
| CI/CD | GitHub Actions |

Không sử dụng LangChain hoặc LangGraph trong MVP.

Agent Loop phải được tự xây dựng.

## 2.2. Kiến trúc hệ thống

```text
                  USER
                   |
                   v
            Interactive CLI
                   |
                   v
              HARNESS CORE
          +--------------------+
          | Agent Loop         |
          | Context Manager    |
          | Permission Engine  |
          | State Manager      |
          | Budget Manager     |
          | Verification       |
          +--------------------+
             |             |
             v             v
       Gemini Adapter   Tool Registry
                           |
                           v
                     Docker Sandbox
                           |
                           v
                     Workspace Copy
                           |
                           v
                      Git Diff
                           |
                           v
                     User Approval
                           |
                           v
                     Host Repository
```

## 2.3. Quy tắc thiết kế

- CLI chỉ xử lý tương tác với người dùng.
- LLM Adapter chỉ xử lý giao tiếp với Gemini.
- Agent Loop điều phối hành động.
- Tool Registry quản lý các công cụ.
- Permission Engine quyết định quyền thực thi.
- Docker Runtime quản lý container.
- Verification Engine kiểm tra kết quả.
- Storage quản lý session và trạng thái.

Các module phải độc lập tương đối.

Không đặt toàn bộ logic vào một file.

## 2.4. Quyền thực thi

Sử dụng chính sách:

**Ask Before Write**

Quy tắc:

- Đọc file trong workspace hợp lệ: cho phép tự động.
- Chỉnh sửa file: phải được phê duyệt.
- Chạy lệnh: phải qua Command Policy.
- Áp dụng patch lên Host: cần phê duyệt riêng.
- Hành động bị cấm: từ chối thực thi.

Mọi quyền phê duyệt phải gắn với hành động cụ thể.

## 2.5. Docker Sandbox

Docker được sử dụng ngay từ Phase 1.

Nguyên tắc:

1. Repository được copy vào workspace cô lập.
2. Agent thao tác trên bản sao.
3. Test chạy trong Docker.
4. Harness thu thập các thay đổi.
5. Tạo Git diff.
6. Người dùng xem và phê duyệt.
7. Chỉ áp dụng thay đổi đã phê duyệt lên Host.

Không được mount toàn bộ thư mục Home, Docker socket hoặc secrets vào container.

Không được tự động chuyển sang thực thi trên Host nếu Docker không hoạt động.

## 2.6. Verification

Kết quả xác minh dựa trên:

- Test results.
- Git diff.
- Policy compliance.

Các trạng thái:

- PASS: Đạt yêu cầu.
- FAIL: Không đạt yêu cầu.
- INCONCLUSIVE: Chưa đủ bằng chứng.

Số lần tự sửa sau khi verification thất bại tối đa: 3.

---

# 3. TỔNG QUAN 8 PHASE

| Phase | Tên | Phụ thuộc | Thời gian |
|---|---|---|---|
| 1 | Foundation & Runtime | Không | Tuần 1 |
| 2 | Repository Exploration | Phase 1 | Tuần 2 |
| 3 | Agent Loop & Editing | Phase 2 | Tuần 3 |
| 4 | Verification & Recovery | Phase 3 | Tuần 4 |
| 5 | Sandbox Hardening | Phase 4 | Tuần 5 |
| 6 | Context & Persistence | Phase 5 | Tuần 6 |
| 7 | Benchmark & Optimization | Phase 6 | Tuần 7 |
| 8 | Portfolio & Release | Phase 7 | Tuần 8 |

Mỗi phase phải được review và phê duyệt trước khi tiếp tục.

---

# 4. PHASE 1 — FOUNDATION & RUNTIME

## 4.1. Mục tiêu

Xây dựng nền tảng ban đầu cho CodePilot Harness.

Sau Phase 1, hệ thống phải:

- Cài đặt được dưới dạng Python package.
- Khởi động Interactive CLI.
- Kết nối Gemini API.
- Gửi và nhận tin nhắn từ Gemini.
- Cho phép thay đổi Gemini model qua cấu hình.
- Đọc cấu hình từ TOML và .env.
- Kiểm tra Docker có hoạt động hay không.
- Chạy Docker smoke test.
- Có unit tests và linting.
- Xử lý lỗi cơ bản.

Phase 1 chưa triển khai Agent Loop hoặc tự sửa code.

## 4.2. Kết quả mong muốn

Chạy:

```powershell
uv sync
uv run codepilot
```

Ví dụ:

```text
CodePilot Harness v0.1.0

Provider: Gemini
Model: <configured-model>

Type /help to see available commands.

codepilot> Hello

Assistant> Hello! How can I help you?

codepilot> /status

Provider: Gemini
Session: In-memory
Sandbox: Docker Available

codepilot> /exit

Goodbye!
```

Kiểm tra Docker:

```powershell
uv run codepilot sandbox smoke-test
```

Đây là hành vi mục tiêu, không phải kết quả đã thực thi.

---

## TASK CP-001 — KHỞI TẠO PROJECT

### Mục tiêu

Tạo cấu trúc Python project chuẩn, có thể cài đặt và chạy bằng uv.

### Các file cần tạo

```text
pyproject.toml
.python-version
.gitignore
src/codepilot/__init__.py
src/codepilot/cli/__init__.py
src/codepilot/cli/main.py
tests/conftest.py
```

### Công việc

1. Khởi tạo Python project.
2. Thiết lập Python 3.12 làm phiên bản cơ sở.
3. Sử dụng cấu trúc src layout.
4. Cấu hình uv.
5. Cài dependencies:
   - typer
   - rich
   - pydantic
   - google-genai
   - python-dotenv
6. Cài development dependencies:
   - pytest
   - pytest-asyncio
   - ruff
7. Định nghĩa console script `codepilot`.
8. Cấu hình pytest.
9. Cấu hình Ruff.
10. Thiết lập .gitignore.

Các file cần được ignore:

```text
.env
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
*.db
*.sqlite
*.sqlite3
```

### Acceptance Criteria

- [ ] `uv sync` thành công.
- [ ] Python package import được.
- [ ] `uv run codepilot --help` chạy được.
- [ ] `uv run pytest` chạy được.
- [ ] `uv run ruff check .` thành công.
- [ ] Cấu trúc project đúng src layout.
- [ ] Không có dependency không cần thiết.

### Test Cases

- Kiểm tra package import.
- Kiểm tra CLI entry point.
- Kiểm tra pytest discovery.
- Kiểm tra Ruff.

### Review Gate

Kiểm tra cấu trúc repository, dependencies và khả năng cài đặt.

---

## TASK CP-002 — CONFIGURATION SYSTEM

### Mục tiêu

Xây dựng hệ thống cấu hình tập trung.

### Các file cần tạo

```text
src/codepilot/config.py
codepilot.toml
.env.example
tests/unit/test_config.py
```

### Quy tắc cấu hình

Độ ưu tiên:

1. Environment variables.
2. codepilot.toml.
3. Default values.

Secrets được đọc từ environment hoặc .env.

Không commit .env.

### Cấu hình mẫu

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

.env.example:

```dotenv
GEMINI_API_KEY=your_api_key_here
```

### Công việc

1. Sử dụng tomllib để đọc TOML.
2. Sử dụng python-dotenv để đọc .env.
3. Tạo Pydantic configuration models.
4. Validate dữ liệu cấu hình.
5. Hỗ trợ environment overrides.
6. Không hardcode Gemini model.
7. Không hiển thị API key.
8. Xử lý trường hợp cấu hình không hợp lệ.
9. Xử lý thiếu API key khi gọi Gemini.
10. Cho phép chạy /help mà không cần API key.

### Acceptance Criteria

- [ ] Đọc TOML thành công.
- [ ] Environment overrides hoạt động.
- [ ] TOML không hợp lệ được báo lỗi.
- [ ] Giá trị cấu hình sai được từ chối.
- [ ] Thiếu API key có thông báo rõ ràng.
- [ ] Không lộ API key.
- [ ] Đổi model không cần sửa source code.

### Test Cases

```text
test_load_valid_config
test_environment_override
test_invalid_toml
test_missing_api_key
test_invalid_model_config
test_secret_not_exposed
```

### Review Gate

Kiểm tra configuration precedence, validation và bảo mật secrets.

---

## TASK CP-003 — GEMINI ADAPTER

### Mục tiêu

Tích hợp Gemini API thông qua một adapter độc lập.

### Các file cần tạo

```text
src/codepilot/llm/__init__.py
src/codepilot/llm/base.py
src/codepilot/llm/gemini.py
tests/unit/test_gemini.py
```

### Thiết kế

Tạo interface chung cho LLM Provider.

Ví dụ:

```python
from typing import Protocol

class LLMProvider(Protocol):
    async def generate(
        self,
        messages: list[dict[str, str]]
    ) -> str:
        ...
```

Đây là interface tham khảo.

Codex có thể điều chỉnh kiểu dữ liệu để phù hợp với SDK, nhưng phải giữ tính độc lập với provider.

### Công việc

1. Tích hợp google-genai SDK.
2. Đọc Gemini API key từ environment.
3. Đọc model ID từ config.
4. Hỗ trợ gửi messages.
5. Nhận và chuẩn hóa response.
6. Xử lý authentication errors.
7. Xử lý rate limit.
8. Xử lý timeout.
9. Xử lý provider errors.
10. Không làm lộ credentials.
11. Thiết kế để bổ sung provider khác sau này.

### Các loại lỗi

```text
AUTHENTICATION_ERROR
RATE_LIMIT_ERROR
TIMEOUT_ERROR
MODEL_ERROR
PROVIDER_ERROR
```

Không retry vô hạn.

Không retry lỗi sai API key.

### Acceptance Criteria

- [ ] Gemini adapter khởi tạo được.
- [ ] Model ID lấy từ config.
- [ ] Response được chuẩn hóa.
- [ ] Lỗi API được xử lý.
- [ ] Không crash CLI khi provider lỗi thông thường.
- [ ] Unit tests sử dụng mock.
- [ ] Unit tests không tiêu thụ Gemini quota.
- [ ] Không có logic CLI trong Gemini adapter.

### Test Cases

```text
test_gemini_success
test_gemini_uses_configured_model
test_gemini_auth_error
test_gemini_rate_limit
test_gemini_timeout
test_gemini_provider_error
```

### Review Gate

Kiểm tra Gemini SDK, abstraction, error handling và secret safety.

---

## TASK CP-004 — INTERACTIVE CLI

### Mục tiêu

Tạo giao diện terminal để người dùng trò chuyện với Gemini.

### Các file

```text
src/codepilot/cli/main.py
src/codepilot/cli/renderer.py
tests/unit/test_cli.py
```

### Các lệnh cần hỗ trợ

```text
codepilot
/help
/status
/exit
```

### Hành vi

#### codepilot

- Khởi động CLI.
- Load configuration.
- Hiển thị thông tin ứng dụng.
- Nhận input.
- Gửi message đến Gemini.
- Hiển thị response.

#### /help

- Hiển thị danh sách lệnh.
- Không gọi Gemini API.

#### /status

Hiển thị:

- Provider.
- Model ID.
- Session mode.
- Docker availability nếu đã kiểm tra.

Không hiển thị API key.

#### /exit

- Thoát CLI.
- Giải phóng tài nguyên.
- Không gọi Gemini.

### Conversation History

Phase 1 chỉ cần lưu lịch sử chat trong memory.

Chưa cần SQLite.

Lịch sử phải được duy trì trong cùng một phiên CLI.

### Xử lý lỗi

- Input rỗng.
- Slash command không hợp lệ.
- Ctrl+C.
- EOF.
- Gemini API lỗi.
- Response dài.

### Acceptance Criteria

- [ ] CLI khởi động được.
- [ ] /help hoạt động.
- [ ] /status hoạt động.
- [ ] /exit hoạt động.
- [ ] Command không hợp lệ có thông báo.
- [ ] Provider lỗi không làm CLI crash.
- [ ] Conversation history được duy trì.
- [ ] Chưa có chức năng sửa file.

### Test Cases

```text
test_cli_help
test_cli_status
test_cli_exit
test_cli_unknown_command
test_cli_empty_input
test_cli_provider_failure
test_cli_conversation_history
```

### Review Gate

Kiểm tra UX, CLI architecture, error handling và khả năng test.

---

## TASK CP-005 — DOCKER FOUNDATION

### Mục tiêu

Thiết lập Docker Sandbox để chuẩn bị cho việc thực thi code ở các phase sau.

### Các file

```text
docker/Dockerfile.sandbox
src/codepilot/execution/__init__.py
src/codepilot/execution/docker_runtime.py
tests/integration/test_docker.py
```

### Yêu cầu

Docker Runtime phải hỗ trợ:

- Kiểm tra Docker availability.
- Khởi tạo container tạm.
- Chạy lệnh smoke test.
- Thu thập stdout và stderr.
- Thu thập exit code.
- Giới hạn thời gian chạy.
- Giới hạn CPU.
- Giới hạn RAM.
- Giới hạn process.
- Tắt network mặc định.
- Chạy bằng non-root user.
- Dọn dẹp container.

### Docker Smoke Test

Command:

```powershell
uv run codepilot sandbox smoke-test
```

Luồng thực hiện:

1. Kiểm tra Docker daemon.
2. Kiểm tra sandbox image.
3. Khởi tạo disposable container.
4. Chạy một Python command vô hại.
5. Kiểm tra user không phải root.
6. Thu thập kết quả.
7. Xóa container.
8. Hiển thị PASS hoặc FAIL.

Ví dụ:

```text
Docker: Available
Sandbox image: Ready
Network: Disabled
User: Non-root
Smoke test: PASS
```

### Quy tắc bảo mật

- Không sử dụng privileged container.
- Không mount Docker socket.
- Không mount host home.
- Không truyền Gemini API key vào container.
- Không sử dụng host networking.
- Không cho phép chạy vô hạn.
- Không thực thi trên Host khi Docker lỗi.
- Container phải được cleanup khi kết thúc.

### Phạm vi Phase 1

Phase 1 chỉ xây dựng Docker runtime foundation và smoke test.

Chưa triển khai:

- Repository copy hoàn chỉnh.
- Agent code execution.
- Patch application.
- Host repository modification.

### Acceptance Criteria

- [ ] Phát hiện Docker availability.
- [ ] Smoke test chạy trong Docker.
- [ ] Container chạy non-root.
- [ ] Network bị tắt.
- [ ] CPU và RAM có giới hạn.
- [ ] Có timeout.
- [ ] Container được cleanup.
- [ ] Docker lỗi được thông báo.
- [ ] Không fallback sang Host.

### Test Cases

```text
test_docker_available
test_docker_unavailable
test_smoke_test_success
test_container_non_root
test_network_disabled
test_timeout_handling
test_container_cleanup
```

Nếu môi trường không có Docker, integration tests có thể SKIPPED.

Tuy nhiên, Phase 1 chưa được nghiệm thu đầy đủ nếu chưa có bằng chứng smoke test chạy thành công trên môi trường Docker thực tế.

### Review Gate

Kiểm tra isolation, resource limits, cleanup và failure handling.

---

## TASK CP-006 — LOGGING & FINAL VALIDATION

### Mục tiêu

Hoàn thiện logging, testing và kiểm tra toàn bộ Phase 1.

### Các file

```text
src/codepilot/telemetry/__init__.py
src/codepilot/telemetry/logging.py
tests/unit/test_logging.py
```

### Logging

Cần ghi nhận:

- Application startup.
- Configuration errors.
- Gemini API errors.
- Docker errors.
- Docker smoke test results.
- Unexpected exceptions.

Không ghi:

- API keys.
- Authentication headers.
- Passwords.
- Toàn bộ environment variables.

### Lệnh kiểm tra

```powershell
uv sync
uv run pytest
uv run ruff check .
uv run codepilot --help
uv run codepilot sandbox smoke-test
```

### Acceptance Criteria

- [ ] Unit tests pass.
- [ ] Ruff pass.
- [ ] CLI help hoạt động.
- [ ] Configuration tests pass.
- [ ] Gemini adapter tests pass.
- [ ] Docker smoke test pass.
- [ ] Logs không chứa secrets.
- [ ] Không triển khai chức năng Phase 2.

### Review Gate

Codex phải tạo Implementation Report và dừng lại.

---

# 5. DEFINITION OF DONE — PHASE 1

Phase 1 chỉ hoàn thành khi đáp ứng các điều kiện sau.

## Functional

- [ ] Cài đặt project bằng uv thành công.
- [ ] Interactive CLI khởi động được.
- [ ] /help hoạt động.
- [ ] /status hoạt động.
- [ ] /exit hoạt động.
- [ ] Gemini adapter hoạt động.
- [ ] Model ID cấu hình được.
- [ ] TOML và .env hoạt động.
- [ ] Docker availability check hoạt động.
- [ ] Docker smoke test thành công.

## Quality

- [ ] Unit tests pass.
- [ ] Ruff pass.
- [ ] External API được mock trong unit tests.
- [ ] Xử lý lỗi Gemini.
- [ ] Xử lý lỗi Docker.
- [ ] Xử lý lỗi cấu hình.

## Security

- [ ] Không commit secrets.
- [ ] Không log API key.
- [ ] Không có unrestricted host execution.
- [ ] Docker chạy non-root.
- [ ] Docker network disabled.
- [ ] Có resource limits.
- [ ] Không fallback sang Host.

## Scope

- [ ] Chưa có Agent Loop.
- [ ] Chưa có code editing.
- [ ] Chưa có patch application.
- [ ] Chưa có self-correction.
- [ ] Chưa triển khai Phase 2–8.

## Evidence

- [ ] Danh sách file thay đổi.
- [ ] Git diff.
- [ ] Kết quả unit tests.
- [ ] Kết quả Ruff.
- [ ] Kết quả Docker smoke test.
- [ ] Các vấn đề còn tồn tại.
- [ ] Implementation Report.

Live Gemini smoke test được khuyến nghị.

Nếu không chạy được vì API key, quota hoặc lỗi provider, phải ghi NOT VERIFIED.

Không được tự nhận PASS.

---

# 6. PHASE 2 — REPOSITORY EXPLORATION

> Project Owner đã cho phép CP-007 đến CP-011. Read-only tools được chạy trong Harness sau kiểm tra workspace và resource limits; không thực thi source repository.
> Theo điều chỉnh trực tiếp: làm trên branch hiện tại, không tạo branch mới. Chỉ commit local; chưa push khi chưa có phép.

## Mục tiêu

Cho phép Agent khám phá và đọc repository Python một cách an toàn.

## Dependencies

- Phase 1 đã được phê duyệt.
- Configuration và Docker Runtime hoạt động.

## Tasks

### CP-007 — Tool Registry

- Thiết kế Tool Interface.
- Đăng ký và quản lý tools.
- Validate arguments bằng Pydantic.
- Chuẩn hóa Tool Results.
- Xử lý tool không tồn tại hoặc input sai.

### CP-008 — File Tools

- Implement list_files.
- Implement read_file.
- Giới hạn workspace.
- Chặn path traversal.
- Chống symlink escape.
- Giới hạn file size và output.

### CP-009 — Code Search

- Tích hợp ripgrep.
- Tìm kiếm text và regex.
- Trả về file path và line number.
- Xử lý binary files.
- Giới hạn search results.

### CP-010 — Repository Map

- Khám phá cấu trúc repository.
- Xác định Python modules.
- Tạo repository map.
- Bỏ qua .git, .venv và cache.
- Không đưa secrets vào context.

### CP-011 — Context Builder

- Kết hợp user task và relevant files.
- Đưa repository map vào context.
- Giới hạn context size.
- Duy trì lịch sử cần thiết.

## Acceptance Criteria

- [ ] List files thành công.
- [ ] Đọc file hợp lệ.
- [ ] Search code chính xác.
- [ ] Repository map được tạo.
- [ ] Chặn truy cập ngoài workspace.
- [ ] Không chỉnh sửa repository.

## Tests

- Tool Registry tests.
- File access tests.
- Path traversal tests.
- Symlink tests.
- Search tests.
- Context Builder tests.

## Review Gate

Review read-only tools, workspace boundaries và context selection.

Không triển khai Phase 3 nếu chưa được duyệt.

---

# 7. PHASE 3 — AGENT LOOP & EDITING

## Mục tiêu

Xây dựng Agent Loop có khả năng gọi tools và đề xuất chỉnh sửa source code.

## Dependencies

- Phase 2 đã được phê duyệt.
- Tool Registry và Context Builder hoạt động.

## Tasks

### CP-012 — Agent State Machine

Các trạng thái:

```text
CREATED
RUNNING
WAITING_APPROVAL
VERIFYING
COMPLETED
FAILED
CANCELLED
```

### CP-013 — Agent Loop

- Build context.
- Gọi Gemini.
- Nhận tool calls.
- Validate arguments.
- Kiểm tra quyền.
- Thực thi tools được cho phép.
- Lưu observations.
- Tiếp tục cho đến khi hoàn thành hoặc hết budget.

### CP-014 — Permission Engine

- Implement Allow / Ask / Deny.
- Áp dụng Ask Before Write.
- Phê duyệt theo exact action.
- Lưu approval decisions.
- Từ chối unauthorized actions.

### CP-015 — Patch Tool

- Tạo proposed patches.
- Validate target paths.
- Chỉnh sửa sandbox workspace sau approval.
- Không sửa Host trực tiếp.
- Phát hiện patch conflicts.

### CP-016 — CLI Approval

- Implement /approve.
- Implement /deny.
- Hiển thị pending action.
- Ngăn tái sử dụng approval.
- Hỗ trợ cancellation.

## Acceptance Criteria

- [ ] Agent gọi được tools.
- [ ] Arguments sai bị từ chối.
- [ ] Write cần approval.
- [ ] Có thể sửa sandbox workspace.
- [ ] Host repository không thay đổi.
- [ ] Giới hạn Agent steps.
- [ ] State transitions chính xác.

## Tests

- Agent Loop tests.
- Tool calling tests.
- Permission tests.
- Patch tests.
- Approval tests.
- State transition tests.
- Budget tests.

## Review Gate

Review Agent Loop, permissions và patch safety.

---

# 8. PHASE 4 — VERIFICATION & RECOVERY

## Mục tiêu

Cho phép Agent kiểm tra kết quả và tự sửa lỗi có giới hạn.

## Dependencies

- Phase 3 đã được phê duyệt.
- Agent Loop và sandbox editing hoạt động.

## Tasks

### CP-017 — Test Runner

- Chạy tests trong Docker.
- Thu thập exit code.
- Thu thập stdout/stderr.
- Giới hạn timeout.
- Giới hạn output.

### CP-018 — Git Diff Verification

- So sánh sandbox với baseline.
- Xác định file thay đổi.
- Phát hiện unauthorized changes.
- Tạo diff để review.

### CP-019 — Verification Engine

Trả về:

```text
PASS
FAIL
INCONCLUSIVE
```

Dựa trên:

- Test results.
- Git diff.
- Policy compliance.

### CP-020 — Self-Correction

- Gửi verification feedback cho Gemini.
- Cho phép tối đa 3 repair retries.
- Theo dõi attempts.
- Dừng khi vượt budget.
- Lưu failure evidence.

### CP-021 — Task Report

Báo cáo:

- Files changed.
- Tests executed.
- Verification verdict.
- Retry count.
- Remaining issues.

## Acceptance Criteria

- [ ] Tests chạy trong Docker.
- [ ] Test failures được phân tích.
- [ ] Agent có thể retry.
- [ ] Retry limit được áp dụng.
- [ ] Unauthorized changes bị phát hiện.
- [ ] Không dựa hoàn toàn vào lời khẳng định của LLM.

## Tests

- Test pass.
- Test fail.
- Retry success.
- Retry exhaustion.
- Timeout.
- Unauthorized diff.
- Inconclusive verification.

## Review Gate

Demo được workflow:

```text
Task
  ↓
Edit
  ↓
Run Tests
  ↓
Fail
  ↓
Analyze Error
  ↓
Retry
  ↓
Verify
```

Không triển khai Phase 5 nếu chưa được duyệt.

---

# 9. PHASE 5 — SANDBOX HARDENING

## Mục tiêu

Tăng cường bảo mật Docker và triển khai cơ chế áp dụng thay đổi lên Host.

## Dependencies

- Phase 4 đã được phê duyệt.
- Verification và Git diff hoạt động.

## Tasks

### CP-022 — Workspace Isolation

- Copy repository vào sandbox.
- Giữ metadata cần thiết.
- Loại trừ secrets và file không cần thiết.
- Ngăn truy cập ngoài sandbox.

### CP-023 — Execution Restrictions

- Giới hạn network.
- Giới hạn CPU.
- Giới hạn memory.
- Giới hạn processes.
- Thiết lập timeout.
- Không cho privileged execution.

### CP-024 — Host Patch Application

- Hiển thị proposed diff.
- Yêu cầu explicit approval.
- Kiểm tra Host có thay đổi từ snapshot hay không.
- Chỉ áp dụng approved changes.
- Từ chối conflicts.
- Giữ nguyên file gốc nếu thất bại.

### CP-025 — Security Testing

Kiểm tra:

- Path traversal.
- Symlink escape.
- Command injection.
- Unauthorized writes.
- Secret leakage.
- Prompt injection.
- Sandbox timeout.
- Container cleanup.

## Acceptance Criteria

- [ ] Host không thay đổi trước approval.
- [ ] Approved patches được áp dụng.
- [ ] Patch conflicts bị từ chối.
- [ ] Dangerous commands bị chặn.
- [ ] Secrets được bảo vệ.
- [ ] Resource limits hoạt động.
- [ ] Không có unrestricted host execution.

## Tests

- Workspace isolation.
- Host modification prevention.
- Patch conflicts.
- Security regression.
- Docker cleanup.

## Review Gate

Bắt buộc security review.

Không chuyển sang Phase 6 nếu còn lỗi Critical hoặc High.

---

# 10. PHASE 6 — CONTEXT & PERSISTENCE

## Mục tiêu

Cho phép Agent xử lý task dài, lưu session và tiếp tục công việc sau khi gián đoạn.

## Dependencies

- Phase 5 đã được phê duyệt.
- Agent Loop và execution events ổn định.

## Tasks

### CP-026 — SQLite Storage

Lưu:

- Sessions.
- Messages.
- Tool calls.
- Tool results.
- Approvals.
- Task states.
- Checkpoints.

### CP-027 — Session Resume

- Liệt kê sessions.
- Resume session.
- Khôi phục conversation history.
- Khôi phục task state.
- Kiểm tra workspace compatibility.

### CP-028 — Context Compaction

- Tóm tắt history cũ.
- Giữ user goals.
- Giữ important decisions.
- Giữ unresolved failures.
- Giữ relevant file references.

### CP-029 — Token Budgeting

- Theo dõi token usage.
- Dành response budget.
- Giới hạn tool outputs.
- Dừng khi vượt budget.

### CP-030 — Recovery

- Xử lý interrupted processes.
- Xử lý provider errors.
- Tránh duplicate side effects.
- Khôi phục consistent state.

## Acceptance Criteria

- [ ] Session tồn tại sau khi thoát CLI.
- [ ] Resume khôi phục context.
- [ ] Interrupted task có thể phục hồi an toàn.
- [ ] Compaction giữ critical constraints.
- [ ] Token budget được áp dụng.
- [ ] Approval không bị replay sai.

## Tests

- SQLite tests.
- Resume tests.
- Interrupted session tests.
- Compaction tests.
- Token budget tests.
- Duplicate action tests.

## Review Gate

Review persistence consistency, recovery và context quality.

---

# 11. PHASE 7 — BENCHMARK & OPTIMIZATION

## Mục tiêu

Đánh giá hiệu quả Coding Agent bằng bộ benchmark có thể tái lập.

## Dependencies

- Phase 6 đã được phê duyệt.
- Agent và Verification Pipeline ổn định.

## Tasks

### CP-031 — Benchmark Dataset

Tạo khoảng 40 Python coding tasks:

- 12 Bug Fixing.
- 8 Unit Testing.
- 8 Refactoring.
- 8 Feature Implementation.
- 4 Code Understanding.

Mỗi task phải có:

- Fixed repository revision.
- Task description.
- Acceptance criteria.
- Independent verification.
- Execution limits.

### CP-032 — Benchmark Runner

- Reset repository trước mỗi task.
- Chạy Agent trong sandbox.
- Lưu results.
- Lưu execution traces.
- Không cho Agent đọc hidden tests.

### CP-033 — Metrics

Thu thập:

- Task Success Rate.
- Test Pass Rate.
- Recovery Rate.
- Tool Error Rate.
- Latency.
- Token Usage.
- Cost Per Task.

### CP-034 — Experiments

So sánh:

A. Baseline LLM + Tools.

B. Agent Loop + Verification + Retry.

C. Full Harness + Context Management.

Sử dụng cùng benchmark, model và giới hạn tài nguyên để so sánh công bằng.

### CP-035 — Optimization

Phân tích:

- Failed tasks.
- Excessive tool calls.
- Context inefficiency.
- Repeated errors.
- Cost bottlenecks.
- Slow verification.

## Acceptance Criteria

- [ ] Benchmark reproducible.
- [ ] Results được lưu.
- [ ] Success được kiểm chứng độc lập.
- [ ] Cost và latency được đo.
- [ ] Failure categories được phân tích.
- [ ] Experiments được ghi chép.

## Tests

- Benchmark fixture validation.
- Repository reset.
- Metrics calculation.
- Result serialization.
- Hidden test isolation.

## Review Gate

Review methodology, kết quả và bằng chứng.

Không được tự tạo số liệu benchmark giả rồi báo là kết quả thực tế.

---

# 12. PHASE 8 — PORTFOLIO & RELEASE

## Mục tiêu

Hoàn thiện sản phẩm để trình diễn và đưa lên GitHub.

## Dependencies

- Phase 7 đã được phê duyệt.
- Benchmark results có sẵn.

## Tasks

### CP-036 — Documentation

- Hoàn thiện README.
- Viết Architecture Documentation.
- Mô tả Agent Loop.
- Mô tả Docker Sandbox.
- Mô tả Permissions.
- Viết Configuration Guide.

### CP-037 — CI/CD

- Thiết lập GitHub Actions.
- Chạy unit tests.
- Chạy Ruff.
- Chạy integration tests phù hợp.
- Kiểm tra packaging.

### CP-038 — Demo

Chuẩn bị demo:

- Interactive CLI.
- Repository exploration.
- Bug fixing.
- Test failure and recovery.
- Approval workflow.
- Docker isolation.
- Session resume.

### CP-039 — Benchmark Report

Bao gồm:

- Dataset description.
- Experimental setup.
- Metrics.
- Results.
- Limitations.
- Failure analysis.

### CP-040 — Release

- Viết installation guide.
- Kiểm tra clean installation.
- Chọn license.
- Tạo tag v0.1.0.
- Viết release notes.

## Acceptance Criteria

- [ ] Project cài được trên môi trường sạch.
- [ ] Documentation đúng với implementation.
- [ ] CI pass.
- [ ] Demo reproducible.
- [ ] Benchmark report hoàn chỉnh.
- [ ] Known limitations được ghi rõ.

## Review Gate

Final review về:

- Architecture.
- Security.
- Code Quality.
- Functionality.
- Benchmark.
- Documentation.

Chỉ release khi Project Owner phê duyệt.

---

# 13. CODEX EXECUTION CONTRACT

Đây là quy định bắt buộc khi Codex triển khai.

## 13.1. Quyền triển khai

Ban đầu:

```text
AUTHORIZED_PHASE = 2
```

Phase 1 đã được Project Owner phê duyệt code review.
Codex chỉ được triển khai Phase 2, CP-007 đến CP-011.
Phase 3–8 chỉ là kế hoạch tham khảo và vẫn LOCKED.

Không được tự chuyển phase.

## 13.2. Quy trình thực hiện task

Với mỗi task:

1. Đọc task specification.
2. Kiểm tra các file hiện có.
3. Xác định dependencies.
4. Implement đúng phạm vi.
5. Viết hoặc cập nhật tests.
6. Chạy tests.
7. Kiểm tra Git diff.
8. Báo cáo kết quả.

Không thực hiện unrelated changes.

## 13.3. Xử lý yêu cầu chưa rõ

Nếu có ambiguity:

1. Xác định điểm chưa rõ.
2. Giải thích ảnh hưởng.
3. Đề xuất giải pháp.
4. Hỏi người dùng nếu ảnh hưởng architecture, security hoặc scope.

Không tự thay đổi quyết định đã được phê duyệt.

## 13.4. Quy định báo cáo test

Phân biệt rõ:

- PASS: Test đã chạy và thành công.
- FAIL: Test đã chạy và thất bại.
- SKIPPED: Test được bỏ qua.
- NOT RUN: Chưa chạy test.
- BLOCKED: Không chạy được vì phụ thuộc bên ngoài.

Không báo SKIPPED hoặc BLOCKED thành PASS.

## 13.5. Git Safety

Codex phải:

- Kiểm tra diff trước khi hoàn thành.
- Không sửa unrelated files.
- Không commit secrets.
- Không force push.
- Không merge khi chưa được duyệt.
- Không sử dụng destructive Git commands khi chưa được phép.

---

# 14. MẪU IMPLEMENTATION REPORT

Sau mỗi phase, Codex phải tạo báo cáo theo mẫu:

```text
# PHASE N — IMPLEMENTATION REPORT

## 1. Tổng quan
Đã triển khai những chức năng nào?

## 2. Tasks hoàn thành
Task ID:
Trạng thái:
Mô tả:

## 3. Files Changed
Created:
Modified:
Deleted:

## 4. Architecture Decisions
Các quyết định kỹ thuật đã thực hiện.

## 5. Tests Executed
Command:
Result:
Passed:
Failed:
Skipped:
Blocked:

## 6. Security Checks
Các kiểm tra bảo mật đã thực hiện.

## 7. Git Diff Summary
Tóm tắt thay đổi source code.

## 8. Known Issues
Các vấn đề chưa giải quyết.

## 9. Acceptance Criteria
PASS / FAIL / INCONCLUSIVE.

## 10. Review Request
Phase đã sẵn sàng review hay chưa?
```

Sau khi tạo báo cáo, Codex phải dừng.

---

# 15. REVIEW PROCESS

Quy trình:

```text
Codex Implement
       |
       v
Run Tests
       |
       v
Implementation Report
       |
       v
ChatGPT Review
       |
       v
Có Findings?
   |         |
  YES        NO
   |         |
   v         v
Codex Fix   Owner Approval
   |         |
   v         v
Review Again  Next Phase
```

## Mức độ lỗi

### CRITICAL

Lỗi bảo mật nghiêm trọng, nguy cơ mất dữ liệu hoặc thực thi không kiểm soát.

### HIGH

Lỗi chức năng chính hoặc vi phạm kiến trúc quan trọng.

### MEDIUM

Lỗi về maintainability, validation hoặc correctness có phạm vi giới hạn.

### LOW

Các cải tiến nhỏ về code quality hoặc documentation.

Không phê duyệt phase nếu còn Critical hoặc High findings chưa được giải quyết.

---

# 16. DEFINITION OF READY

Một phase được phép bắt đầu khi:

- Dependencies đã hoàn thành.
- Kiến trúc liên quan đã rõ.
- Scope đã được xác định.
- Acceptance Criteria có thể kiểm thử.
- Công cụ cần thiết sẵn sàng.
- Người dùng đã phê duyệt phase.

# 17. DEFINITION OF DONE

Một phase hoàn thành khi:

- Tasks bắt buộc đã implement.
- Acceptance Criteria đạt.
- Tests bắt buộc pass.
- Security requirements đạt.
- Git diff được kiểm tra.
- Known issues được ghi nhận.
- Implementation Report hoàn chỉnh.
- Project Owner phê duyệt.

---

# 18. PROMPT GIAO CODEX TRIỂN KHAI PHASE 1 (LƯU THAM KHẢO)

> Prompt lịch sử. Quyền hiện tại theo mục 13 và 19: Phase 2 AUTHORIZED.

Bạn là Software Engineer phụ trách triển khai dự án
CodePilot Harness.

Hãy đọc toàn bộ:

1. README.md
2. IMPLEMENTATION_PLAN.md

PHẠM VI ĐƯỢC PHÉP: PHASE 1 ONLY.

Hãy triển khai lần lượt:

- CP-001: Project Initialization.
- CP-002: Configuration System.
- CP-003: Gemini Adapter.
- CP-004: Interactive CLI.
- CP-005: Docker Foundation.
- CP-006: Logging & Final Validation.

YÊU CẦU:

1. Tuân thủ Modular Monolith.
2. Sử dụng Python 3.12+, uv, Typer, Rich,
   Pydantic, google-genai, TOML, dotenv,
   Docker, pytest và Ruff.
3. Gemini model ID phải cấu hình được.
4. Không hardcode secrets.
5. CLI phải có /help, /status, /exit.
6. Docker smoke test phải chạy trong container.
7. Không thực thi untrusted code trên Host.
8. Chưa triển khai Agent Loop.
9. Chưa triển khai code editing.
10. Viết unit tests.
11. Chạy tests và lint.
12. Kiểm tra Git diff.
13. Không triển khai Phase 2–8.
14. Không tự động push hoặc merge.

KHI HOÀN THÀNH:

- Tạo Phase 1 Implementation Report.
- Liệt kê tất cả file đã thay đổi.
- Báo cáo kết quả test thực tế.
- Báo cáo các acceptance criteria chưa đạt.
- Báo cáo các vấn đề còn tồn tại.
- Dừng lại và chờ review.

KHÔNG ĐƯỢC TỰ ĐỘNG BẮT ĐẦU PHASE 2.

---

# 19. TRẠNG THÁI PROJECT

| Phase | Trạng thái |
|---|---|
| Phase 1 | APPROVED (code review) |
| Phase 2 | AUTHORIZED — đã triển khai CP-007–CP-011, chờ review |
| Phase 3 | LOCKED |
| Phase 4 | LOCKED |
| Phase 5 | LOCKED |
| Phase 6 | LOCKED |
| Phase 7 | LOCKED |
| Phase 8 | LOCKED |

Chỉ Project Owner được quyền phê duyệt chuyển phase.

---

# 20. TIÊU CHÍ THÀNH CÔNG CUỐI PROJECT

CodePilot Harness được xem là hoàn thành khi:

1. Nhận coding task qua Interactive CLI.
2. Đọc hiểu Python repository.
3. Lựa chọn và gọi tools hợp lệ.
4. Đề xuất và chỉnh sửa code an toàn.
5. Chạy tests trong Docker.
6. Tự sửa lỗi trong giới hạn cho phép.
7. Không tự ý sửa Host repository.
8. Lưu và resume session.
9. Tạo execution reports có bằng chứng.
10. Đánh giá hiệu quả bằng benchmark.

Mục tiêu cuối cùng là chứng minh giá trị của
Harness Engineering thông qua:

- Architecture.
- Reliability.
- Security.
- Context Engineering.
- Tool Execution.
- Verification.
- Evaluation.

Không chỉ dựa vào khả năng sinh câu trả lời của LLM.
