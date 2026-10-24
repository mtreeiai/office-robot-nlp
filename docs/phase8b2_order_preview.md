# Phase 8B.2 — Deterministic Order Preview, Option A

Đã bổ sung thứ tự dự kiến chỉ đọc, tách biệt thứ tự yêu cầu gốc, policy đã chọn
và thứ tự thực thi tương lai. Batch hợp lệ được ghi nhận ngay, không hỏi xác nhận
thêm. Không triển khai scheduler hoặc điều khiển robot.

## 1. File trong phần cải tiến này

Tạo mới:

- `office_robot_nlp/order_preview.py`: hàm thuần build_order_preview.
- `tests/test_order_preview.py`: 15 test xác định không gọi provider thật.
- `docs/phase8b2_order_preview.md`: báo cáo và hướng dẫn này.

Sửa:

- `office_robot_nlp/domain/batch.py`: thêm DTO frozen OrderPreviewItem.
- `office_robot_nlp/response_plan.py`: facts preview và template giải thích policy.
- `office_robot_nlp/runtime_monitor.py`: thứ tự gốc, preview và execution status riêng.
- `text_conversation.py`: tạo plan có preview sau đăng ký batch nguyên tử.
- `batch_conversation_demo.py`: task HIGH ở cuối và cả hai lựa chọn sau xung đột.
- `docs/phase8b2.md`: liên kết bản bổ sung hiện hành.

Không sửa luồng TASK_COMMAND, resolver, dữ liệu 7 phòng thật, lifecycle transitions,
robot snapshot, handoff/load ownership, luật conflict hoặc cancellation.

## 2. Hàm preview thuần

```python
from office_robot_nlp.order_preview import build_order_preview

preview = build_order_preview(batch, task_records)
```

Đầu vào là DeliveryBatch đã được xác thực/đăng ký cùng các TaskMonitoringRecord
hiện có. Mỗi OrderPreviewItem chỉ tham chiếu task bằng taskId, không tạo task mới.
Các field gồm taskId, sequenceIndex, previewPosition, documentText, destinationId,
destinationText và priority. previewPosition bắt đầu từ 1, sequenceIndex vẫn từ 0.

PRIORITY dùng khóa sort `(priority_rank, sequenceIndex)` với HIGH=0, NORMAL=1,
LOW=2. STRICT_ORDER dùng sequenceIndex, kể cả priority tương đối khác nhau sau khi
người dùng đã giải quyết conflict. Việc cho phép strict batch thuộc bước validation
hiện có, không được kiểm tra lại hoặc ghi đè policy trong hàm preview.

Hàm đọc dữ liệu từ batch references và task records, trả tuple item frozen. Không
ghi vào batch/tasks/robot, không đăng ký, dispatch hoặc cập nhật lifecycle. Không
dùng LLM, khoảng cách, map pose, khả năng mang tài liệu hoặc room availability.
Thiếu record sẽ báo lỗi, không dựng task giả.

## 3. Giữ sequenceIndex và thứ tự gốc

DeliveryBatch.tasks giữ nguyên tuple ban đầu. sorted() chỉ tạo thứ tự trình bày mới.
Ví dụ bản ghi thực của demo:

| Task | Tài liệu | ID phòng | Priority | sequenceIndex | previewPosition |
|---|---|---|---|---:|---:|
| task_001 | hợp đồng | 02 | NORMAL | 0 | 2 |
| task_002 | báo cáo | 05 | NORMAL | 1 | 3 |
| task_003 | hồ sơ | 07 | HIGH | 2 | 1 |

Các ID, recipient, constraints và lifecycle không đổi. Preview không phải lịch
thực thi được xác nhận; Task Scheduler/robot execution chưa có trong phase này.

## 4. ResponsePlan và template

response_plan_for_registered_batch() bổ sung orderPreview từ cùng hàm thuần mà
monitor sử dụng, cùng batchId/taskIds thực tế. Facts tasks vẫn giữ thứ tự yêu cầu
gốc, còn facts orderPreview chứa vị trí dự kiến riêng.

orderPolicySelectedByUser được suy ra từ draft.orderConfirmed, nghĩa là người
dùng đã trả lời câu hỏi chọn policy sau conflict. Không lưu thêm workflow/state
confirmation mới. Một yêu cầu STRICT_ORDER ban đầu hợp lệ vẫn có thể có flag False.

Template giữ lời ghi nhận các task, mô tả policy và thứ tự dự kiến đầy đủ, ví dụ:

```text
Đã chọn giao theo mức ưu tiên. Mình đã ghi nhận 2 nhiệm vụ: báo cáo đến phòng 5
và hợp đồng đến phòng 2. Ưu tiên cao: hợp đồng đến phòng 2.
Thứ tự dự kiến (theo mức ưu tiên) là: hợp đồng đến phòng 2 trước,
sau đó báo cáo đến phòng 5.
Việc thực hiện còn phụ thuộc điều kiện vận hành và hệ thống robot tiếp nhận.
```

Chọn STRICT_ORDER sẽ nói rõ “Đã chọn giữ đúng thứ tự bạn yêu cầu” và hiển thị
báo cáo → phòng 5 trước hợp đồng → phòng 2, dù hợp đồng vẫn giữ priority HIGH.
Preview được sinh bằng template xác định. Luồng batch tiếp tục bỏ qua optional
Gemini NLG như trước, nên provider NLG không thể đổi thứ tự hoặc claim thực thi.
Luồng đơn và optional NLG của luồng đơn không đổi.

## 5. Clarification theo Option A

Khi có conflict, chưa có task/batch nào của yêu cầu đó được đăng ký. Câu hỏi cũ
vẫn được giữ. Sau câu trả lời “theo mức ưu tiên” hoặc “giữ đúng thứ tự”:

1. DialogueManager chọn PRIORITY hoặc STRICT_ORDER và đặt orderConfirmed=True.
2. Xác thực lại toàn bộ draft.
3. Nếu hợp lệ, đăng ký một lần bằng transaction trong bộ nhớ hiện có.
4. Tạo preview từ records đã đăng ký và trả lời ngay, không có bước hỏi đồng ý thêm.

Câu trả lời mơ hồ/sai ngữ cảnh giữ nguyên pending batch và chưa đăng ký task.
Nếu còn thiếu thông tin semantic khác, clarification hiện có vẫn tiếp tục cho
thông tin đó. Cơ chế thử lại khi lỗi đăng ký nội bộ vẫn giữ như trước.

## 6. orderConfirmed

False không phải “batch chưa hợp lệ” hoặc “mọi batch đều cần thêm xác nhận”.
Batch PRIORITY bình thường hoặc STRICT_ORDER không conflict vẫn ghi nhận ngay.
Conflict chỉ được bỏ qua sau lựa chọn policy tường minh, khi flag thành True.
Unresolved conflict không đi tới BatchReady. Không thay đổi ý nghĩa field hiện có.

## 7. Terminal monitor và commands

`/batch <batchId>` và batch debug sau intake dùng cùng renderer, có:

```text
[ORIGINAL REQUEST ORDER]
1. hợp đồng -> phòng 2 — NORMAL (taskId=task_001; sequenceIndex=0; destinationId=02)
2. báo cáo -> phòng 5 — NORMAL (taskId=task_002; sequenceIndex=1; destinationId=05)
3. hồ sơ -> phòng 7 — HIGH (taskId=task_003; sequenceIndex=2; destinationId=07)

[ORDER PREVIEW — NOT EXECUTED]
1. hồ sơ -> phòng 7 — HIGH (taskId=task_003; sequenceIndex=2; destinationId=07)
2. hợp đồng -> phòng 2 — NORMAL (taskId=task_001; sequenceIndex=0; destinationId=02)
3. báo cáo -> phòng 5 — NORMAL (taskId=task_002; sequenceIndex=1; destinationId=05)

[EXECUTION STATUS]
Scheduler      : NOT_IMPLEMENTED
Robot Accepted : NO
Document Load  : UNCONFIRMED
Physical Delivery: NOT_STARTED
```

Monitor tiếp tục in chi tiết task ID, sequence, lifecycle, handoff, load, provenance
và snapshot robot thực tế. Khi có sự kiện monitoring riêng, các field đó lấy từ
records hiện tại; preview không tạo sự kiện hoặc thay đổi trạng thái này.

NOT EXECUTED mô tả tính chất preview; actual execution status luôn đọc riêng từ
lifecycle/nguồn sự kiện, không suy từ vị trí trong preview. Các lệnh /batches,
/tasks, /task, /status, /sim và /help tiếp tục hoạt động. DEBUG=false không tự in
monitor cho lượt ngôn ngữ, nhưng vẫn trả lời đầy đủ về thứ tự dự kiến.

## 8. Kiểm chứng

15 test mới bao phủ task HIGH ở cuối, cùng priority ổn định, mixed priorities,
strict order đã xác thực, cả hai lựa chọn conflict đăng ký đúng một lần, không
hỏi thêm confirmation, invalid answer, repeated read-only preview, live CLI data,
ResponsePlan facts, NLG overclaim, ordinary False flag, single task và missing records.

Đã chạy thành công:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall office_robot_nlp tests
python batch_conversation_demo.py
```

**262/262 test pass**, gồm toàn bộ 247 test trước và 15 test mới. Compileall thành công.
Demo fake parser tạo 4 batch/9 task READY, kiểm chứng HIGH-last, atomic unknown-room
clarification và hai policy choices. Hai batch conflict chỉ được tạo sau lựa chọn,
không có câu hỏi xác nhận lần hai. Không gửi request provider thật.

## 9. Thử Gemini thủ công sau này

Git Bash, từ repository root, với GEMINI_API_KEY đã có trong môi trường:

```bash
export CLU_PROVIDER=gemini
export GEMINI_MODEL=gemini-3.1-flash-lite
export DEBUG_CONVERSATION=true
export NLG_ENABLED=false
python text_conversation.py
```

Nhập ca HIGH-last trong prompt rồi `/batch batch_001` (dùng batch ID thực tế).
Tiếp tục nhập câu conflict báo cáo → phòng 5 rồi hợp đồng → phòng 2 nhưng ưu tiên
hợp đồng; trả lời “theo mức ưu tiên”. Lặp lại câu conflict và trả lời “giữ đúng
thứ tự”. Kiểm tra từng policy, sequenceIndex gốc, preview, READY/NOT_SENT/UNCONFIRMED.

Để xác minh phản hồi thường, chạy phiên mới với `export DEBUG_CONVERSATION=false`.
Các lệnh `/batch` vẫn cho phép xem monitor theo yêu cầu.

## 10. Giới hạn và phạm vi

Preview chỉ áp dụng policy semantic đã hợp lệ, không dự đoán trạng thái thực tế,
đảm bảo sức chứa, tối ưu tuyến hoặc quyết định dispatch. Chất lượng Gemini hiểu
ngôn ngữ vẫn cần kiểm tra live thủ công; fake tests không chứng minh model hiểu
mọi cách diễn đạt. Thông tin thiếu/mơ hồ vẫn làm rõ theo cơ chế cũ.

Không thêm Task Scheduler, HTTP API, PyQt5, RobotGateway, ROS2, navigation hay robot
execution logic. Dừng sau cải tiến Phase 8B.2 Option A này.
