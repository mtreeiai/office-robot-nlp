# Phase 8B.2 — Priority và strict order

Đã sửa hướng dẫn NLU và structured-output schema để phân biệt ưu tiên một task
với thứ tự cố định của toàn batch. Không thêm scheduling hoặc HTTP API.

## Quy tắc diễn giải

Với câu:

```text
Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hồ sơ trước.
```

Candidate mong đợi giữ thứ tự các item và có:

| sequenceIndex | Tài liệu | Room ID sau resolver | Priority |
|---:|---|---|---|
| 0 | hợp đồng | 02 | HIGH |
| 1 | báo cáo | 05 | NORMAL |
| 2 | hồ sơ | 07 | NORMAL |

orderPolicy là PRIORITY. Từ “trước” hoặc ưu tiên một tài liệu không quy định
thứ tự cố định cho các tài liệu còn lại. Priority không sắp lại mảng tasks.

STRICT_ORDER chỉ dùng khi có yêu cầu cố định thứ tự toàn batch, ví dụ
“Giao lần lượt theo đúng thứ tự đã nêu.” Strict order tự nó không làm task đầu HIGH.
Nếu yêu cầu toàn batch thực sự là STRICT_ORDER và có thêm ưu tiên một item,
vẫn giữ cả hai ý nghĩa. Không dùng regex hoặc hậu xử lý để tự đổi STRICT_ORDER
thành PRIORITY. Conflict được chuyển sang câu hỏi làm rõ hiện có.

Prompt có ba ví dụ đối chiếu: priority-only, whole-batch strict order và strict
order xung đột với task HIGH xuất hiện sau. Schema có description cho orderPolicy,
tasks, priority và từng policy điều kiện. Contract enum và decoder không bị đổi.

## Ý nghĩa orderConfirmed=False

Tên field lịch sử này chỉ đánh dấu việc người dùng đã chọn policy sau một câu hỏi
xung đột thứ tự/priority. Nó không phải cờ “batch hợp lệ”, “robot đã nhận” hay
“cần người dùng xác nhận thêm mọi yêu cầu”.

- Batch PRIORITY hợp lệ: False là bình thường.
- STRICT_ORDER không xung đột: False cũng là bình thường, vẫn có BatchReady.
- Khi có xung đột: chưa đăng ký task; người dùng trả lời BATCH_ORDER thì flag
  mới thành True và policy lựa chọn được giữ.
- Provider không được gửi orderConfirmed=True để tự bỏ qua câu hỏi.

BatchReady phụ thuộc việc xác thực không còn issue. Đã bổ sung docstring/comment
và regression tests cho ý nghĩa này; không thay đổi logic conflict.

## ASK_USER có phải default không?

Không. Audit code không tìm thấy bước mặc định ifBusy/ifClosed thành ASK_USER.
Batch candidate thiếu constraints được decoder điền `{ifBusy: None, ifClosed: None}`.
Các policy chưa nêu phải là null trong output NLU.

ASK_USER trong candidate được decoder giữ nếu provider trả về giá trị đó, giống
WAIT và RETRY. Vì vậy ASK_USER xuất hiện trong một lượt không có yêu cầu policy
không phải bằng chứng người dùng đã chọn nó, cũng không phải default do runtime
điền: đó là nội dung do provider cung cấp. Decoder không xác minh grounding ngôn ngữ.

Hướng dẫn mới yêu cầu chỉ lấy policy từ yêu cầu tường minh, dùng null khi thiếu,
không suy ra ASK_USER từ urgency, strict order hoặc trạng thái phòng chưa biết.
Policy tường minh vẫn được giữ nguyên, không bị tự xóa trong hậu xử lý.

## File và kiểm chứng

Sửa trong phạm vi fix:

- `office_robot_nlp/clu.py`: hướng dẫn và ba ví dụ semantic đối chiếu.
- `office_robot_nlp/batch_schema.py`: description cho batch order, priority và policies.
- `office_robot_nlp/gemini_adapter.py`: guidance policy/priority cho schema task đơn.
- `office_robot_nlp/domain/batch.py`: làm rõ ý nghĩa orderConfirmed.
- `office_robot_nlp/batch_validation.py`: comment về cờ xác nhận xung đột.

Thêm `tests/test_order_semantics.py`: 10 fake-provider regression tests.
Các test kiểm tra request thật được adapter dựng có đúng prompt/schema guidance,
sau đó chạy candidate từ fake transport qua DialogueManager, resolver và registry.
Bao phủ ưu tiên một item, ưu tiên item ở giữa, strict order hợp lệ, strict order
kèm ưu tiên, cả hai lựa chọn giải quyết xung đột, sequenceIndex, null/default policies,
policy tường minh và pipeline TaskReady đơn.

Đã chạy thành công:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall office_robot_nlp tests
```

Kết quả: **247/247 test pass**, gồm toàn bộ 237 test trước và 10 test mới.
Compileall thành công. Không gửi request tới provider thật.

Fake transport xác minh hướng dẫn gửi đi và hành vi xác định của pipeline, không
chứng minh Gemini thật luôn diễn giải đúng. Câu live đã báo cần được thử lại thủ
công với prompt/schema mới; fix này không tự chạy live evaluation hoặc tiêu quota.

Dừng sau semantic interpretation fix.
