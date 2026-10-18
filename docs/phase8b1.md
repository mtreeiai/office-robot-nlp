# Phase 8B.1 — Robot State Monitoring & Task Intake Debugging

Đã bổ sung inspector cục bộ cho phiên hội thoại. Việc NLP ghi nhận nhiệm vụ,
automation chấp nhận nhiệm vụ, xác nhận nạp tài liệu và trạng thái robot là các
thông tin riêng biệt. Không có scheduler, navigation hoặc transport trong phase này.

## 1. File tạo mới

- `office_robot_nlp/runtime_monitor.py`: snapshot robot, record nhiệm vụ, sự kiện
  monitoring có nguồn và bộ render terminal xác định.
- `tests/test_phase8b1.py`: 28 test không gọi provider thật.
- `docs/phase8b1.md`: báo cáo triển khai, snapshot thực sinh từ model và cách thử.

## 2. File sửa

- `text_conversation.py`: mở rộng lưu nhiệm vụ trong session, xử lý lệnh slash,
  snapshot debug, simulation opt-in và giữ an toàn khi provider lỗi.

Không sửa dữ liệu phòng, resolver, DialogueManager hoặc bảng chuyển lifecycle.

## 3. RobotState model

`RobotStateSnapshot` là dataclass frozen với `robotId`, `connectionState`,
`operatingState`, `locationId`, `currentTaskId`, `carryingDocumentIds`,
`lastUpdatedAt`, `source`.

Mặc định: robot ID chưa biết, `NOT_CONNECTED`, `UNKNOWN`, location/task ID là
`None`, carrying là `None`, timestamp là `None`, nguồn `NONE`.
`carryingDocumentIds=None` được in UNKNOWN; tuple rỗng chỉ được hiểu là trạng
thái đã xác nhận không mang tài liệu. Không giả định robot ở Lobby.

Nguồn có enum `NONE`, `AUTOMATION`, `SIMULATED`. Phương thức
`update_robot_state(snapshot)` yêu cầu nguồn sự kiện tường minh; snapshot mô
phỏng còn yêu cầu bật simulation. TaskReady và sự kiện nạp/queue/accept của task
không gọi phương thức này, không tự thay đổi vị trí, kết nối hay carrying của robot.

## 4. Task monitoring model

`TaskMonitoringRecord` giữ chính `DeliveryTaskLifecycle` hiện có, cùng `handoff`,
`documentLoad`, nguồn riêng cho từng chiều trạng thái và `lastEvent` có timestamp UTC.

Ứng dụng trước đây chỉ có `active_task`, chưa có registry lưu nhiều task.
Đã mở rộng session bằng một mapping record theo ID; `active_task` trở thành
property nhìn vào chính mapping này, nên không có hai bản lifecycle độc lập.
API gán `active_task` cũ được giữ cho fixture Phase 7C; gán trực tiếp không được
coi là bằng chứng automation. `tasks` trả view chỉ đọc.

TaskReady tạo ID bằng bộ đếm ứng dụng (`task_001`, `task_002`, ...), duy nhất
trong phiên và tránh đè ID có sẵn. ID không do LLM tạo. Các task lưu theo thứ tự
ghi nhận, không sắp theo priority. Phiên kết thúc thì dữ liệu trong bộ nhớ kết thúc;
không bổ sung persistence hoặc scheduling.

Record giữ nguyên ResolvedIntent, destination ID chính thức, tài liệu, người nhận,
priority và constraints. `documentId` vẫn `None` khi chỉ có documentText.
Nhiệm vụ mới bắt đầu ở `READY`, handoff `NOT_SENT`, load `UNCONFIRMED` và không
thay đổi physical snapshot.

## 5. Handoff semantics

Các trạng thái là `NOT_SENT`, `PENDING_ACK`, `ACCEPTED`, `REJECTED`.
`TaskMonitoringEvent` có các loại `HANDOFF_PENDING`, `TASK_ACCEPTED`, `TASK_REJECTED`
để ghi nhận thông tin tường minh. Đây là API trong process, không gửi nhiệm vụ.

`process_monitoring_event(event)` yêu cầu nguồn AUTOMATION hoặc SIMULATED và ID
task hợp lệ. `PENDING_ACK` chưa chứng minh task đã được chấp nhận. ACCEPTED
không tự tạo TASK_QUEUED, không nạp tài liệu và không sửa robot snapshot.
Nguồn AUTOMATION chỉ dành cho bên tích hợp cung cấp sự kiện automation thực;
demo không tự tạo các acknowledgement này.

## 6. Document-load semantics

`DocumentLoadState`: `UNCONFIRMED`, `NOT_LOADED`, `LOADED`.
Chỉ `DOCUMENT_LOADED` hoặc `DOCUMENT_NOT_LOADED` mới sửa chiều trạng thái này.
Nguồn load được giữ riêng, nên sự kiện khác không làm mất thông tin load đến
từ mô phỏng hay automation.

Xác nhận nạp không xác minh danh tính tài liệu, không sinh physical document ID,
không tự điền carryingDocumentIds và không chuyển lifecycle.

## 7. Terminal debug template

Khi `DEBUG_CONVERSATION=true`, mỗi TaskReady in `ROBOT TASK MONITOR`, gồm:

- SEMANTIC TASK: ID task, intent, document text/ID, recipient, destination text/ID,
  priority, constraints.
- MAP: map/frame, door và staging pose từ SemanticRoomRegistry Phase 8A.
- TASK EXECUTION: lifecycle, handoff, load, nguồn mỗi chiều và sự kiện cuối.
- ROBOT STATE: snapshot cuối được cung cấp tường minh.
- INTERPRETATION: semantic validity, phòng đã đăng ký, acceptance kèm nguồn,
  trạng thái delivery và các xác nhận còn thiếu.

`Execution checks` chỉ tổng hợp bằng chứng đã ghi nhận. Thiếu acknowledgement,
load, kết nối hoặc liên kết robot/task được in `NOT_CONFIRMED`. Mô phỏng không
được tính là xác nhận automation. Nếu có đủ bằng chứng, dòng này chỉ nói
`PREREQUISITES_RECORDED`; vẫn cần execution event tường minh, không tự bắt đầu
hoặc kết luận đủ an toàn để điều hướng.

Khi DEBUG tắt, hội thoại thường vẫn trả câu tự nhiên và không tự in monitor.
Các lệnh inspection do người dùng nhập vẫn in dữ liệu theo yêu cầu.
Không đọc/in API key hay toàn bộ cấu hình. Lỗi provider không in raw error body
trong debug vì body có thể chứa credential hoặc thông tin request.

## 8. Các lệnh cục bộ

| Lệnh | Hành vi |
|---|---|
| `/status` | Snapshot robot và task robot đang báo xử lý nếu có trong session; nếu không, task gần nhất; thông báo rõ nếu chưa có task |
| `/tasks` | Task ID, room ID, priority, lifecycle của tất cả task cục bộ theo thứ tự intake |
| `/task <taskId>` | Snapshot chi tiết task được chỉ định, hình học, handoff và load |
| `/help` | Danh sách lệnh và trạng thái bật/tắt simulation |

Tất cả chuỗi bắt đầu `/`, kể cả lệnh sai hoặc thiếu tham số, đều được xử lý trước
NLU. Không gọi Gemini NLU hoặc NLG, không sửa conversation state. `LocalCommandResult`
không có ResponsePlan (`None`) vì không đi qua pipeline phản hồi ngôn ngữ.
Task ID chưa đăng ký được thông báo `Unknown task ID: ...`.

## 9. Simulation opt-in

Mặc định tắt. Bật bằng `--enable-simulation`, hoặc `simulation_enabled=True`
khi tự tạo ứng dụng trong test.

```text
/sim document-loaded <taskId>
/sim task-queued <taskId>
/sim task-accepted <taskId>
```

Mỗi lệnh tạo event ghi nguồn SIMULATED, in rõ nhãn này và snapshot sau thay đổi.
`task-accepted` là tiện ích nhỏ để quan sát acknowledgement độc lập; không tự queue.
Chỉ task chỉ định được sửa. Queue dùng đúng `apply_execution_event()` hiện có;
READY → QUEUED hợp lệ, QUEUED → QUEUED bị từ chối mà không sửa record.

`process_execution_event()` cũ cũng cập nhật cùng record, có thể nhận `task_id`
tường minh. Mặc định API này vẫn là đầu vào automation như trước; nếu caller
chọn nguồn SIMULATED thì phải bật simulation và response được thêm nhãn SIMULATED.
Không có lệnh mô phỏng navigation, hoàn tất giao hay cập nhật sensor ngầm.

## 10. Snapshot ví dụ: Phòng 2 thật

Block dưới được sinh bằng `create_application()` + fake parser với một yêu cầu
giao gấp cho anh Phong tới phòng 2, sau đó gọi `task_snapshot()`. Đây là dữ liệu
từ model và registry thực của ứng dụng, không có request Gemini hoặc robot.

```text
==================================================
             ROBOT TASK MONITOR
==================================================

[SEMANTIC TASK]
Task ID          : task_001
Intent           : DELIVER_DOCUMENT
Document Text    : tài liệu này
Document ID      : UNKNOWN
Recipient Text   : anh Phong
Destination Text : phòng 2
Destination ID   : 02
Priority         : HIGH
If Busy          : WAIT
If Closed        : RETRY

[MAP]
Map ID           : office_amcl
Frame            : map
Door             : main_door
Staging X        : 23.900
Staging Y        : -4.150
Staging Yaw      : 1.347

[TASK EXECUTION]
Lifecycle        : READY
Lifecycle Source : NONE
Handoff          : NOT_SENT
Handoff Source   : NONE
Document Loaded  : UNCONFIRMED
Load Source      : NONE
Last Event       : NONE

[ROBOT STATE]
Robot ID         : UNKNOWN
Connection       : NOT_CONNECTED
Operating State  : UNKNOWN
Location         : UNKNOWN
Current Task     : NONE
Carrying Docs    : UNKNOWN
Last Updated     : UNKNOWN
State Source     : NONE

[INTERPRETATION]
Semantic request : VALID
Room registered  : YES
Robot accepted   : NO (NOT_SENT; NONE)
Physical delivery: NOT_STARTED (NONE)
Execution checks : NOT_CONFIRMED; missing: automation acceptance, document load, robot connection, robot task association, robot operating state

==================================================
```

## 11. Test mới

28 test trong `tests/test_phase8b1.py` bao phủ:

- TaskReady đăng ký record và giữ nguyên semantics, ID 02, HIGH, recipient,
  constraints; không sinh document ID hay thay đổi robot.
- Pose phòng 2 và snapshot thay đổi đúng với phòng 7/người nhận/priority khác.
- Nhiều task không ghi đè nhau, không xếp priority và inspector chọn đúng task.
- Slash commands không gọi NLU/NLG, kể cả lệnh lỗi, unknown ID và session rỗng.
- Simulation opt-in tại cả CLI và API, source/timestamp, load/accept/queue độc lập,
  không sửa task khác và không có success ngầm.
- Chuyển lifecycle sai bị từ chối nguyên tử; API execution và inspector dùng
  cùng một record.
- Sự kiện handoff và load tường minh, provenance từng chiều, snapshot robot bất
  biến và khác biệt giữa carrying UNKNOWN với confirmed empty.
- Lỗi provider 429/503 giữ nguyên task, counter, conversation, lifecycle và robot.
- Unknown room không tạo task, SOCIAL/TASK_UPDATE còn hoạt động.
- Bật/tắt terminal debug, bảo vệ credential trong lỗi và pass cờ simulation CLI.

## 12. Kết quả kiểm chứng

**195/195 test pass**, gồm 167 test có sẵn và 28 test mới.
`compileall` thành công. Đã chạy đúng hai lệnh yêu cầu:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall office_robot_nlp tests
```

Test hồi quy có thể in `NETWORK_ERROR: offline` từ fake provider; đó là output
kiểm thử lỗi có chủ đích, không phải request mạng thật.

## 13. Chạy demo và thử thủ công

Trong Git Bash, từ repository root, với `GEMINI_API_KEY` đã cấu hình sẵn:

```bash
export CLU_PROVIDER=gemini
export GEMINI_MODEL=gemini-3.1-flash-lite
export DEBUG_CONVERSATION=true
export NLG_ENABLED=false
python text_conversation.py
```

PowerShell tương đương:

```powershell
$env:CLU_PROVIDER = 'gemini'
$env:GEMINI_MODEL = 'gemini-3.1-flash-lite'
$env:DEBUG_CONVERSATION = 'true'
$env:NLG_ENABLED = 'false'
python text_conversation.py
```

Để bật mô phỏng, đổi lệnh cuối thành:

```bash
python text_conversation.py --enable-simulation
```

Nhập:

```text
Giao gấp tài liệu này cho anh Phong ở phòng 2.
/status
/tasks
/task task_001
```

Dùng ID task thực tế in ra. Trước các sự kiện, kiểm tra ID phòng 02, pose
(23.900, -4.150, 1.347), lifecycle READY, NOT_SENT, UNCONFIRMED,
robot NOT_CONNECTED/UNKNOWN, location UNKNOWN, carrying UNKNOWN.

Nếu đã bật simulation:

```text
/sim document-loaded task_001
/sim task-accepted task_001
/sim task-queued task_001
/status
```

Kiểm tra load LOADED, handoff ACCEPTED và lifecycle QUEUED với từng nguồn
SIMULATED, robot snapshot vẫn chưa kết nối/chưa biết. Nhập lại lệnh queued để
xác nhận báo transition sai và giữ nguyên trạng thái. Không có điều hướng.

## 14. Provider requests

Không gửi bất kỳ request thật nào tới Gemini, OpenAI hoặc Ollama trong quá trình
triển khai, kiểm thử hoặc tạo snapshot tài liệu. Thử khả năng trích xuất câu tiếng
Việt của Gemini thật vẫn là bước thủ công của người vận hành.

## 15. Sai khác và giới hạn

Không có sai khác bắt buộc so với đặc tả. Đã triển khai phần simulation tùy chọn,
thêm lệnh `task-accepted` để kiểm tra handoff riêng. Task và robot monitoring
chỉ ở bộ nhớ session; nguồn sự kiện là hợp đồng trong process cho tích hợp sau,
chưa có kết nối robot, API/ROS transport hoặc persistence.

Đã dừng ở Phase 8B.1.
