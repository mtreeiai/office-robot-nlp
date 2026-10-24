# Phase 8B.2 — Multi-document DeliveryBatch

Bản bổ sung hiện hành: [Order Preview & Conversation Feedback — Option A](phase8b2_order_preview.md).
Demo đã được mở rộng thành 4 batch/9 task, gồm cả PRIORITY và STRICT_ORDER;
các số liệu và bảng demo bên dưới ghi lại phiên bản Phase 8B.2 ban đầu.

Đã triển khai hiểu ngôn ngữ, xác thực nguyên tử, làm rõ nhiều lượt, đăng ký và
theo dõi lô giao tài liệu. Giữ nguyên luồng nhiệm vụ đơn. Không triển khai scheduler,
dispatch, HTTP, GUI, ROS2 hoặc điều khiển robot.

## 1. Thay đổi kiến trúc

```text
LanguageUnderstandingParser
  ├─ TASK_COMMAND → DialogueManager → TaskReady → monitor nhiệm vụ đơn hiện có
  └─ BATCH_COMMAND → DialogueManager → validate_batch
       ├─ thiếu/sai/mơ hồ → PendingBatch → làm rõ đúng mục
       └─ hợp lệ toàn bộ → BatchReady → đăng ký nguyên tử → DeliveryBatch
                                                 └─ các TaskMonitoringRecord độc lập
```

Không bổ sung một bộ hiểu ngôn ngữ bằng regex. EntityResolver và validate_intent
hiện có vẫn kiểm tra từng ứng viên. Guard cũ chống cắt danh sách phòng thành một
task đơn vẫn giữ, nhưng không chặn BATCH_COMMAND hợp lệ.

## 2. File tạo mới

- `office_robot_nlp/domain/batch.py`: OrderPolicy, AllocationStatus, BatchDraft,
  PendingBatch, BatchIssue, BatchTaskReference, DeliveryBatch.
- `office_robot_nlp/batch_validation.py`: xác thực mọi mục và tạo câu hỏi xác định.
- `office_robot_nlp/batch_schema.py`: các field batch dùng chung cho adapter schema.
- `tests/test_batch_contract.py`: 11 test domain, decoder và fake provider transport.
- `tests/test_phase8b2.py`: 31 test tích hợp, rollback, clarification, monitor và NLG.
- `batch_conversation_demo.py`: demo CLI xác định, chỉ dùng fake parser.
- `docs/phase8b2.md`: báo cáo và hướng dẫn này.

## 3. File sửa

- `office_robot_nlp/clu.py`: contract, decoder, hướng dẫn batch và semantic context.
- `office_robot_nlp/domain/conversation.py`: thêm pendingBatch và mode chờ batch.
- `office_robot_nlp/dialogue_manager.py`: BatchReady, clarification từng mục,
  phân bổ/thứ tự mơ hồ, cancellation và tái sử dụng merge TASK_UPDATE.
- `office_robot_nlp/gemini_adapter.py`: batch schema và context mục đang chờ.
- `office_robot_nlp/openai_adapter.py`: đồng bộ contract để giữ adapter tương thích.
- `office_robot_nlp/ollama_adapter.py`: đồng bộ contract và context tương tự.
- `office_robot_nlp/response_plan.py`: plan và template ghi nhận/làm rõ batch.
- `office_robot_nlp/runtime_monitor.py`: liên kết batch/sequence và render batch.
- `text_conversation.py`: đăng ký nguyên tử, lưu batch, lệnh inspection, debug.
- `tests/test_gemini_adapter.py`, `tests/test_openai_adapter.py`: cập nhật assertion
  danh sách CLU chính xác để bao gồm các loại mới; giữ các assertion cũ khác.

Không sửa 7 phòng thật, alias, geometry, resolver hoặc bảng chuyển lifecycle.

## 4. DeliveryBatch domain

DeliveryBatch frozen chứa `batchId`, `sessionId`, tuple các BatchTaskReference và
`orderPolicy`. Mỗi reference chứa `taskId` và `sequenceIndex` bắt đầu từ 0.
Thông tin tài liệu, người nhận, đích, priority và constraints nằm trong ResolvedIntent
của TaskMonitoringRecord hiện có. Lifecycle chỉ nằm trong DeliveryTaskLifecycle
của record này; batch không lưu một bản lifecycle khác có thể bị lỗi thời.

TaskMonitoringRecord có thêm `batchId` và `sequenceIndex` tùy chọn. Nhiệm vụ đơn
vẫn dùng None cho cả hai field. Monitor batch lấy trạng thái mới nhất theo task ID.

Batch phải có ít nhất một reference, ID task không trùng và sequence liên tục
đúng thứ tự đầu vào. Bộ đếm ứng dụng tạo ID task/batch, tránh đè ID đã có và không
dùng LLM. Các ID duy nhất trong runtime session hiện tại; dữ liệu vẫn chỉ ở bộ nhớ.
Session ID dùng conversationId hiện có (`text-demo` trong console).

Không đặt giới hạn số task, số ngăn hay suy đoán sức chứa. documentId luôn None
trong intake này. Không cấp compartment ID hoặc xác minh danh tính tài liệu.

## 5. Tương thích nhiệm vụ đơn

TASK_COMMAND vẫn trả TaskReady với ResolvedIntent như trước; caller cũ không
phải chuyển sang BatchReady. Lifecycle, RobotStateSnapshot, handoff, load,
SOCIAL, TASK_UPDATE, ROBOT_DECISION và các lệnh cũ tiếp tục hoạt động.

Domain batch cho phép một hoặc nhiều thành viên để có thể dùng trong API thống
nhất sau này. Phase này không tạo HTTP API và không tự bọc mọi TaskReady thành batch.

## 6. Gemini structured output

Tiếp tục dùng discriminator `type` của project, tương ứng khái niệm `category`
trong ví dụ đặc tả. Thêm các loại:

- BATCH_COMMAND: các ứng viên tài liệu và đích.
- BATCH_ORDER: lựa chọn tường minh sau câu hỏi xung đột thứ tự/priority.
- BATCH_CONFIRM: thử đăng ký lại sau lỗi nội bộ, chỉ trong ngữ cảnh đang chờ.
- CANCEL_PENDING: hủy yêu cầu/bản nháp đang chờ, không hủy task đang thực thi.

Ví dụ candidate:

```json
{
  "type": "BATCH_COMMAND",
  "tasks": [
    {
      "documentText": "hợp đồng",
      "recipientText": "anh Phong",
      "destinationText": "phòng 2",
      "priority": "HIGH",
      "constraints": {"ifBusy": null, "ifClosed": null}
    },
    {
      "documentText": "báo cáo",
      "recipientText": "chị Lan",
      "destinationText": "phòng 5",
      "priority": "NORMAL",
      "constraints": {"ifBusy": null, "ifClosed": null}
    }
  ],
  "orderPolicy": "PRIORITY",
  "allocationStatus": "CLEAR"
}
```

Schema yêu cầu các field semantic đầy đủ cho mỗi item, có minItems=1 và không
có maxItems. Decoder cũng chấp nhận dạng candidate rút gọn: priority thiếu thành
NORMAL, recipient thiếu thành None và constraints thiếu thành không có policy.
Không nhận taskId, documentId, destinationId, roomId, tọa độ hay compartmentId.

allocationStatus là CLEAR hoặc AMBIGUOUS, diễn tả độ rõ của phân bổ trong ngôn ngữ,
không phải xác nhận sensor. Thiếu field này trong candidate rút gọn có documentText
trùng nhau sẽ mặc định AMBIGUOUS để không tự tạo nhiều bản. Prompt yêu cầu dùng
AMBIGUOUS khi chưa rõ tài liệu/bản nào tương ứng phòng nào, kể cả các cách diễn đạt
khác không thể phát hiện chỉ bằng so sánh text.

CLARIFICATION_ANSWER và TASK_UPDATE hỗ trợ itemIndex tùy chọn, bắt đầu từ 0.
Nếu nêu tường minh thì phải đúng mục đang chờ. Chỉ số sai bị từ chối; bỏ chỉ số
thì câu trả lời ngắn chỉ điền mục đang được hỏi. Provider nhận awaitingItemIndex,
field chờ và các semantic task đang giữ; không nhận geometry hoặc robot IDs.

OpenAI và Ollama dùng cùng decoder và contract, đã kiểm tra bằng fake transport.

## 7. Xác thực và đăng ký nguyên tử

Mọi item đều qua resolve_destination + validate_intent. Không dùng fuzzy matching,
không sinh room ID. ID 02, 05, 07 là các chuỗi chính thức lấy từ catalog thật.
Document text thiếu, phòng thiếu/chưa đăng ký/đa nghĩa và phân bổ mơ hồ được giữ
ở PendingBatch. Không đăng ký riêng các item hợp lệ của một yêu cầu chưa hoàn chỉnh.

Trước đăng ký, ứng dụng kiểm tra lại toàn bộ draft và so khớp kết quả với BatchReady.
Sau đó tạo đầy đủ batch references, lifecycle và monitoring records ở vùng chuẩn bị.
Chỉ khi tất cả thành công mới công bố cả hai mapping task/batch cùng các counter.
Lỗi khi tạo record thứ hai hoặc ID trùng/colliding không để lại task mồ côi.

Nếu bước đăng ký lỗi, các task cũ và counter được giữ nguyên; draft hợp lệ được
giữ để người dùng xác nhận thử lại bằng BATCH_CONFIRM hoặc hủy. Không in nội dung
lỗi nội bộ/provider ra câu trả lời. Đây là commit trong ứng dụng console tuần tự,
không phải transaction database hoặc cơ chế phân tán.

## 8. Làm rõ nhiều lượt

Ví dụ hợp đồng → phòng 2, báo cáo → phòng 8:

1. Draft giữ cả hai item, kết quả phân giải hợp lệ của hợp đồng và thông tin còn lại.
2. Hỏi đích khác cho mục báo cáo; chưa đăng ký task nào của draft.
3. `Phòng 5` chỉ sửa destinationText mục báo cáo.
4. Xác thực lại toàn bộ; sau đó đăng ký hai task cùng batch.

Mỗi lần chỉ hỏi một lỗi/item. Nếu hai đích đều sai, trả lời cho đích đầu không
được áp dụng cho đích thứ hai. Recipient, documentText, priority và constraints
khác được giữ. SOCIAL, UNKNOWN và câu trả lời sai lặp lại không làm mất draft.
TASK_UPDATE chỉ sửa item đang chờ; chỉ số item khác bị chặn.

Phân bổ mơ hồ như `Giao tài liệu đến phòng 2 và phòng 5` cần người dùng nói rõ
từng tài liệu hoặc từng bản cùng đích. Không chấp nhận một câu trả lời ngắn để
đoán tài liệu/bản cho nhiều item. Câu restatement phải giữ số item và thứ tự đích
đang chờ; chỉ điền lại phân bổ tài liệu, giữ priority, constraints và recipient
đã có. Muốn đổi cấu trúc/số item thì hủy draft và nêu yêu cầu mới.

Cancellation tường minh chỉ xóa draft, không xóa task đã đăng ký trước đó.
Timeout, 429, 503 giữ nguyên draft, batch/task trước đó và robot snapshot.

## 9. Priority và thứ tự

Mỗi task có Priority riêng. Không có yêu cầu gấp thì NORMAL; yêu cầu ưu tiên hợp
đồng chỉ đánh dấu hợp đồng HIGH. sequenceIndex luôn giữ thứ tự nói ban đầu,
kể cả một task HIGH xuất hiện sau task NORMAL.

OrderPolicy mặc định PRIORITY. Chỉ yêu cầu thứ tự cố định tường minh mới dùng
STRICT_ORDER. Nếu STRICT_ORDER đưa một task priority thấp hơn trước task priority
cao hơn, ứng dụng hỏi người dùng chọn giữ thứ tự hay theo priority.
BATCH_ORDER là câu trả lời xác nhận lựa chọn; không tự đổi thứ tự hoặc priority.
Không sắp lịch thực thi, tối ưu lộ trình hoặc tính khoảng cách.

## 10. Monitor và phản hồi tự nhiên

DELIVERY BATCH MONITOR in batch/session ID, số task, policy và từng thành viên với
document/recipient/destination, official room ID, priority, sequence, lifecycle,
handoff, load và từng nguồn xác nhận. Tất cả đọc từ model runtime.
Khi một task nhận sự kiện SIMULATED riêng, batch view lấy trạng thái record mới
nhất và vẫn in các nguồn đó; sibling task không bị sửa.

Sau đăng ký mới, mọi task đều READY / NOT_SENT / UNCONFIRMED và các nguồn NONE.
Robot snapshot không đổi. Acceptance, load và execution được tổng hợp riêng,
không suy ra sức chứa hoặc acceptance chỉ vì batch hợp lệ.

Template tiếng Việt liệt kê đầy đủ các cặp tài liệu/đích/người nhận và ghi nhận
HIGH/STRICT_ORDER nếu có. Các plan batch mới dùng template xác định kể cả khi
NLG_ENABLED=true, nhằm giữ nguyên mọi association và câu hỏi làm rõ. Gemini NLG
tùy chọn của luồng đơn vẫn giữ nguyên. Không tuyên bố đã nạp, nhận task, đủ sức
mang tài liệu, đang di chuyển hay giao xong khi chỉ ghi nhận semantics.

## 11. Lệnh terminal và demo

Thêm `/batches` và `/batch <batchId>`. Lệnh đầu liệt kê ID, số task, VALID;
lệnh sau in đủ thành viên và trạng thái xác nhận. Unknown ID/thiếu tham số báo
rõ mà không gọi NLU/NLG. `/status`, `/tasks`, `/task`, `/help` và `/sim` cũ giữ nguyên.
DEBUG_CONVERSATION=true tự in batch monitor khi đăng ký thành công; khi false
chỉ trả lời tự nhiên cho lượt ngôn ngữ.

Demo xác định, không cần key và không dựng bất kỳ provider nào:

```bash
python batch_conversation_demo.py
```

Đã chạy thành công. Tóm tắt dữ liệu thực do demo tạo:

| Batch | Task | sequenceIndex | Document | Room ID | Priority | Lifecycle |
|---|---|---:|---|---|---|---|
| batch_001 | task_001 | 0 | hợp đồng | 02 | HIGH | READY |
| batch_001 | task_002 | 1 | báo cáo | 05 | NORMAL | READY |
| batch_001 | task_003 | 2 | hồ sơ | 07 | NORMAL | READY |
| batch_002 | task_004 | 0 | biên bản | 02 | NORMAL | READY |
| batch_002 | task_005 | 1 | chứng từ | 05 | NORMAL | READY |

Batch thứ hai ban đầu có chứng từ → phòng 8. Lúc đang hỏi đích khác, `/tasks`
vẫn chỉ có task_001–003. Sau `Phòng 5`, task_004–005 mới được đăng ký cùng nhau.
Toàn bộ handoff là NOT_SENT, load UNCONFIRMED, robot NOT_CONNECTED/UNKNOWN.

Thử Gemini thủ công sau này, với GEMINI_API_KEY đã có trong môi trường:

```bash
export CLU_PROVIDER=gemini
export GEMINI_MODEL=gemini-3.1-flash-lite
export DEBUG_CONVERSATION=true
export NLG_ENABLED=false
python text_conversation.py
```

Nhập ví dụ ba tài liệu trong prompt rồi `/batches`, `/batch batch_001`, `/tasks`.
Dùng ID thực tế được in ra. Thử thêm phòng 8 → trả lời Phòng 5, phân bổ tài liệu
mơ hồ, SOCIAL khi đang chờ, lựa chọn thứ tự và cancellation. Đây là bước thử
NLU thật của người dùng, chưa chạy tự động.

## 12. Test mới

42 test mới: 11 ở test_batch_contract.py, 31 ở test_phase8b2.py. Bao phủ decoder,
schema, fake transport Gemini/OpenAI/Ollama, association 2/3 tài liệu và recipient,
7 phòng thật, batch 12 item không gắn giới hạn sức chứa, priority/thứ tự, pending
draft, item targeting, SOCIAL, TASK_UPDATE, cancellation, rollback và retry.

Có test ID trùng trong domain và trong allocator, va chạm với task cũ, lỗi nội bộ
khi tạo record thứ hai, ID/physical document ID giả, monitor lấy dữ liệu thực,
provenance simulation, guard NLG và lỗi timeout/429/503. Các test đều dùng fake
parser hoặc fake transport, chặn urlopen trong suite tích hợp mới.

## 13. Kết quả kiểm chứng

**237/237 test pass**, gồm 195 test trước và 42 test mới.
Đã chạy thành công đúng các lệnh yêu cầu:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall office_robot_nlp tests
```

Đã chạy CLI fake parser và kiểm tra atomic draft/registration qua demo.
Không gửi request thật tới Gemini, OpenAI hoặc Ollama, không tiêu thụ quota.

## 14. Giới hạn ngôn ngữ đã biết

- Phân bổ tài liệu/copies không rõ phải làm rõ, không đoán hoặc tạo bản sao.
- Restatement phân bổ hiện giữ nguyên số mục và thứ tự đích. Chỉnh toàn cục,
  tách/gộp task hoặc đổi mục khác khi đang hỏi một mục cần hủy rồi nêu lại.
- Chỉ hỗ trợ giao tài liệu. Giao rồi mang về, workflow lấy/trả, điều kiện thực thi
  phức tạp hoặc tác vụ khác phải UNKNOWN/làm rõ; không bỏ phần không hỗ trợ.
- Nhận biết câu nói thành các candidate/association vẫn là trách nhiệm NLU.
  Prompt đã cập nhật, nhưng độ chính xác với Gemini thật chưa được live-evaluate.
  Bộ test fake xác minh contract và hành vi xác định, không chứng minh model luôn
  hiểu đúng mọi cách diễn đạt. Không thêm bộ regex extraction thứ hai để thay NLU.

## 15. Giả định và ranh giới với robot team

Không cần thông tin bổ sung từ robot team để hoàn tất Phase 8B.2. Tiếp tục dùng
7 room ID và frame/map metadata đã xác nhận ở Phase 8A. Không yêu cầu xác nhận lại.

Sức chứa, số ngăn, gán document identity, trạng thái nạp vật lý, dispatch/ack transport
và chính sách scheduler sẽ do các phase tích hợp sau quyết định. Phase hiện tại
không suy đoán các giá trị này và không triển khai HTTP, PyQt5, RobotGateway,
ROS2, navigation, PDDL, occupancy, retry/defer/return execution hoặc camera.

Dừng ở Phase 8B.2.
