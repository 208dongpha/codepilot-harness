# Báo cáo rà soát độc lập Phase 1

## Phạm vi và kết quả

Chỉ kiểm tra CP-001 đến CP-006. Đã sửa hai vấn đề bảo mật trong phạm vi Phase 1, bổ sung test hồi quy, cập nhật toàn bộ tài liệu dưới `docs` sang tiếng Việt. Không thêm chức năng tác nhân, không triển khai Phase 2, không commit, push hoặc merge.

Mốc Git nền: `28344e0` (`docs: expand project README`). Source hiện nằm trong working tree, bản vá đầy đủ tại `docs/review/phase1.patch`. Danh sách tệp và SHA-256 bản vá được ghi trong `docs/review/THONG_TIN.md`.

## Phát hiện và sửa đổi

### HIGH — Model có thể chứa và hiển thị nhầm credential

Trước sửa, validation chỉ kiểm tra không rỗng/không có khoảng trắng; startup và `/status` hiển thị trực tiếp giá trị model. Đã sửa `src/codepilot/config.py` để chỉ nhận cú pháp ID Gemini (`gemini-...`, tùy chọn tiền tố `models/`) hoặc giá trị giữ chỗ. `display_model` che toàn bộ giá trị không phù hợp bằng `[REDACTED]`; CLI dùng hàm này ở cả hai vị trí. Không đưa input vào lỗi cấu hình.

Đã kiểm thử bằng dữ liệu tổng hợp: chuỗi giống credential, header Bearer, ký tự xuống dòng, query chứa key, model hợp lệ và lỗi cấu hình CLI. Cú pháp không chứng minh model tồn tại hoặc có quyền truy cập. Không thể phát hiện mọi bí mật được cố tình viết theo đúng cú pháp model. Model tùy chỉnh ngoài dạng đã ghi nhận bị từ chối; đây là giới hạn hiện tại.

### MEDIUM — Formatter log không tự ngăn nội dung tùy ý

Trước sửa, formatter chuyển tiếp `record.getMessage()` nguyên văn. Các điểm log hiện tại dùng thông điệp cố định nhưng formatter chưa có hàng rào nếu sau này nhận raw exception. Đã giới hạn sự kiện cho phép; nội dung ngoài danh sách bị thay bằng `[REDACTED]`, không đưa traceback vào JSON. Test kiểm tra nội dung ngoài danh sách không được xuất.

Phạm vi bảo vệ là logger ứng dụng `codepilot`. Không khẳng định kiểm soát logger của mọi thư viện, terminal echo hoặc phản hồi hội thoại.

## Docker và đường thực thi

Rà soát toàn bộ `src` chỉ thấy `subprocess.run` trong Docker runtime, với đối số đầu luôn là `docker`, `shell` không bật. Không thấy `os.system`, `eval`, `exec` hoặc nhánh gọi Python trên Host khi Docker lỗi. Unit test xác nhận trường hợp không có Docker chỉ gọi Docker và trả lỗi.

Integration test đã gọi Docker inspect trên container thật trước khi cleanup, xác nhận:

| Thiết lập | Giá trị quan sát |
|---|---|
| User | `10001:10001` |
| NetworkMode | `none` |
| NanoCpus | `1000000000` tương ứng 1 CPU |
| Memory | `268435456` tương ứng 256 MiB |
| PidsLimit | `64` |
| ReadonlyRootfs | `true` |
| Privileged | `false` |
| Số mounts | `0` |
| CapDrop | `ALL` |
| SecurityOpt | Có `no-new-privileges` |

Không có cờ truyền biến môi trường, gắn Docker socket hoặc thư mục Host trong lệnh tạo container. Smoke command kiểm tra UID khác root trong container. Không còn container smoke sau kiểm tra. Timeout và lỗi cleanup được kiểm thử giả lập; không cố tình làm cạn tài nguyên thật hoặc đánh giá mọi hình thức container escape.

## Secrets và phạm vi log

Đã quét tự động 30 tệp working tree được Git liệt kê và 2 tệp trong commit HEAD bằng dấu hiệu khóa Google, credential dạng token, token GitHub, Bearer token và private key. Không phát hiện mẫu khớp. Quét không xuất đoạn văn bản hoặc giá trị nghi là secret. Không mở `.env` hoặc `api_key.md`; cả hai được `git check-ignore` xác nhận bị ignore.

Không tìm thấy tệp `.log` trong workspace sau khi loại `.git` và `.venv`. Chương trình hiện ghi log ra stderr; các test lỗi provider và redaction dùng dữ liệu tổng hợp. Bộ tài liệu và bản vá sau khi tạo được quét lại bằng cùng nhóm dấu hiệu.

**NOT VERIFIED:** Không so sánh với API key thật và không bảo đảm phát hiện mọi loại bí mật. Không quét cache, file nội bộ Git, transcript công cụ, log terminal hoặc log ngoài workspace. Lượt triển khai trước từng có output hiển thị giá trị giống credential do nhập nhầm trường model; không thể xác nhận bản sao output đó đã được xóa. Người dùng báo đã thay, nhưng trạng thái thu hồi phía tài khoản chưa được kiểm chứng. Vì vậy không kết luận “mọi log đều sạch”. Không lặp lại giá trị đó trong tài liệu.

## Kết quả chạy lại

| Lệnh | Kết quả thực tế |
|---|---|
| `uv run pytest -q` | PASS: 43 test đạt, 0 thất bại, 0 bỏ qua, 2.71 giây; có integration test Docker thật |
| `uv run ruff check .` | PASS: `All checks passed!` |
| `uv run codepilot sandbox smoke-test` | PASS: exit 0, non-root, network disabled, `codepilot-smoke-ok` |
| `git diff --check` | Không có lỗi whitespace |

Không chạy lại live Gemini trong lượt này vì không cần đọc hoặc sử dụng API key để thực hiện yêu cầu rà soát. Bằng chứng live trước đây được ghi riêng trong Implementation Report; trạng thái kết nối/quota hiện tại: NOT VERIFIED.

## Reviewer kiểm tra độc lập

Đọc source và các test trong working tree hoặc áp dụng bản vá vào checkout tách biệt từ mốc Git nền:

```powershell
git apply --check D:/Dongphh/AIcoding/docs/review/phase1.patch
git apply D:/Dongphh/AIcoding/docs/review/phase1.patch
uv sync
docker build -f docker/Dockerfile.sandbox -t codepilot-sandbox:phase1 docker
uv run pytest -q
uv run ruff check .
uv run codepilot sandbox smoke-test
```

Chạy `git apply` trong checkout sạch đúng mốc nền, không chạy lại trên working tree hiện tại. Không cần API key để chạy unit test, CLI help hoặc Docker smoke. Docker daemon không có sẽ làm integration test bỏ qua; trường hợp này không đủ nghiệm thu Docker.

Tệp bản vá chứa source, test và tài liệu; không chứa `.env` hoặc `api_key.md`. Thư mục `docs/review` chỉ chứa tài liệu bàn giao và bản vá sinh ra, không nằm bên trong chính bản vá để tránh tự tham chiếu.

## Kết luận

Source và bằng chứng local đã sẵn sàng cho reviewer. Các giới hạn secret scan, log lịch sử, quota hiện tại và kiểm chứng bảo mật toàn diện vẫn chưa được xác minh như mô tả ở trên. Chưa có phê duyệt Phase 2.
