# Báo cáo triển khai Phase 2 — Repository Exploration

> Cập nhật review: F-001/F-002 đã sửa, chờ review lại; Phase 2 chưa APPROVED. Kết quả hiện tại ở mục 12. Các mục 1–11 là bằng chứng của lượt triển khai ban đầu, đã được push sau đó theo quyền của Project Owner. Lượt sửa hiện tại bắt đầu từ `674a48c`, chỉ commit local, chưa push.

## 1. Tổng quan và quyền triển khai

Project Owner đã phê duyệt code review Phase 1 và cho phép CP-007 đến CP-011. Đã triển khai đủ năm task, kiểm tra regression và chuẩn bị tài liệu giải thích core AI. Trạng thái Phase 2: **đã triển khai, chờ review; chưa được phê duyệt**. Phase 3–8 vẫn LOCKED.

Theo điều chỉnh trực tiếp, làm trên `main`, không tạo branch mới. Yêu cầu ban đầu có bước commit nên đã tạo các commit local theo chức năng. Chưa có quyền push trong lượt triển khai này; không push hoặc merge.

Đã đọc README, kế hoạch triển khai, source Phase 1 và cả hai lần cập nhật AGENTS.md. Bản AGENTS.md mới nhất do người dùng cập nhật được giữ nguyên ở working tree, không tự đưa thay đổi mới đó vào commit tài liệu bàn giao. Commit `df3b208` chỉ chứa bản quy tắc trước lần cập nhật mới nhất.

## 2. Các task và chức năng đã triển khai

| Task | Kết quả triển khai | Bằng chứng chính |
|---|---|---|
| CP-007 | Registry chỉ đăng ký ba tool read-only, Pydantic strict, lỗi/kết quả thống nhất, giới hạn JSON output, event log an toàn | test_tool_registry.py |
| CP-008 | Workspace policy, list_files/read_file, kiểm tra path/link, giới hạn đọc, xử lý binary/encoding/quyền truy cập | test_file_tools.py |
| CP-009 | Ripgrep literal/regex, relative path và dòng, snippet quanh match, timeout, output/result limits, không shell hoặc fallback | test_search.py và test_search_integration.py |
| CP-010 | Python AST map package/module/class/function và số dòng, không import target, giới hạn duyệt/symbol/JSON | test_repository_map.py |
| CP-011 | Relevance theo path/symbol/search, đọc lại source, references, context JSON có budget, xuất message độc lập SDK | test_context_builder.py và test_repository_exploration.py |

CLI Phase 1 vẫn giữ nguyên hành vi trò chuyện. Phase 2 được sử dụng qua API Python, chưa thêm lệnh khám phá repository vào CLI và chưa gọi tool tự động bởi LLM.

## 3. Quyết định kiến trúc và đánh đổi

- Giữ Modular Monolith; các module `tools` và `context` tách khỏi CLI và Gemini SDK. Không thêm dependency.
- Workspace root được chốt khi khởi tạo. Tool arguments không có quyền thay root. Đường dẫn đầu vào phải tương đối; kiểm tra cả biểu diễn Windows và canonical path.
- Từ chối tất cả symlink/junction, reparse point và hardlink thay vì cho phép một số link nội bộ. Quyết định này ưu tiên sự đơn giản và hạn chế alias đường dẫn.
- Đọc tối đa số byte cấu hình, dùng file descriptor read-only, kiểm tra regular file/inode sau đọc. Chỉ xử lý UTF-8/UTF-8 BOM; chuẩn hóa newline trong bộ nhớ.
- Loại trừ đường dẫn nhạy cảm theo policy và kiểm tra nội dung theo dấu hiệu credential/private key/literal nhạy cảm. Từ chối cả file thay vì cố giữ phần còn lại khi thấy dấu hiệu nhạy cảm.
- Ripgrep chỉ nhận text đã kiểm tra qua stdin, không nhận đường dẫn target. Dùng executable cài bên ngoài workspace, `--no-config`, regex engine mặc định, danh sách đối số và environment tối thiểu. Không truyền API key của tiến trình vào search subprocess.
- Một lượt search có deadline; mỗi tiến trình nhận thời gian còn lại. Mỗi input bị giới hạn kích thước, output tạm bị giới hạn bởi input và số dòng match; JSON trả ra tiếp tục chịu `max_output_chars`. Thiếu ripgrep hoặc timeout trả lỗi rõ ràng.
- AST chỉ phân tích cú pháp; không import/evaluate source. Map có giới hạn số file, entry, độ sâu, byte, symbol, thời gian duyệt và ký tự JSON.
- Context xếp hạng bằng heuristic: +3 cho từ trùng path, +5 cho từ trùng symbol, +10 khi có search reference. Đồng điểm dùng thứ tự path. Không giả định đây là tìm kiếm ngữ nghĩa hoặc kết quả benchmark tối ưu.
- Builder dựng lại map từ file hợp lệ, không tin metadata/snippet do caller truyền vào. Excerpt lấy tối đa 30 dòng quanh reference, tối đa số file cấu hình. Kiểm tra JSON serialize gồm câu hỏi, map, source và chỉ dẫn; đây là budget ký tự, không phải token budget.
- Logger ứng dụng chỉ ghi event tool có tên/trạng thái cố định; không ghi query, đường dẫn, nội dung file hoặc exception thô. Fixture test cô lập logger để không giữ stream đã đóng giữa các test.

Chi tiết cách chạy API, resource defaults và cách giải thích phần core AI nằm trong `CORE_AI_CONTEXT_GUIDE.md`.

## 4. Danh sách tệp thêm hoặc sửa

So với mốc trước triển khai `94e8173`, gồm:

- `AGENTS.md`
- `IMPLEMENTATION_PLAN.md`
- `codepilot.toml`
- `docs/CORE_AI_CONTEXT_GUIDE.md`
- `docs/PHASE_2_IMPLEMENTATION_REPORT.md`
- `src/codepilot/config.py`
- `src/codepilot/context/__init__.py`
- `src/codepilot/context/builder.py`
- `src/codepilot/context/repository_map.py`
- `src/codepilot/telemetry/logging.py`
- `src/codepilot/tools/__init__.py`
- `src/codepilot/tools/base.py`
- `src/codepilot/tools/factory.py`
- `src/codepilot/tools/files.py`
- `src/codepilot/tools/registry.py`
- `src/codepilot/tools/search.py`
- `src/codepilot/tools/workspace.py`
- `tests/conftest.py`
- `tests/integration/test_repository_exploration.py`
- `tests/integration/test_search_integration.py`
- `tests/unit/test_context_builder.py`
- `tests/unit/test_file_tools.py`
- `tests/unit/test_repository_map.py`
- `tests/unit/test_search.py`
- `tests/unit/test_tool_registry.py`

AGENTS.md là tệp do người dùng cung cấp/cập nhật, ngoài các thay đổi source. Không sửa README, Gemini adapter, CLI hoặc Docker runtime trong Phase 2. Không sửa lockfile hoặc thêm thư viện.

## 5. Kết quả kiểm tra thực tế

Môi trường đã quan sát: Windows, Python 3.12, ripgrep 15.2.0, uv và Docker Desktop Linux containers.

| Lệnh | Kết quả |
|---|---|
| `uv sync` | PASS: 41 package đã resolve/kiểm tra, không đổi dependency |
| `uv run pytest -q` | PASS: **124 passed in 4.18s**, 0 thất bại, 0 bỏ qua |
| `uv run ruff check .` | PASS: `All checks passed!` |
| `git diff --check` | PASS: không có lỗi whitespace |
| `uv run codepilot sandbox smoke-test` | PASS: exit 0, Docker thật chạy non-root, tắt mạng, output `codepilot-smoke-ok` và `Smoke test: PASS` |

Toàn bộ 59 test trước Phase 2 được giữ lại và chạy cùng suite. Các test mới bao phủ registry, tham số sai, tên tool lạ, boundary/traversal, symlink/hardlink, alias Windows, secrets, binary/encoding, permission failure, file lớn, giới hạn duyệt/output, missing rg/timeout, regex sai, search rỗng, line references, AST và context budget/relevance.

Có integration test dùng ripgrep thật trên fixture, fixture symlink thật và luồng registry → search → map → context. Fixture có lệnh raise ở cấp module; luồng hoàn tất mà không thực thi lệnh đó. So sánh bytes trước/sau xác nhận nội dung repository fixture không bị sửa. Integration test Docker thật của Phase 1 cũng chạy, không bị bỏ qua.

Lỗi timeout ripgrep và permission denied được gây ra bằng mock. Không coi đó là bằng chứng đã làm treo ripgrep hoặc thay ACL thực tế. Các lỗi trong quá trình triển khai đã được sửa và test chạy lại; không tính lần thất bại hoặc lint lỗi ban đầu là PASS cuối.

## 6. Kiểm tra bảo mật

- Registry factory chỉ có `list_files`, `read_file`, `search_code`; không có tool write/edit hoặc run_command.
- File access dùng cờ read-only. Không có import module target, eval/exec, shell=True hoặc đường thực thi source target trong `tools`/`context`.
- Query không được diễn giải như shell option. Ripgrep không tự đọc `.ignore`, `.git` hoặc file target; chỉ đọc stdin đã kiểm tra. Executable trong workspace bị từ chối.
- Chặn path traversal, đường dẫn tuyệt đối/drive/UNC, NTFS stream và alias kết thúc bằng dấu chấm/khoảng trắng. Xác minh canonical path vẫn nằm trong workspace.
- Symlink escape và hardlink alias dùng fixture thật đã qua test. Junction/reparse point có kiểm tra trong source; chưa tạo junction thật riêng để kiểm thử.
- File nhạy cảm và nội dung tổng hợp giống secret không xuất hiện trong output/context. Builder không chuyển tiếp snippet hoặc symbol giả do caller truyền vào.
- `.env` và `api_key.md` được Git ignore. Không mở file chứa API key thật để kiểm tra loại trừ. Test chỉ dùng dữ liệu tổng hợp; không gọi Gemini live trong lượt này.
- Đã quét các file thay đổi theo mẫu credential/private key và không phát hiện mẫu khớp. Quét không in nội dung nghi là secret. Kết luận chỉ có giá trị cho nhóm mẫu và phạm vi đã quét; không cam kết phát hiện mọi bí mật.
- Không có fallback thực thi source trên Host; Docker runtime Phase 1 giữ nguyên.

## 7. Git status và diff

Các chức năng đã có commit local riêng; chưa push. Mốc nền là `94e8173`. Dùng `git diff 94e8173 -- src tests codepilot.toml IMPLEMENTATION_PLAN.md docs/CORE_AI_CONTEXT_GUIDE.md docs/PHASE_2_IMPLEMENTATION_REPORT.md` để review nội dung triển khai. Không viết lại commit cũ, không thay nhánh.

Bản `AGENTS.md` mới nhất còn modified do người dùng cập nhật trong lúc triển khai. Giữ nguyên và không đưa thay đổi mới đó vào commit bàn giao này. Source và tài liệu do Codex tạo được commit riêng theo chức năng/tài liệu; working tree cuối vì vậy có thể chỉ còn thay đổi AGENTS.md của người dùng.

## 8. Các commit local khi lập báo cáo

```text
e67d374 docs(plan): authorize repository exploration tasks
84e974c feat(tools): add validated read-only tool registry
ed1d5f1 feat(files): enforce workspace boundaries and bounded text reads
50fb02f feat(search): add bounded ripgrep search over validated text
e433d2a feat(repo): map Python symbols through bounded static analysis
6217460 fix(files): reject Windows aliases and sensitive literal content
7cabecc fix(search): preserve line references and center excerpts on matches
300ad2f fix(repo): bound supplied paths and AST traversal time
4af4282 feat(context): select referenced source within a serialized budget
df3b208 docs(git): define functional commit boundaries across the project
19bcff4 fix(repo): identify package initializer names exactly
c921de6 feat(logging): record tool execution without sensitive arguments
```

Commit tài liệu hoàn tất sau khi tạo báo cáo; xem `git log origin/main..HEAD` để lấy hash mới nhất. Tên commit theo chức năng thực tế, không theo mã CP hoặc tên Phase. Quyền commit lấy từ bước 7 của yêu cầu triển khai, không suy ra từ quyền push. Chưa push hoặc merge.

## 9. Acceptance criteria

| Tiêu chí | Trạng thái | Bằng chứng |
|---|---|---|
| Registry gọi được read-only tools | PASS | unit registry và luồng integration |
| Tham số được validate | PASS | Pydantic strict, extra bị từ chối |
| list_files trả path hợp lệ | PASS | fixture trong workspace, giới hạn và thứ tự ổn định |
| read_file đọc text trong workspace | PASS | UTF-8, newline, số dòng và output limit |
| Từ chối traversal | PASS | POSIX/Windows, absolute/drive/UNC/ADS/alias |
| Từ chối symlink escape | PASS | fixture symlink thật, không follow khi duyệt/search |
| Loại trừ sensitive files | PASS theo policy | đường dẫn/định dạng/mẫu nội dung đã test; không bảo đảm mọi bí mật chưa biết |
| Search trả line number | PASS | ripgrep thật, Unicode và match ở dòng dài |
| Search timeout và output limit | PASS | mock timeout; kết quả thật có giới hạn/cờ truncated |
| Map nhận diện cấu trúc Python | PASS | AST fixture package/module/class/function, lỗi syntax, budget |
| Context chọn relevant source | PASS | path/symbol/search, bỏ metadata/snippet giả |
| Context không vượt budget | PASS | đo chuỗi JSON thực tế, giới hạn ký tự |
| Không có write/edit tools | PASS | allowlist và snapshot fixture trước/sau |
| Không có Agent Loop | PASS | source chỉ cung cấp API read-only, không auto tool calling |
| Phase 1 không regression | PASS trong phạm vi test | 59 test cũ cùng toàn bộ suite; Docker thật PASS |
| Gemini live/quota hiện tại | NOT VERIFIED | không chạy Gemini trong Phase 2 |
| Phase 2 Owner Approval | Chưa có | chờ review |

## 10. Giới hạn và câu hỏi review

- Heuristic relevance chưa hiểu ngữ nghĩa hoặc từ đồng nghĩa. File bị cắt map/traversal có thể không được chọn theo symbol. Khi thiếu dữ liệu cần thể hiện điều đó trong câu trả lời.
- Budget là số ký tự JSON, chưa đo token Gemini hoặc toàn bộ lịch sử hội thoại. Không cam kết mọi bundle vừa context window của mọi model.
- Bộ lọc secret có thể bỏ sót định dạng mới hoặc từ chối nhầm source hợp lệ. Không xác minh log/transcript bên ngoài workspace hoặc trạng thái thu hồi credential ở phía tài khoản.
- Chỉ UTF-8/UTF-8 BOM và module `.py` được xử lý theo chính sách hiện tại. Map chỉ chứa định nghĩa tĩnh, không phân tích dynamic import, tên tạo lúc runtime hoặc quan hệ gọi hàm.
- AST parse bị giới hạn kích thước input, nhưng chưa dùng worker để cưỡng chế timeout/memory riêng ngay bên trong `ast.parse`. Deadline được kiểm tra khi duyệt file/AST. Không xem đây là sandbox chống mọi input AST đối kháng.
- Repository có tiến trình ngoài đồng thời thay đổi không được snapshot nguyên tử. Kiểm tra link/canonical path/inode không phải chứng minh chống mọi race filesystem. Các đường tấn công đồng thời đối kháng chưa được kiểm chứng toàn diện.
- Search trên stdin từng file ưu tiên kiểm soát boundary hơn tốc độ ở repository rất lớn. Không có benchmark hiệu năng lớn hoặc fuzzing trong lượt này.
- ReadOnlyTool callbacks là code Harness tin cậy; cờ read_only/allowlist không phải sandbox cho plugin do bên thứ ba tùy ý đăng ký.
- Metadata và source trong JSON được đánh dấu là dữ liệu không đáng tin; chưa cam kết vô hiệu hóa mọi prompt injection. Không có quyền edit/execute để model khai thác trong Phase 2.

## 11. Yêu cầu review

Source, tests, hướng dẫn core AI và các commit local đã sẵn sàng cho reviewer. Nhờ kiểm tra boundary, giới hạn tài nguyên, heuristic relevance và các giới hạn công khai trên. Không tự phê duyệt Phase 2. Dừng tại đây, không push/merge hoặc bắt đầu Phase 3 nếu chưa có chỉ thị mới.

## 12. Kết quả sửa review F-001 và F-002

### 12.1. Phạm vi và thay đổi

Chỉ sửa metadata và việc lựa chọn/cắt dữ liệu của CP-010/CP-011. Không thay đổi kiến trúc Modular Monolith, cấu hình policy, kiểm tra path/link/secret, allowlist tool, Docker hoặc Gemini adapter. Không triển khai Agent Loop, write/edit hay Phase 3. Làm trên main; không push hoặc merge trong lượt sửa này.

**F-001 — References chính xác:** Thay việc cắt đôi chuỗi bằng cắt nguyên dòng từ biên xa dòng trọng tâm nhất. Luôn giữ dòng trọng tâm nếu còn đủ budget cho excerpt. Cập nhật start_line/end_line theo các dòng thực tế còn lại. Chỉ khi riêng một dòng quá dài mới tìm prefix vừa budget bằng tìm kiếm nhị phân; đặt partial_line=true và start_line=end_line. Không cắt theo byte nên không phá ký tự Unicode. Giữ cách chuẩn hóa CRLF/CR thành LF có sẵn trong Workspace; nội dung excerpt không bao gồm terminator của dòng cuối.

Test mới đối chiếu trực tiếp nội dung với các dòng gốc của fixture, thay vì chỉ đếm dòng từ chuỗi đã cắt. Bao phủ budget 900/1200 ký tự, Unicode, dòng dài và reference tập trung ở dòng 46. Test dòng Unicode quá dài xác nhận prefix không rỗng, partial_line=true và references đúng một dòng. Bộ test mới đã tái hiện lỗi trên implementation cũ trước khi sửa.

**F-002 — Kết quả thiếu dữ liệu rõ ràng:** Thêm thuộc tính tính toán incomplete, danh sách reasons hữu hạn và các số đếm. Phân biệt kết quả rỗng đầy đủ với thiếu dữ liệu do scan/resource limits, nguồn không đọc được, syntax errors, map budget hoặc context budget. Giữ số đếm và lý do của map đầu vào khi rebuild, không thay chúng bằng trạng thái của danh sách module còn lại.

Workspace chỉ thêm số đếm entry bị bỏ qua sau lỗi; không đổi quyết định cho phép/từ chối truy cập. Map ghi nhận scan_skipped, skipped, syntax_errors. Context giữ input_map_skipped/input_map_syntax_errors riêng, và thêm skipped_sources/scan_skipped/omitted_sources. Số đếm phản ánh các lần bỏ qua hoặc ứng viên đã biết, không khẳng định đếm được các file chưa duyệt và không phải thống kê file duy nhất giữa nhiều bước.

Mã reasons giới hạn ở 15 loại đã định nghĩa, được gộp để không lặp theo số file. Metadata không chứa raw exception, path bị chặn hoặc nội dung nguồn bị bỏ qua. Cả metadata và thuộc tính computed đều được tính trong JSON budget. Map bỏ module nếu cần để giữ metadata; khi riêng metadata không vừa thì trả MAP_BUDGET_TOO_SMALL hoặc CONTEXT_BUDGET_EXCEEDED, không trả dữ liệu vượt budget và không che mất việc thiếu dữ liệu.

### 12.2. Kết quả kiểm tra thực tế

| Lệnh | Kết quả lượt sửa |
|---|---|
| `uv sync` | PASS: resolve 41 package, kiểm tra 41 package, không đổi dependency |
| `uv run pytest -q` | PASS: **147 passed in 4.28s**, 0 thất bại, 0 bỏ qua |
| `uv run ruff check .` | PASS: All checks passed! |
| `git diff --check` | PASS: không có lỗi whitespace |
| `uv run codepilot sandbox smoke-test` | PASS: exit 0, Docker thực tế, codepilot-smoke-ok, Smoke test: PASS |

Toàn bộ 124 test trước lượt review vẫn chạy trong suite. Có thêm 23 trường hợp hồi quy sau khi mở rộng tham số, gồm references/budget/Unicode và từng nhóm kết quả chưa đầy đủ: scan limit, entry không dùng được, file không đọc được, file lớn, byte budget, symbol/map budget, deadline, input limit, source count, context budget, syntax errors và việc giữ metadata qua rebuild. Test lặp 100 lỗi đọc xác nhận metadata vẫn gọn, không tạo danh sách path lỗi. File rỗng được đọc thành công không bị báo là exploration thất bại.

Các tình huống permission/deadline được mô phỏng bằng mock; không tuyên bố đã làm hỏng quyền hoặc daemon thật. Integration tests ripgrep/Docker và boundary/credential regression cũ đều chạy, không bỏ qua. Không mở hoặc in API key thật, không gọi Gemini live trong lượt sửa này.

### 12.3. Tệp thay đổi trong lượt sửa

1. `src/codepilot/context/builder.py` — cắt nguyên dòng, partial_line, coverage metadata, bảo toàn input diagnostics và budget cuối.
2. `src/codepilot/context/repository_map.py` — lý do thiếu dữ liệu, số đếm scan, incomplete và giới hạn metadata/map output.
3. `src/codepilot/tools/workspace.py` — thêm số đếm entry bị bỏ qua, giữ nguyên policy và file access.
4. `tests/unit/test_context_builder.py` — hồi quy references và các trường hợp context chưa đầy đủ.
5. `tests/unit/test_repository_map.py` — kiểm tra lý do thiếu dữ liệu và metadata hữu hạn.
6. `docs/CORE_AI_CONTEXT_GUIDE.md` — giải thích cách cắt mới và cách đọc coverage metadata.
7. `docs/PHASE_2_IMPLEMENTATION_REPORT.md` — bằng chứng và giới hạn của lượt sửa này.

### 12.4. Commit local và review

- `1395983 fix(context): preserve focused line boundaries when fitting excerpts`
- `3b6fc3f fix(context): preserve bounded exploration coverage diagnostics`

Tài liệu được commit riêng sau khi cập nhật báo cáo; hash cuối có trong Git log. Chưa push và không merge.

### 12.5. Giới hạn còn lại

- Budget vẫn tính bằng ký tự JSON, không phải token Gemini. Metadata mới chiếm một phần budget trước đây dành cho source; budget quá nhỏ có thể trả lỗi rõ ràng.
- scan_limit gộp các giới hạn duyệt của Workspace; chưa phân loại riêng entry/file/depth/scan timeout. Số file thực sự chưa được duyệt không xác định được nên chỉ có cờ/lý do, không đưa số đếm giả.
- incomplete là tín hiệu bảo thủ: cả việc chỉ đính kèm một cửa sổ của file cũng được ghi nhận bằng source_window. Nó không khẳng định mọi phần bị bỏ đều có liên quan đến câu hỏi.
- Vẫn giữ giới hạn đã công khai ở mục 10: snapshot nhiều file không nguyên tử, AST parse chưa bị cưỡng chế bằng worker riêng, secret detection theo mẫu và không xác minh Gemini live/quota.

F-001 và F-002 đã có sửa đổi và test tương ứng. Phase 2 chưa được phê duyệt; dừng chờ review, Phase 3–8 vẫn khóa.
