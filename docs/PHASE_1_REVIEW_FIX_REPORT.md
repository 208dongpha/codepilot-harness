# Báo cáo sửa findings review Phase 1

## 1. Phạm vi

Chỉ xử lý F-001, giữ cơ chế credential safety của F-002 và thực hiện F-003. Không thêm chức năng Phase 2. Các sửa đổi còn trong working tree để review cuối; chưa commit, push hoặc merge.

## 2. F-001 — Timeout và cleanup Docker

### Nguyên nhân

`subprocess.run` timeout chỉ kết thúc Docker CLI phía Host; không chứng minh tiến trình trong container đã dừng. Runtime trước đây gọi xóa trong `finally` nhưng không kiểm tra lại sự tồn tại của container. Nếu cleanup lỗi, lỗi đó có thể che mất lỗi thực thi ban đầu. Sự kiện PASS còn được ghi trước khi cleanup hoàn tất.

### Sửa đổi

- Sau khi đã bắt đầu tạo container, mọi đường thoát vẫn yêu cầu `docker rm --force` trong `finally`, kể cả timeout khi tạo hoặc chạy container. Đây là yêu cầu Docker buộc dừng/xóa đúng container có tên đã tạo, không chạy lệnh thay thế trên Host.
- Phân biệt lỗi timeout Docker CLI và lỗi không khởi chạy được Docker bằng thông báo cố định, không đưa stdout/stderr tùy ý vào lỗi.
- Xử lý riêng timeout/lỗi gọi lệnh xóa và exit code khác 0. Tiếp tục thử xác minh trạng thái ngay cả khi lệnh xóa báo lỗi.
- Dùng `docker container ls --all` với bộ lọc tên chính xác. Chỉ exit code 0 và kết quả rỗng mới là bằng chứng container không còn tồn tại. Exit code lỗi hoặc timeout truy vấn được báo `NOT VERIFIED`, kèm lý do an toàn. Kết quả không rỗng báo container vẫn tồn tại; không coi là đã dừng hoặc xóa.
- Nếu lệnh xóa lỗi nhưng truy vấn chứng minh container đã biến mất, vẫn báo lỗi lệnh cleanup và ghi rõ đã xác minh sự vắng mặt. Không đổi một lệnh lỗi thành PASS.
- Khi cả thực thi và cleanup đều lỗi, thông báo cuối giữ cả hai lỗi cùng tên container để người vận hành kiểm tra. Không lặp vô hạn; các lệnh xóa và xác minh đều có timeout 10 giây.
- Chỉ ghi `docker_smoke PASS` sau khi smoke command thành công và cleanup có bằng chứng xác minh. Cleanup lỗi ghi `docker_cleanup FAIL` và ném lỗi ra CLI.

### Test hồi quy

Có 21 unit test Docker, gồm các trường hợp thành công, thiếu Docker, timeout thực thi, timeout tạo container, container trả exit code lỗi, lệnh xóa trả lỗi/timeout/không gọi được, container còn tồn tại và truy vấn trạng thái lỗi/timeout. Ma trận lỗi liên tiếp kiểm tra giữ cả lỗi thực thi lẫn cleanup, đúng tên container, lệnh chỉ gọi Docker và timeout hữu hạn. Test xác nhận không ghi PASS nếu thiếu bằng chứng cleanup.

Trước sửa, bộ test cập nhật tái hiện 18 thất bại và 3 test đạt trên implementation cũ. Sau sửa, các test này đều đạt; không hạ tiêu chí để làm test xanh.

## 3. F-002 — Credential safety

Không thay đổi `config.py`, validation/redaction model, CLI hoặc logger. Đã chạy riêng regression tests liên quan model/credential: 9 test đạt, 15 test không thuộc lựa chọn. Dữ liệu test là dữ liệu tổng hợp. Không mở hoặc in API key thật; không gọi Gemini trực tiếp trong lượt sửa này. Lệnh smoke dùng cơ chế nạp cấu hình sẵn có, không hiển thị giá trị khóa.

## 4. F-003 — Kết quả thực tế

| Lệnh | Kết quả |
|---|---|
| `uv run pytest -q` | PASS: 59 test đạt trong 3.12 giây, 0 thất bại, 0 bỏ qua |
| `uv run ruff check .` | PASS: `All checks passed!` |
| `uv run codepilot sandbox smoke-test` | PASS: exit 0, output `codepilot-smoke-ok`, thông báo `Smoke test: PASS` |
| `git diff --check` | PASS: không có lỗi whitespace |
| Liệt kê container có tiền tố `codepilot-smoke` sau kiểm tra | Không còn container khớp |

Suite có integration test chạy Docker thật, kiểm tra các security flags bằng inspect. Smoke test thực tế hoàn tất với bước xác minh container đã biến mất trong runtime mới. Các cờ non-root, tắt mạng, giới hạn CPU/RAM/PID, chỉ đọc, bỏ capabilities và không gắn thư mục Host được giữ nguyên.

## 5. Danh sách tệp thay đổi

- `src/codepilot/execution/docker_runtime.py`: xử lý lỗi Docker, cleanup có xác minh, giữ lỗi liên tiếp, dời log PASS.
- `tests/unit/test_docker_runtime.py`: cập nhật kỳ vọng xác minh cleanup và bổ sung test hồi quy cho lỗi liên tiếp.
- `docs/PHASE_1_REVIEW_FIX_REPORT.md`: báo cáo lượt sửa này bằng tiếng Việt.

Không sửa tài liệu bàn giao/bản vá cũ: chúng là ảnh chụp của lượt review trước. Reviewer dùng Git diff hiện tại cho lượt sửa này.

## 6. Giới hạn và phần chưa xác minh

- Timeout, mất Docker daemon và cleanup lỗi được gây ra bằng mock trong unit test; chưa cố tình làm Docker daemon thực tế mất kết nối hoặc treo. Không tuyên bố đã xác minh thực tế các tình huống phá lỗi này.
- Khi Docker không trả được kết quả truy vấn, trạng thái container thực tế vẫn là NOT VERIFIED. Runtime yêu cầu xóa và báo lỗi có tên container; không thể bảo đảm container đã biến mất nếu daemon không đáp ứng.
- Bộ lọc truy vấn xác minh trạng thái tại thời điểm lệnh hoàn tất, không phải giám sát liên tục. Lượt sửa không phải kiểm định toàn diện container escape hoặc tài nguyên dưới tải.
- Không kiểm tra Gemini live/quota, trạng thái thu hồi khóa hoặc log ngoài workspace trong lượt sửa này.

## 7. Yêu cầu review cuối

F-001 đã có sửa đổi và test hồi quy; F-002 không thay đổi cơ chế bảo vệ; F-003 đã có bằng chứng chạy lệnh. Dừng tại đây để reviewer kiểm tra độc lập. Phase 2 chưa được phép triển khai.
