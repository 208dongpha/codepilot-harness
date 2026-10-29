# Hướng dẫn sử dụng Phase 1

Cần Python 3.12+, uv và Docker Desktop ở chế độ Linux container. Chạy lệnh tại thư mục gốc repository.

```powershell
uv sync
Copy-Item .env.example .env
```

Tự đặt biến `GEMINI_API_KEY` trong `.env`. Không gửi hoặc đưa giá trị khóa vào tài liệu. Đặt `llm.model` trong `codepilot.toml` thành ID Gemini tài khoản hỗ trợ.

```powershell
docker build -f docker/Dockerfile.sandbox -t codepilot-sandbox:phase1 docker
uv run codepilot sandbox smoke-test
uv run codepilot
```

Trong CLI, dùng `/help`, `/status`, `/exit`; nhập văn bản để trò chuyện. Lịch sử nằm trong bộ nhớ của phiên; yêu cầu thất bại không được thêm vào lịch sử. EOF hoặc Ctrl+C kết thúc phiên và đóng kết nối SDK.

Thứ tự ưu tiên cấu hình: biến môi trường của tiến trình > `.env` > TOML > mặc định. Ghi đè theo tên `CODEPILOT_<SECTION>_<FIELD>`, ví dụ `CODEPILOT_LLM_MODEL`, `CODEPILOT_LLM_TEMPERATURE`, `CODEPILOT_SANDBOX_TIMEOUT_SECONDS`, `CODEPILOT_LOGGING_LEVEL`. Thời hạn gọi Gemini mặc định 60 giây; đổi qua `CODEPILOT_LLM_TIMEOUT_SECONDS`.

Trường model chỉ chấp nhận ID có dạng `gemini-...` hoặc `models/gemini-...`, gồm chữ thường, số, dấu chấm và dấu gạch nối giữa các phần. Giá trị giữ chỗ `YOUR_SUPPORTED_GEMINI_MODEL_ID` được phép để khởi động CLI nhưng không gọi Gemini. Giá trị khác bị từ chối bằng thông báo cấu hình chung; lớp hiển thị che giá trị không hợp lệ bằng `[REDACTED]`. Đây là kiểm tra cú pháp, không xác nhận model tồn tại hay phát hiện được mọi loại bí mật. Model tùy chỉnh ngoài dạng trên chưa được hỗ trợ.

Sandbox Phase 1 chỉ chạy câu lệnh Python kiểm tra cố định trong container: UID/GID 10001, tắt mạng, CPU 1, RAM 256 MiB, tối đa 64 tiến trình, hệ thống tệp chỉ đọc, bỏ capabilities và cấm tăng đặc quyền. Không gắn thư mục Host hoặc truyền khóa vào container. Container được xóa trong `finally`; lỗi cleanup được thông báo. Không có đường chạy thay thế trên Host.

```powershell
uv run pytest -q
uv run ruff check .
uv run codepilot --help
uv build
```

Unit test giả lập Gemini và Docker. Integration test chạy container thật, đối chiếu thiết lập bằng Docker inspect; chỉ bỏ qua khi daemon không khả dụng. Thiếu image làm test thất bại và cần build bằng lệnh trên. Test giả lập không chứng minh kết nối Gemini thực tế.

Log ứng dụng ghi sự kiện JSON đã định nghĩa sẵn; thông điệp ngoài danh sách được che. Chương trình hiện ghi log ra stderr, không tạo file log. Không nhập bí mật vào nội dung trò chuyện: đầu vào terminal và phản hồi hội thoại không phải kênh log được bảo vệ.

Phase 2 chưa được phê duyệt.
