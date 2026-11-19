# THIẾT KẾ HỆ THỐNG OFFICE ROOM CALENDAR & NLP AVAILABILITY CHECK

**Dự án:** Office Robot NLP

**Repository:** https://github.com/mtreeiai/office-robot-nlp

**Nhánh phát triển duy nhất:** main

**Phạm vi:** Backend API, Website quản lý lịch, Database và tích hợp kiểm tra lịch vào NLP.

Tớ sẽ hệ thống hóa toàn bộ yêu cầu mà cậu đã thống nhất, từ kiến trúc tổng thể đến thiết kế từng thành phần, bao gồm cả **5 ý tưởng mở rộng mà cậu đã chấp nhận**.

Đây sẽ là bản đặc tả thiết kế để làm cơ sở triển khai sau này. Hiện tại chúng ta đang thống nhất kiến trúc và chức năng, chưa chỉnh sửa mã nguồn.

---

# PHẦN I. TỔNG QUAN HỆ THỐNG

## 1. Mục tiêu của dự án

Cậu muốn nâng cấp Backend NLP hiện tại để robot không chỉ hiểu câu lệnh và xác định phòng đích mà còn có khả năng **kiểm tra lịch sử dụng phòng trước khi thực hiện yêu cầu di chuyển**.

Đồng thời, xây dựng một website để người quản lý chủ động tạo và cập nhật lịch sử dụng các phòng.

Hệ thống phải bảo đảm ba nguyên tắc:

**Nguyên tắc 1 — Website là nơi quản lý lịch**

Người quản lý sử dụng website để:

- Xem lịch của từng phòng.
- Tạo lịch họp hoặc lịch sử dụng phòng.
- Chỉnh sửa thời gian và nội dung lịch.
- Hủy lịch.
- Theo dõi phòng nào đang có lịch và khi nào rảnh.

**Nguyên tắc 2 — Backend là nơi kiểm tra và quyết định**

Backend chịu trách nhiệm:

- Tiếp nhận yêu cầu từ robot.
- Sử dụng Gemini NLP để hiểu câu lệnh.
- Xác định phòng đích.
- Truy vấn cơ sở dữ liệu lịch.
- Kiểm tra khả năng tiếp nhận yêu cầu tại phòng.
- Trả về kết quả có cấu trúc và câu phản hồi tiếng Việt.

**Nguyên tắc 3 — Robot không có quyền quản lý lịch**

Robot chỉ:

- Nhận câu lệnh từ người dùng.
- Gửi câu lệnh lên Backend.
- Nhận phản hồi.
- Thông báo phòng rảnh/bận.
- Tiếp tục điều hướng khi được Backend cho phép.

Robot **không được tạo, sửa, hủy hoặc đặt lịch phòng thông qua hội thoại**.

Đây là ranh giới chức năng cần giữ xuyên suốt quá trình phát triển.

## 2. Ví dụ hoạt động hoàn chỉnh

Giả sử người quản lý tạo một lịch trên website:

| Thông tin | Nội dung |  |  |
| --- | --- | --- | --- |
| Phòng | Phòng 02 |  |  |
| Ngày | 19/11/2026 |  |  |
| Bắt đầu | 14:00 |  |  |
| Kết thúc | 16:00 |  |  |
| Nội dung | Họp dự án |  |  |
| Trạng thái | Đã xác nhận |  |  |

Đến 14:30 cùng ngày, người dùng nói với robot:

> Giao tài liệu đến phòng 2.
> 

Hệ thống xử lý:

**Bước 1:** Robot gửi câu lệnh tiếng Việt đến Backend API.

**Bước 2:** Gemini NLP nhận diện đây là yêu cầu giao tài liệu và phòng đích là 02.

**Bước 3:** Backend truy vấn lịch phòng 02.

**Bước 4:** Hệ thống phát hiện phòng có lịch họp đến 16:00.

**Bước 5:** Backend trả kết quả BUSY, không cho phép robot bắt đầu điều hướng đến phòng đó.

**Bước 6:** Robot phản hồi:

> Phòng 02 hiện đang có lịch họp đến 16 giờ. Phòng dự kiến rảnh sau thời gian này. Vui lòng thực hiện yêu cầu giao tài liệu sau 16 giờ.
> 

Nếu phòng rảnh, Backend trả kết quả FREE và robot có thể tiếp tục quy trình điều hướng hiện tại, với điều kiện những kiểm tra an toàn khác cũng đạt yêu cầu.

**Lưu ý:** Phòng không có lịch họp không đồng nghĩa với việc chắc chắn có người nhận tài liệu. Hệ thống đang xác minh khả năng tiếp nhận theo lịch, không xác minh sự hiện diện thực tế của con người.

---

# PHẦN II. KIẾN TRÚC TỔNG THỂ

## 3. Các thành phần của hệ thống

Tớ đề xuất tổ chức thành bốn lớp.

| Lớp | Thành phần | Vai trò |
| --- | --- | --- |
| Presentation | Calendar Web UI | Giao diện quản lý lịch phòng |
| Application | FastAPI + Gemini NLP + Calendar Service | Xử lý yêu cầu và kiểm tra lịch |
| Data | PostgreSQL | Lưu lịch phòng, cấu hình và lịch sử chỉnh sửa |
| Client | Robot | Gửi câu lệnh, nhận kết quả và thực hiện điều hướng |

Kiến trúc luồng dữ liệu:

`NGƯỜI QUẢN LÝ
                          │
                          ▼
                 CALENDAR WEB UI
                          │
                    HTTPS API
                          │
                          ▼
                ┌───────────────────┐
                │                   │
                │  FASTAPI BACKEND  │
                │                   │
                │  Calendar API     │
                │  Calendar Service │
                │  Availability     │
                │                   │
                └─────────┬─────────┘
                          │
                          ▼
                    POSTGRESQL
                          ▲
                          │
                  READ CALENDAR
                          │
                ┌─────────┴─────────┐
                │                   │
                │  FASTAPI BACKEND  │
                │                   │
                │  Gemini NLP       │
                │  Room Resolver    │
                │  Availability     │
                │  Response Builder │
                │                   │
                └─────────▲─────────┘
                          │
                         HTTPS
                          │
                        ROBOT
                          ▲
                          │
                   Người dùng nói
                "Giao tài liệu phòng 2"`

Hai khối FastAPI trong sơ đồ là **hai nhóm chức năng trong cùng một Backend**, không phải hai server độc lập.

### Nguyên tắc kiến trúc

- Website và NLP dùng chung dữ liệu lịch.
- Website không tự quyết định phòng rảnh/bận.
- Robot không truy cập trực tiếp PostgreSQL.
- Gemini không tự suy đoán trạng thái lịch.
- Mọi quyết định rảnh/bận đều do một Availability Service thống nhất thực hiện.
- Backend phải trả về trạng thái có cấu trúc để robot xử lý an toàn.
- Không sửa thuật toán Nav2 trong phạm vi này.

---

# PHẦN III. NĂM Ý TƯỞNG ĐÃ ĐƯỢC CHẤP NHẬN

## 4. Ý tưởng 1 — Website Calendar và Dashboard trạng thái phòng

Đây là phần giao diện cho người quản lý.

Tớ đề xuất xây dựng một website có ba trang chính:

| Trang | Chức năng |
| --- | --- |
| Dashboard | Theo dõi trạng thái của 7 phòng |
| Calendar | Xem và chỉnh sửa lịch theo ngày, tuần, tháng |
| Room Management | Xem cấu hình và chính sách của từng phòng |

### 4.1. Dashboard

Màn hình chính hiển thị:

| Phòng | Trạng thái | Rảnh tiếp theo | Lịch tiếp theo |
| --- | --- | --- | --- |
| Phòng 01 | FREE | Hiện tại | 15:00 |
| Phòng 02 | BUSY | 16:00 | 14:00–16:00 |
| Phòng 03 | FREE | Hiện tại | 17:00 |
| Phòng 04 | BUSY | 15:30 | 13:00–15:30 |
| Phòng 05 | FREE | Hiện tại | Không có |
| Phòng 06 | FREE | Hiện tại | Không có |
| Phòng 07 | FREE | Hiện tại | 16:30 |

Đây là dữ liệu minh họa.

Dashboard nên tự cập nhật trạng thái theo thời gian dựa trên lịch đã lưu.

Ví dụ, khi một cuộc họp bắt đầu lúc 14:00, trạng thái tương ứng thay đổi thành BUSY mà người quản lý không cần thao tác thủ công.

Khi cuộc họp kết thúc, hệ thống tính toán lại trạng thái theo các lịch còn hiệu lực.

### 4.2. Calendar

Giao diện lịch nên có:

- Chế độ ngày, tuần, tháng.
- Bộ lọc theo phòng.
- Màu sắc phân biệt các phòng.
- Form tạo hoặc chỉnh sửa cuộc họp.
- Kiểm tra trùng lịch trước khi lưu.
- Xác nhận trước khi xóa lịch.
- Lịch lặp lại hằng tuần.

Tớ đề xuất dùng **FullCalendar** để xây dựng phần này.

### 4.3. Room Management

Trang này quản lý danh sách bảy phòng hiện có.

Không cần thay đổi tọa độ robot hoặc dữ liệu bản đồ.

Website chỉ sử dụng room_id đã được NLP nhận diện, chẳng hạn:

`01
02
03
04
05
06
07`

Đây là điểm quan trọng để tránh tình trạng website lưu room_02, trong khi NLP trả 02, dẫn đến không tìm thấy lịch.

---

## 5. Ý tưởng 2 — Kiểm tra khoảng thời gian dự kiến robot đến phòng

Tớ muốn triển khai ý tưởng này ngay trong phiên bản đầu.

### 5.1. Vấn đề

Nếu Backend chỉ kiểm tra thời điểm người dùng đưa ra yêu cầu, kết quả có thể không còn chính xác khi robot đến nơi.

Ví dụ:

| Sự kiện | Thời gian |
| --- | --- |
| Người dùng yêu cầu | 13:55 |
| Phòng bắt đầu họp | 14:00 |
| Robot dự kiến đến | 14:02 |

Tại 13:55, phòng vẫn đang rảnh.

Nhưng khi robot đến lúc 14:02, phòng đã có lịch họp.

### 5.2. Giải pháp

Backend cần kiểm tra **khoảng thời gian giao nhận dự kiến**.

Định nghĩa:

- t0t0: Thời điểm nhận yêu cầu.
- TtravelTtravel: Thời gian dự kiến để robot đến phòng.
- ThandoffThandoff: Thời gian dành cho việc giao nhận.

Khi đó:

tarrival=t0+Ttravel

tarrival=t0+Ttravel

Khoảng thời gian cần kiểm tra:

tarrival, tarrival+Thandoff)

[tarrival, tarrival+Thandoff)

Ví dụ:

t0=13:55

t0=13:55

Ttravel=7 phuˊt

Ttravel=7 phuˊt

Thandoff=3 phuˊt

Thandoff=3 phuˊt

Suy ra:

tarrival=14:02

tarrival=14:02

Backend kiểm tra khoảng:

[14:02, 14:05)

[14:02, 14:05)

Nếu khoảng này trùng lịch phòng, kết quả là BUSY.

### 5.3. Phiên bản đầu tiên

Vì hiện tại chúng ta chỉ phát triển trên Backend main, chưa cần kết nối dữ liệu ước lượng thời gian từ Nav2.

Tớ đề xuất sử dụng các thông số cấu hình:

`TRAVEL_TIME_ESTIMATE = 7 minutes
HANDOFF_DURATION = 3 minutes`

Đây là giá trị minh họa để thiết kế và kiểm thử, không phải thời gian di chuyển đã đo của robot.

Sau này, nếu cậu muốn, có thể thay bằng thời gian dự kiến thực tế mà robot cung cấp.

---

## 6. Ý tưởng 3 — Chính sách giao tài liệu riêng cho từng phòng

Không phải phòng nào cũng có cùng quy định sử dụng.

Ví dụ, phòng họp có thể không cho phép giao tài liệu khi đang có cuộc họp, nhưng phòng làm việc thông thường có thể cho phép giao ở cửa.

Vì vậy, tớ đề xuất bổ sung **Room Delivery Policy**.

### 6.1. Các chính sách

| Chính sách | Ý nghĩa |
| --- | --- |
| BLOCK_WHEN_BUSY | Có lịch họp thì không cho phép giao |
| ALLOW_DOOR_HANDOFF | Có thể giao bên ngoài cửa dù phòng có lịch họp |
| BUSINESS_HOURS_ONLY | Chỉ cho phép giao trong giờ hành chính |
| MANAGER_APPROVAL | Cần người quản lý cho phép |
| DISABLED | Tạm thời không tiếp nhận yêu cầu giao |

Ví dụ:

| Phòng | Chính sách |
| --- | --- |
| Phòng 01 | BLOCK_WHEN_BUSY |
| Phòng 02 | BLOCK_WHEN_BUSY |
| Phòng 03 | ALLOW_DOOR_HANDOFF |
| Phòng 04 | BUSINESS_HOURS_ONLY |
| Phòng 05 | BLOCK_WHEN_BUSY |
| Phòng 06 | DISABLED |
| Phòng 07 | MANAGER_APPROVAL |

Bảng trên chỉ là ví dụ, chưa phải cấu hình chính thức.

### 6.2. Phân biệt trạng thái lịch và quyền giao tài liệu

Đây là chi tiết kỹ thuật cần thiết.

Một phòng có thể:

- calendarStatus = BUSY
- deliveryAllowed = true

Ví dụ, phòng 03 đang họp nhưng cho phép giao tài liệu tại cửa.

Do đó, Backend không nên đồng nhất hai biến:

```
{"roomId":"03","calendarStatus":"BUSY","deliveryPolicy":"ALLOW_DOOR_HANDOFF","deliveryAllowed":true
}
```

Robot có thể nhận câu trả lời:

> Phòng 03 hiện đang có lịch họp. Tuy nhiên, tài liệu có thể được giao tại khu vực tiếp nhận bên ngoài phòng.
> 

**Quyết định thiết kế:** Trong lần triển khai đầu tiên, sử dụng BLOCK_WHEN_BUSY làm mặc định cho cả bảy phòng. Sau đó bổ sung khả năng cấu hình riêng trên website.

---

## 7. Ý tưởng 4 — Tự động tìm thời gian phòng rảnh tiếp theo

Khi phòng bận, Backend không nên chỉ trả lời:

> Phòng 02 đang bận.
> 

Hệ thống nên xác định **khoảng thời gian sớm nhất có thể giao tài liệu**.

### 7.1. Ví dụ

Giả sử lịch phòng 02:

| Bắt đầu | Kết thúc | Nội dung |
| --- | --- | --- |
| 13:00 | 14:30 | Họp kỹ thuật |
| 15:00 | 16:00 | Họp dự án |
| 16:30 | 17:00 | Họp nhóm |

Người dùng yêu cầu giao tài liệu lúc 14:00.

Backend phát hiện phòng đang có lịch họp.

Sau đó kiểm tra các khoảng trống tiếp theo.

Kết quả:

`14:30–15:00: Rảnh 30 phút
16:00–16:30: Rảnh 30 phút`

Nếu khoảng giao nhận dự kiến yêu cầu 10 phút và không có giới hạn khác, Backend có thể đề xuất 14:30.

Robot phản hồi:

> Phòng 02 đang có lịch họp đến 14 giờ 30. Thời gian có thể giao tài liệu sớm nhất là 14 giờ 30.
> 

### 7.2. Thuật toán

Backend thực hiện:

1. Lấy các sự kiện lịch của phòng.
2. Sắp xếp theo thời gian bắt đầu.
3. Gộp các khoảng bận chồng lấn hoặc liền nhau.
4. Tìm khoảng trống đầu tiên đủ dài cho việc giao nhận.
5. Áp dụng chính sách phòng.
6. Trả về thời gian phù hợp.

Đặc biệt, cần kiểm tra **độ dài khoảng trống**, không chỉ thời điểm cuộc họp kết thúc.

Nếu phòng rảnh 2 phút nhưng robot cần 10 phút để giao nhận thì không nên đề xuất khoảng đó.

---

## 8. Ý tưởng 5 — Kiểm tra lại lịch trước khi cho phép điều hướng

Đây là cơ chế bảo vệ khi người quản lý thay đổi lịch.

### 8.1. Vấn đề

Giả sử:

- 13:00: Robot gửi yêu cầu đến phòng 02.
- 13:00: Backend trả kết quả FREE.
- 13:01: Người quản lý thêm lịch họp bắt đầu lúc 13:05.
- 13:03: Robot chuẩn bị khởi hành.

Nếu robot sử dụng kết quả kiểm tra cũ, quyết định giao tài liệu có thể không còn phù hợp.

### 8.2. Giải pháp

Tớ đề xuất cơ chế **Availability Validation** với thời hạn hiệu lực ngắn.

Ví dụ:

```
{"roomId":"02","calendarStatus":"FREE","deliveryAllowed":true,"checkedAt":"2026-11-18T13:00:00+07:00","validUntil":"2026-11-18T13:00:30+07:00"
}
```

Hệ thống robot chỉ được sử dụng kết quả này trong khoảng thời gian cho phép.

Khi chuẩn bị gửi goal đến Nav2, nếu kết quả đã hết hạn thì client phải yêu cầu Backend kiểm tra lại.

### 8.3. Cần xử lý thêm trường hợp lịch thay đổi

Khi người quản lý thêm lịch mới, Backend cập nhật dữ liệu ngay lập tức.

Các yêu cầu kiểm tra sau đó phải sử dụng lịch mới.

Tớ đề xuất mỗi lần cập nhật lịch sẽ tăng một số phiên bản:

`calendar_version = 15`

Khi dữ liệu lịch thay đổi:

`calendar_version = 16`

Backend có thể sử dụng phiên bản này để phát hiện kết quả kiểm tra trước đó đã lỗi thời.

**Lưu ý:** Thời hạn hiệu lực và phiên bản lịch chỉ giúp phát hiện dữ liệu cũ; chúng không giữ chỗ phòng và không bảo đảm lịch sẽ không thay đổi sau khi kiểm tra.

---

# PHẦN IV. THIẾT KẾ CƠ SỞ DỮ LIỆU

## 9. PostgreSQL lưu những dữ liệu gì?

Tớ đề xuất một database chuyên phục vụ Calendar, hoàn toàn độc lập với cơ chế phân công nhiệm vụ trước đây.

### Bảng 1 — rooms

Quản lý thông tin các phòng.

| Trường | Ý nghĩa |
| --- | --- |
| room_id | Mã phòng |
| display_name | Tên hiển thị |
| enabled | Phòng có đang hoạt động không |
| delivery_policy | Chính sách giao tài liệu |
| description | Mô tả |

Mã phòng phải thống nhất với dữ liệu NLP hiện tại.

### Bảng 2 — calendar_events

Lưu các cuộc họp.

| Trường | Ý nghĩa |
| --- | --- |
| event_id | Mã sự kiện |
| room_id | Phòng được sử dụng |
| title | Tên cuộc họp |
| start_time | Thời gian bắt đầu |
| end_time | Thời gian kết thúc |
| status | Trạng thái cuộc họp |
| created_by | Người tạo |
| updated_at | Thời điểm cập nhật |

Hai lịch còn hiệu lực không được trùng thời gian trong cùng một phòng. Việc kiểm tra cần thực hiện ở tầng cơ sở dữ liệu hoặc trong một giao dịch có kiểm soát để tránh hai người quản lý đồng thời đặt trùng lịch.

### Bảng 3 — calendar_users

Quản lý tài khoản và quyền truy cập website.

### Bảng 4 — calendar_audit_logs

Lưu lịch sử thay đổi:

- Ai tạo cuộc họp.
- Ai sửa thời gian.
- Ai hủy cuộc họp.
- Thao tác được thực hiện khi nào.

Tớ đề xuất giữ bảng này ngay từ phiên bản đầu vì dữ liệu lịch có thể ảnh hưởng trực tiếp đến quyết định điều hướng.

---

# PHẦN V. THIẾT KẾ API VÀ TÍCH HỢP NLP

## 10. Hai nhóm API

### Nhóm A — Calendar Management API

Phục vụ website.

| Endpoint đề xuất | Chức năng |
| --- | --- |
| GET /v1/calendar/rooms | Lấy danh sách phòng |
| GET /v1/calendar/events | Lấy danh sách lịch |
| POST /v1/calendar/events | Tạo lịch |
| PATCH /v1/calendar/events/{id} | Chỉnh sửa lịch |
| DELETE /v1/calendar/events/{id} | Hủy lịch |
| GET /v1/calendar/availability | Kiểm tra phòng |
| GET /v1/calendar/free-slots | Tìm khoảng thời gian rảnh |

Các API ghi dữ liệu chỉ dành cho người quản lý đã xác thực.

### Nhóm B — NLP API

Giữ đường dẫn API hiện tại:

`POST /v1/sessions/{sessionId}/messages`

Backend bổ sung xử lý:

`Nhận câu lệnh
      ↓
Gemini NLP
      ↓
Xác định phòng
      ↓
Kiểm tra Calendar
      ↓
Áp dụng Delivery Policy
      ↓
Tạo phản hồi
      ↓
Trả kết quả về robot`

Tớ đề xuất **giữ nguyên các trường phản hồi NLP hiện có** và bổ sung một đối tượng thông tin kiểm tra lịch.

Ví dụ khi phòng bận:

```
{"sessionId":"session-example","messageId":"message-example","dialogueStatus":"ROOM_BUSY","reply":"Phòng 02 đang có lịch họp đến 16 giờ.","batch":null,"roomAvailability": {"roomId":"02","status":"BUSY","availableFrom":"2026-11-18T16:00:00+07:00","deliveryAllowed":false}
}
```

Đây là **API contract đề xuất**, chưa được triển khai trên nhánh main.

Khi hiện thực, tớ muốn bổ sung một nguyên tắc:

**Không sử dụng riêng TASK_READY để kết luận robot được phép di chuyển.**

Robot phải kiểm tra kết quả cho phép giao tài liệu. Nếu Backend trả BUSY, UNKNOWN hoặc không có xác nhận hợp lệ thì không gửi goal điều hướng.

Điều này đòi hỏi cập nhật client robot ở giai đoạn tích hợp sau, nhưng hiện tại chúng ta chỉ thiết kế và phát triển phía Server.

---

# PHẦN VI. THỨ TỰ HOẠT ĐỘNG VÀ KIỂM THỬ

## 11. Quy trình hoạt động của hệ thống

### Quy trình A — Người quản lý cập nhật lịch

`Người quản lý
      ↓
Đăng nhập website
      ↓
Chọn phòng
      ↓
Tạo / Sửa / Hủy lịch
      ↓
Backend kiểm tra dữ liệu
      ↓
PostgreSQL lưu thay đổi
      ↓
Dashboard cập nhật`

### Quy trình B — Robot nhận yêu cầu

`Người dùng
      ↓
Nói "Giao tài liệu đến phòng 2"
      ↓
Robot gửi yêu cầu HTTPS
      ↓
Gemini xác định phòng 02
      ↓
Backend kiểm tra lịch
      ↓
Kiểm tra Delivery Policy
      ↓
Trả FREE / BUSY / UNKNOWN
      ↓
Robot thông báo kết quả`

Chỉ khi đủ điều kiện, hệ thống phía robot mới được tiếp tục điều hướng.

## 12. Các trường hợp cần kiểm thử

| Test | Tình huống | Kết quả mong muốn |
| --- | --- | --- |
| T01 | Phòng không có lịch | FREE |
| T02 | Phòng đang có cuộc họp | BUSY |
| T03 | Phòng sắp có cuộc họp | Kiểm tra khoảng giao dự kiến |
| T04 | Hai lịch trùng nhau | Từ chối tạo lịch |
| T05 | Người quản lý sửa lịch | Backend đọc dữ liệu mới |
| T06 | Cuộc họp vừa kết thúc | Tính toán lại trạng thái |
| T07 | Database mất kết nối | UNKNOWN, không cho phép điều hướng |
| T08 | Phòng không tồn tại | Trả lỗi phòng không hợp lệ |
| T09 | Lịch thay đổi sau khi kiểm tra | Kết quả cũ bị coi là không còn hiệu lực |
| T10 | Phòng có chính sách đặc biệt | Áp dụng đúng Delivery Policy |
| T11 | Render khởi động lại | Lịch vẫn được lưu |
| T12 | Robot gửi lại cùng một yêu cầu | Không tạo bản ghi lịch mới |
| T13 | Người dùng yêu cầu đặt phòng qua NLP | Không thực hiện thay đổi lịch |

---

# PHẦN VII. LỘ TRÌNH TRIỂN KHAI TRÊN NHÁNH main

Tớ đề xuất triển khai theo thứ tự sau.

| Giai đoạn | Công việc | Tiêu chí hoàn thành |
| --- | --- | --- |
| **1. Database** | Tạo bảng phòng, lịch, người dùng và lịch sử thay đổi | Dữ liệu được lưu bền vững |
| **2. Calendar API** | Xây dựng API quản lý và kiểm tra lịch | Kiểm thử API đạt |
| **3. Calendar Website** | Xây dựng Dashboard và giao diện lịch | Người quản lý thao tác được |
| **4. Availability Engine** | Triển khai kiểm tra khoảng thời gian, chính sách và tìm giờ rảnh | Đạt các trường hợp kiểm thử |
| **5. NLP Integration** | Tích hợp kiểm tra lịch vào API NLP hiện tại | Server phản hồi đúng FREE/BUSY |
| **6. Validation & Security** | Kiểm thử phân quyền, lịch thay đổi, lỗi database và khởi động lại | Không phát sinh quyết định điều hướng thiếu an toàn |
| **7. Deployment** | Cập nhật dịch vụ Render | Website và NLP API hoạt động trên cùng Backend |

Các giai đoạn này đều triển khai trên nhánh main của mtreeiai/office-robot-nlp, theo yêu cầu của cậu. Mỗi giai đoạn nên có kiểm thử và commit riêng để dễ khôi phục nếu có sự cố.

---

# PHẦN VIII. KẾT LUẬN THIẾT KẾ

Hệ thống mà cậu muốn xây dựng gồm **hai phân hệ chính**:

### 1. Office Room Calendar Management

Dành cho người quản lý.

- Website quản lý lịch bảy phòng.
- PostgreSQL lưu dữ liệu lịch.
- Tự động hiển thị trạng thái phòng.
- Quản lý chính sách giao tài liệu.
- Phát hiện lịch trùng.
- Tìm thời gian phòng rảnh.

### 2. NLP Room Availability Verification

Dành cho robot.

- Gemini NLP nhận diện phòng đích.
- Backend kiểm tra lịch.
- Xét khoảng thời gian robot dự kiến đến.
- Áp dụng chính sách của phòng.
- Đề xuất thời điểm có thể giao.
- Xác minh lại kết quả trước khi điều hướng.

**Năm ý tưởng đã thống nhất** được tích hợp vào hai phân hệ này, không cần tạo thêm hệ thống phân công nhiệm vụ hoặc thay đổi Nav2.

### Định hướng kỹ thuật cuối cùng

Tớ đề xuất giữ ba nguyên tắc xuyên suốt:

**Một là, lịch phòng chỉ được quản lý thông qua website và các API quản trị có phân quyền.** Robot không được thay đổi lịch.

**Hai là, Backend là nguồn quyết định duy nhất về trạng thái lịch và khả năng giao tài liệu.** Gemini chỉ phân tích ngôn ngữ, không tự suy đoán dữ liệu lịch.

**Ba là, chỉ những kết quả kiểm tra còn hiệu lực mới được phép sử dụng để quyết định có tiếp tục điều hướng hay không.**

Với thiết kế này, dự án của cậu sẽ có một Backend phục vụ hai đối tượng khác nhau: **người quản lý vận hành lịch trên website và robot sử dụng dữ liệu lịch để xử lý yêu cầu giao tài liệu thông minh hơn**.

**Bước triển khai đầu tiên nên là thiết kế Database và Calendar API**, vì đây là nền tảng để xây dựng website, Availability Engine và tích hợp NLP mà không phải chỉnh sửa kiến trúc nhiều lần.

# PHẦN IX. **Flow**

![image.png

## **Flow A — Người dùng đặt lịch họp qua website**

Ví dụ: Người dùng đặt phòng 02 từ 14:00 đến 16:00 ngày 20/11/2026.

!image.png

Một lịch họp phải chứa tối thiểu: phòng, người đặt, ngày giờ bắt đầu, kết thúc và trạng thái.

**Điểm cần xử lý ở Database:** Hai người có thể đặt cùng một phòng gần như đồng thời. Chỉ kiểm tra lịch trùng ở website là không đủ; PostgreSQL cần ràng buộc bảo đảm hai lịch đã xác nhận không chồng lấn. Có thể dùng `tstzrange` và exclusion constraint

## **Flow B — Robot truy vấn trạng thái phòng để sắp xếp giao việc**

Ví dụ:

- Phòng 02 có lịch họp 14:00–16:00.
- Robot có yêu cầu giao tài liệu đến phòng 02.
- Thời gian dự kiến robot đến là 14:30.

!image.png

Ở đây, Backend **không gửi lệnh điều hướng và không tự sắp xếp các task**.

Phía Robot sử dụng phản hồi để quyết định:

| Phản hồi từ Server | Robot xử lý |
| --- | --- |
| `ALLOW` | Có thể xét thực hiện, nếu điều kiện robot/Nav2 hợp lệ |
| `DEFER` | Tạm hoãn; có thể thực hiện nhiệm vụ khác |
| `DENY` | Không tự giao đến phòng đó |
| `NEEDS_APPROVAL` | Chờ phê duyệt |
| `UNKNOWN` | Không coi là được phép giao; thử lại hoặc báo operator |

Điểm mới quan trọng là **robot có thể sử dụng lịch phòng như một ràng buộc khi sắp xếp công việc**, thay vì chỉ biết địa điểm cần đi.

## **Flow C — Lịch thay đổi sau khi robot đã nhận yêu cầu**

Ví dụ robot đã kiểm tra phòng rảnh lúc 13:50, nhưng người dùng tạo lịch họp mới lúc 13:52.

Giải pháp là mỗi kết quả Availability có:

- `checkedAt`: thời gian kiểm tra.
- `validUntil`: thời hạn sử dụng kết quả.
- `calendarVersion`: phiên bản lịch khi kiểm tra.

Khi chuẩn bị bắt đầu chặng giao đến phòng, robot phải kiểm tra lại nếu kết quả hết hạn hoặc không còn phù hợp.

Nếu robot đã di chuyển và lịch thay đổi, việc xử lý phải tuân thủ chính sách điều hướng an toàn của robot. Calendar API không được tự phát lệnh hủy Nav2.

## **5. Thiết kế Database**

| Bảng | Dữ liệu chính | Chức năng |
| --- | --- | --- |
| `calendar_rooms` | room_id, name, bookable, enabled | Danh mục phòng |
| `calendar_users` | user_id, email, role | Người dùng website |
| `calendar_bookings` | booking_id, room_id, organizer_id, start_at, end_at, status | Lịch họp |
| `calendar_room_policies` | room_id, policy, working_hours | Quy định tiếp nhận |
| `calendar_audit_logs` | user_id, action, time, changes | Lịch sử thao tác |
| `calendar_room_versions` | room_id, version | Phát hiện lịch thay đổi |

Về sau, khi triển khai lịch lặp lại hằng tuần, cần thêm cách biểu diễn chuỗi lịch và những lần xuất hiện ngoại lệ.

### **Hai quyết định dữ liệu quan trọng**

**Thứ nhất, không mặc định cả bảy phòng đều đặt lịch họp được.**

`data/office_rooms.json` đang có bảy địa điểm với ID từ `01` đến `07`, trong đó có những khu vực như phòng giải lao và phòng ăn. Chúng có thể là đích điều hướng nhưng không nhất thiết là phòng họp.

GitHub

Vì vậy cần trường `bookable`. Nhóm sẽ xác nhận phòng nào thực sự cho phép đặt lịch.

**Thứ hai, lịch phòng không giống trạng thái hiện diện thực tế.**

Nếu phòng không có cuộc họp được đăng ký thì kết quả `FREE` chỉ có nghĩa **không có lịch bận trong hệ thống**, không chứng minh phòng không có người.

Về sau, nếu có cảm biến hoặc Computer Vision, có thể kết hợp dữ liệu hiện diện thật; nhưng chưa nằm trong phạm vi này.

## **6. Thiết kế API cho Website và Robot**

Mình đề xuất tách thành hai nhóm API, có quyền truy cập khác nhau.

### **Nhóm A — Calendar Booking API**

Dành cho website.

| Method | Endpoint đề xuất | Chức năng |
| --- | --- | --- |
| GET | `/v1/calendar/rooms` | Danh sách phòng có thể đặt |
| GET | `/v1/calendar/bookings` | Lấy lịch theo phòng/khoảng thời gian |
| POST | `/v1/calendar/bookings` | Tạo lịch họp |
| PATCH | `/v1/calendar/bookings/{id}` | Sửa lịch |
| DELETE | `/v1/calendar/bookings/{id}` | Hủy lịch |
| GET | `/v1/calendar/free-slots` | Tìm khung giờ phòng còn trống |

Người dùng được tạo và quản lý lịch của mình. Admin có thể quản lý phòng, chính sách và các lịch khác theo phân quyền.

### **Nhóm B — Robot Availability API**

Dành cho robot truy vấn, chỉ đọc.

Endpoint đề xuất

```
POST /v1/robot/availability/check
Authorization: Bearer <ROBOT_READ_TOKEN>
Content-Type: application/json
```

**Input**

```
{
  "roomId": "02",
  "operation": "DOCUMENT_DELIVERY",
  "arrivalWindow": {
    "start": "2026-11-20T14:30:00+07:00",
    "end": "2026-11-20T14:35:00+07:00"
  }
}
```

Trong đó `arrivalWindow` do phía Robot cung cấp từ thời gian dự kiến thực thi. Nếu chưa biết ETA, robot có thể yêu cầu kiểm tra sơ bộ, nhưng kết quả phải được phân biệt với một quyết định đã kiểm tra theo khoảng thời gian cụ thể.

**Output**

```
{
  "roomId": "02",
  "calendarStatus": "BUSY",
  "deliveryDecision": "DEFER",
  "reasonCode": "ROOM_BOOKED",
  "nextAvailableFrom": "2026-11-20T16:00:00+07:00",
  "checkedAt": "2026-11-20T14:25:00+07:00",
  "validUntil": "2026-11-20T14:25:30+07:00",
  "calendarVersion": 8,
  "dataSource": "CALENDAR"
}
```

Đây là **hợp đồng API đề xuất**, chưa phải endpoint đang tồn tại trên `main`.

Việc tách API độc lập có lợi thế là nhóm robot có thể truy vấn lại Availability bất cứ lúc nào, không cần gửi lại câu nói qua Gemini.

NLP vẫn có thể gọi chung Availability Service để trả lời bằng tiếng Việt:

> “Phòng 02 có lịch họp đến 16 giờ. Theo lịch hiện tại, bạn có thể giao tài liệu sau thời điểm đó.”
> 

Nhưng không nên thay đổi `dialogueStatus` hiện có một cách phá vỡ contract với HMI.

## **8. Những trường hợp kiểm thử bắt buộc**

| Test | Input | Kết quả mong đợi |
| --- | --- | --- |
| T01 | Đặt phòng 02 lúc 14:00–16:00 | Booking được tạo |
| T02 | Đặt trùng phòng 02 lúc 15:00 | `409 CONFLICT` |
| T03 | Đặt phòng 03 cùng thời gian | Thành công nếu phòng bookable và còn trống |
| T04 | Robot hỏi phòng 02 lúc 14:30 | `BUSY`, theo chính sách trả `DEFER` |
| T05 | Robot hỏi phòng 02 sau 16:00 | `FREE` nếu không có lịch khác |
| T06 | Admin hủy cuộc họp | Availability cập nhật |
| T07 | Người dùng khác sửa lịch không thuộc quyền | `403 FORBIDDEN` |
| T08 | Robot token gọi API tạo lịch | Bị từ chối |
| T09 | PostgreSQL không truy cập được | `UNKNOWN` hoặc lỗi có cấu trúc, không trả `FREE` |
| T10 | Hai request đặt trùng đồng thời | Chỉ một booking được lưu |
| T11 | Calendar thay đổi sau khi Robot kiểm tra | Kết quả cũ không còn đáng tin |
| T12 | Restart Backend | Lịch đã lưu vẫn tồn tại |

Để kiểm thử end-to-end, nhóm nên chuẩn bị một môi trường **Fake Robot Client** gọi Availability API trước, rồi mới kết nối HMI/ROS 2 thực tế. Cách này giúp tách lỗi Server khỏi lỗi Robot.

## **9. Tọa độ cần bổ sung vào giai đoạn nào?**

**Chưa cần tọa độ ở Phase 0–4.**

Các chức năng Website, Database, Calendar API và Availability Engine chỉ cần `roomId`.

Ở Phase 5, hai bên cần thống nhất ánh xạ:

```
Calendar Room ID: "02"
         |
         v
NLP Semantic Room ID: "02"
         |
         v
Robot Navigation Location ID: "02"
```

Tọa độ `{x, y, yaw}` là dữ liệu của hệ thống điều hướng. Không nên lưu hoặc cho phép người dùng chỉnh tọa độ Nav2 trong Calendar Website.

Khi bạn gửi tọa độ sau, chúng ta có thể thiết kế lớp ánh xạ location và kiểm tra bản đồ đang chạy. Điều này sẽ không làm thay đổi phần Database lịch họp.

## **10. Những quyết định nên thống nhất trước khi triển khai**

Mình đề xuất mặc định như sau:

| Vấn đề | Phương án đề xuất |
| --- | --- |
| Base branch | `origin/main` |
| Development branch | `feature/office-room-calendar` |
| Booking permissions | Người dùng đặt lịch; owner/admin sửa, hủy |
| Room IDs | Giữ `01`–`07`, chỉ phòng được duyệt mới `bookable` |
| Booking conflict | Không cho lịch xác nhận chồng lấn |
| Room Policy mặc định | `BLOCK_WHEN_BUSY` |
| Robot access | API read-only với token riêng |
| NLP integration | Giữ tương thích, không đổi đột ngột `TASK_READY` |
| Robot task scheduling | Phía Robot đảm nhiệm |
| Calendar source | PostgreSQL |
| Recurring booking | Bổ sung sau CRUD lịch đơn |
| Deployment | Staging riêng trước, không ảnh hưởng production |
| Coordinates | Bổ sung sau, không phụ thuộc Calendar |

### **Đề xuất cuối cùng**

Mình khuyên **chốt thiết kế theo 7 phase trên**, nhưng chỉ giao agent triển khai Phase 0 trước để xuất báo cáo kiến trúc và hợp đồng API, rồi mới bắt đầu Phase 1–6.

Kết quả mong đợi của hướng phát triển này không phải một Scheduler mới, mà là một **hệ thống đặt lịch phòng đáng tin cậy, với Availability API thống nhất cho cả NLP và Robot**.

Khi hoàn thành, bạn sẽ có thể trình diễn rõ ràng:

**Người dùng đặt lịch trên Website → PostgreSQL lưu → Robot hỏi Server → Server trả trạng thái phòng → Robot quyết định giao ngay, hoãn hoặc chọn nhiệm vụ khác.**

Đó là một phạm vi phát triển độc lập, dễ kiểm thử, không bị phụ thuộc vào conflict của nhánh Integration v1 và phù hợp để phát triển tiếp khi bạn cung cấp dữ liệu vị trí thực tế.