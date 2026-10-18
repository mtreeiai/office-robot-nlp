# Phase 8A — Tích hợp semantic map thật

Demo mặc định dùng 7 phòng đã đăng ký, ID chuỗi `"01"`–`"07"`.
`TaskReady` chỉ tạo lifecycle `READY`. Phase này không gửi lệnh điều hướng,
không chạy scheduler, ROS2, PDDL, camera hay cảm biến.

## Dữ liệu và phân giải

- `data/office_rooms.json`: toàn bộ JSON nguồn của automation, giữ nguyên cả
  cách viết số thập phân, `display_name`, `description`, `room_anchor`, `doors`,
  `p1`, `p2`, `zone_depth`, `staging_pose`, `yaw`, `yaw_deg`.
- `data/office_room_aliases.json`: cấu hình NLP riêng, nối với nguồn bằng
  `room_id` chuỗi. Mỗi phòng có `spoken_name` là `Phòng N` và các alias
  `phòng N`, `phong N`, `phongN`.
- `office_robot_nlp/semantic_map.py`: đọc hai file, xác thực rồi tạo
  `MapCatalog`/`MapLocation` hiện có; hình học nằm trong lookup riêng.

EntityResolver hiện có vẫn so khớp chính xác sau chuẩn hóa dấu, chữ hoa/thường,
khoảng trắng và dấu câu ở biên. Ví dụ `Phòng 3`, `phong 3`, `phong3` → `"03"`.
Không có fuzzy matching, chọn số gần nhất hay ID do Gemini tạo.
`Phòng 8`, `Phòng A`, `Phòng B`, `Phòng 10` không phân giải được.
`phong giai lao` và `an` chỉ được giữ trong `source_name`, không tự trở thành alias.

Loader từ chối ID trùng, ID dạng số, alias sau chuẩn hóa trỏ nhiều phòng,
tham chiếu không tồn tại, thiếu bản ghi alias, cấu trúc sai, tọa độ không hữu hạn,
pose thiếu/sai, `zone_depth <= 0`, door ID trùng và yaw không nhất quán.
Sai lệch yaw cho phép tối đa 0.1 độ, xét tương đương một vòng quay, phù hợp
độ làm tròn của nguồn. Chỉ kiểm tra, không sửa giá trị nguồn.
Khóa JSON trùng cũng bị từ chối.

Metadata đã xác nhận: `map_id="office_amcl"`, `frame_id="map"`, map liên quan
`office_amcl.pgm`. File ảnh map không cần thiết để nạp registry và không được
bổ sung hay dựng lại trong Phase 8A.

Một số đoạn cửa, đặc biệt phòng 4, 6, 7, có vẻ dài bất thường so với cửa thông
thường. Dữ liệu được giữ nguyên; không tự sửa đầu mút hay tính lại staging pose.
`room_anchor` chỉ là điểm tham chiếu, đích giao tài liệu là `staging_pose`.

## Nạp registry và ranh giới automation

```python
from office_robot_nlp.semantic_map import load_semantic_registry

registry = load_semantic_registry()  # Chỉ đọc file cục bộ, không gọi provider.
catalog = registry.map_catalog
room = registry.get_room("03")
pose = registry.get_staging_pose("03", "main_door")
assert (pose.x, pose.y, pose.yaw, pose.frame_id) == (13.600, 0.650, -2.570, "map")
```

Room và pose chỉ đọc, doors/aliases dùng tuple; mapping phòng không cho sửa.
Lookup sai ID hoặc door báo `KeyError`, không suy đoán hay chuyển số `3` thành
`"03"`. Mỗi lần lấy catalog trả một bản riêng theo domain model hiện có.

NLP/HRI chịu trách nhiệm tạo `ResolvedIntent.destination.locationId` đã xác thực.
Đây chính là `destinationId` trong debug/contract khái niệm. Automation tương lai
sẽ dùng cùng ID để lấy staging pose, thực hiện điều hướng và gửi execution events.
Không bổ sung HTTP/ROS transport hoặc tọa độ vào prompt hay DialogueManager.

Registry là metadata tĩnh. Availability, occupancy, trạng thái cửa và người nhận
đều chưa có tín hiệu, tương đương UNKNOWN; registry không tạo trạng thái
AVAILABLE/BUSY/DOOR_OPEN. Việc 7 staging pose từng được thử thành công không
chứng minh tình trạng hiện tại của phòng.

## Runtime và hội thoại

`create_application()` nạp registry trước khi khởi tạo provider. Đường dẫn mặc
định và đường dẫn tương đối trong biến môi trường đều được tính từ repository
root, nên không phụ thuộc thư mục làm việc. File sai/thiếu gây `Setup error: ...`
rõ tên file; không fallback về phòng giả.

Demo chính không import hoặc đăng ký `meeting_room_A/B`. Hàm
`office_robot_nlp/demo_catalog.py` vẫn còn cho các fixture hồi quy cô lập.
Người viết test có thể truyền `map_catalog` rõ ràng; không thể đồng thời truyền
catalog đó và các đường dẫn registry tường minh.

Phòng chưa đăng ký tạo `UNKNOWN_LOCATION` trong ResponsePlan, không tạo
`TaskReady` hoặc đăng ký execution task. Pending task giữ nguyên tài liệu,
người nhận, priority và constraints để người dùng bổ sung đích hợp lệ.
Câu trả lời nói phòng chưa có trong danh sách hiện tại, không khẳng định phòng
không tồn tại trong tòa nhà hoặc navigation đã thất bại.

`Giao tài liệu này.` rồi `Phòng 5.` tạo `TaskReady` với ID `"05"`, state `READY`.
Đã sửa lỗi cũ làm mất `recipientText` khi `dataclasses.replace()` xử lý câu trả
lời làm rõ: field này hiện là compatibility property nên phải truyền lại tường minh.
`SOCIAL`, `TASK_UPDATE`, priority HIGH và constraints vẫn hoạt động.

CLU được hướng dẫn trả UNKNOWN cho nhiều đích, không cắt lấy phòng đầu.
Demo còn chặn dạng liệt kê số phòng tường minh như `phòng 1 và phòng 2`
ngay cả nếu parser chỉ trả về phòng 1. Yêu cầu này đi qua nhánh UNKNOWN hiện có;
pending task được giữ nếu đang làm rõ. Các diễn đạt phức hợp khác vẫn phụ thuộc
CLU phân loại đúng; không xây bộ hiểu ngôn ngữ thứ hai trong phase này.

## Chạy demo

Trong PowerShell, từ repository root, với Python trong PATH:

```powershell
$env:CLU_PROVIDER = 'gemini'
$env:GEMINI_MODEL = 'gemini-3.1-flash-lite'
$env:NLG_ENABLED = 'false'
$env:DEBUG_CONVERSATION = 'true'
# GEMINI_API_KEY cần được thiết lập sẵn trong môi trường.
python text_conversation.py
```

Máy hiện tại có Python launcher nhưng lệnh `python` chưa có trong PATH:

```powershell
py -3 text_conversation.py
```

Trong Git Bash:

```bash
export CLU_PROVIDER=gemini
export GEMINI_MODEL=gemini-3.1-flash-lite
export NLG_ENABLED=false
export DEBUG_CONVERSATION=true
python text_conversation.py
```

Nếu shell chỉ có launcher Windows, thay `python` bằng `py -3`.
Không tự động đọc `.env`; `.env.example` là cấu hình mẫu để thiết lập môi trường.

| Biến | Bắt buộc / mặc định |
|---|---|
| `GEMINI_API_KEY` | Bắt buộc khi chạy hội thoại Gemini thật |
| `CLU_PROVIDER` | Mặc định `gemini`; cũng có thể truyền `--provider gemini` |
| `GEMINI_MODEL` | Mặc định hiện có `gemini-3.1-flash-lite` |
| `NLG_ENABLED` | Mặc định `false`, dùng template để tiết kiệm quota |
| `DEBUG_CONVERSATION` | Đặt `true` để xem semantics và lifecycle |
| `ROOM_REGISTRY_PATH` | Tùy chọn, mặc định `data/office_rooms.json` |
| `ROOM_ALIASES_PATH` | Tùy chọn, mặc định `data/office_room_aliases.json` |

Chỉ kiểm tra/liệt kê registry, không cần API key, không dựng provider kể cả khi
`NLG_ENABLED=true`:

```powershell
py -3 text_conversation.py --list-rooms
```

## Checklist kiểm thử Gemini thủ công

Chưa chạy các yêu cầu Gemini thật. Sau khi bật debug và tắt Gemini NLG như trên,
thử từng câu sau và xem cả câu trả lời tự nhiên lẫn output debug:

| Câu nhập | Kết quả mong đợi |
|---|---|
| `Giao tài liệu tới phòng 1` | `destinationId: 01`, lifecycle READY |
| `Giao tài liệu tới phòng 3` | `destinationId: 03`, lifecycle READY |
| `Giao tài liệu tới phòng 7` | `destinationId: 07`, lifecycle READY |
| `Giao tài liệu tới phòng 8` | UNKNOWN_LOCATION, hỏi đích khác, không tạo task |
| `Giao tài liệu tới phòng A` | UNKNOWN_LOCATION, không có meeting_room_A |
| `Giao gấp tài liệu cho anh Phong ở phòng 2` | ID 02, HIGH, recipient anh Phong, READY |

Sau một phòng chưa đăng ký, ứng dụng đang giữ pending task. Có thể trả lời
`Phòng 5` để hoàn tất, hoặc nhập `exit` và chạy lại cho ca độc lập tiếp theo.
Thử thêm hai lượt `Giao tài liệu này.` → `Phòng 5.` và yêu cầu
`Giao tài liệu tới phòng 1 và phòng 2.`; câu nhiều đích không được tạo task.
READY chỉ xác nhận ghi nhận nhiệm vụ, không được nói đã bắt đầu, di chuyển hay
hoàn tất giao. Các kiểm thử fake parser xác minh pipeline xác định; không thay
thế kiểm chứng khả năng trích xuất của provider thật.

## Kiểm chứng tự động và danh sách file

Tạo 6 file:

- `data/office_rooms.json`
- `data/office_room_aliases.json`
- `office_robot_nlp/semantic_map.py`
- `tests/test_semantic_map.py`
- `tests/test_phase8a.py`
- `docs/phase8a.md`

Sửa 6 file:

- `text_conversation.py`: runtime thật, lookup registry, bảo vệ nhiều đích,
  `--list-rooms`, debug hiển thị rõ destinationText và destinationId.
- `office_robot_nlp/clu.py`: hướng dẫn phân loại, recipient và nhiều đích.
- `office_robot_nlp/dialogue_manager.py`: giữ recipient qua clarification,
  diễn đạt phòng chưa đăng ký trong danh sách.
- `office_robot_nlp/response_plan.py`: template phòng chưa đăng ký.
- `office_robot_nlp/demo_catalog.py`: ghi rõ chỉ là fixture legacy.
- `.env.example`: các biến cấu hình demo và registry.

37 test mới, tổng **167/167 pass**, gồm toàn bộ 130 test cũ. Các test mới bao
phủ dữ liệu, validation cấu hình, resolver, lifecycle, hội thoại nhiều lượt,
recipient/priority/constraints, SOCIAL/TASK_UPDATE, nhiều đích, runtime,
đường dẫn, startup lỗi, debug và liệt kê không gọi provider.
Đã đối chiếu toàn bộ JSON hình học với block nguồn trong prompt: giống nguyên
văn sau khi bỏ newline ở biên, bao gồm cách viết các số thập phân.

Các lệnh theo yêu cầu:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall office_robot_nlp tests
```

Đã thực thi thành công trên máy này bằng lệnh tương đương:

```powershell
py -3 -m unittest discover -s tests -p "test_*.py" -v
py -3 -m compileall office_robot_nlp tests
```

`py -3 text_conversation.py --list-rooms` cũng chạy thành công, in đúng 7 ID.
Không gửi yêu cầu thật tới Gemini, OpenAI hoặc Ollama. Một test hồi quy có in
`NETWORK_ERROR: offline` từ fake provider; đây là output kiểm thử lỗi chủ động,
không phải kết nối mạng thật.

Không thiếu dữ liệu để hoàn tất Phase 8A. Sai khác duy nhất về lệnh kiểm thử
là dùng launcher `py -3` theo môi trường Windows hiện tại. Chưa commit vì thư
mục được cung cấp không có `.git`; các JSON đã nằm trong source tree để đưa
vào quản lý phiên bản cùng code. Dừng ở Phase 8A.
