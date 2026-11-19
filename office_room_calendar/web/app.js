'use strict';
const $ = selector => document.querySelector(selector);
const state = { user: null, csrf: null, rooms: [], calendar: null, page: 'dashboard', loading: false, key: null };
const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const pageText = {
  dashboard: ['Tổng quan', 'Một ngày làm việc nhịp nhàng.', 'Tình trạng lịch của bảy địa điểm, được cập nhật từ server.'],
  calendar: ['Lịch đặt phòng', 'Dành chỗ cho những ý tưởng.', 'Lịch theo ngày, tuần và tháng. Các lịch xác nhận được kiểm tra xung đột tại server.'],
  rooms: ['Quản lý phòng', 'Không gian, theo cách của bạn.', 'Chỉ quản trị viên có thể cập nhật cấu hình và chính sách phòng.']
};
const errorText = {BOOKING_CONFLICT:'Khoảng giờ này đã có lịch. Chọn khoảng giờ khác.', STALE_VERSION:'Dữ liệu đã thay đổi. Đóng biểu mẫu và làm mới trước khi lưu lại.', ROOM_NOT_BOOKABLE:'Phòng chưa được duyệt cho đặt lịch.', FORBIDDEN:'Bạn không có quyền thực hiện thao tác này.', INVALID_CREDENTIALS:'Email hoặc mật khẩu chưa đúng.', LOGIN_THROTTLED:'Đã có quá nhiều lần đăng nhập. Vui lòng thử lại sau.', CALENDAR_UNAVAILABLE:'Không kết nối được dữ liệu lịch. Chưa thể xác nhận phòng rảnh.', INVALID_REQUEST:'Kiểm tra lại nội dung và khoảng thời gian.', INVALID_WINDOW:'Khoảng giờ không hợp lệ hoặc dài quá giới hạn.', INVALID_RECURRENCE:'Chuỗi lặp có giờ không hợp lệ tại thời điểm đổi múi giờ.', IDEMPOTENCY_CONFLICT:'Yêu cầu đã thay đổi. Đóng và mở lại biểu mẫu.'};
function node(tag, text, className) { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; if (className) item.className = className; return item; }
function notice(text, bad=false) {
  const item = $('#notice'); item.textContent = text; item.classList.toggle('error', bad); item.hidden = false;
  const dialog = document.querySelector('dialog[open]');
  if (dialog) { let feedback=dialog.querySelector('[role=alert]'); if (!feedback) {feedback=node('p',undefined,'form-feedback'); feedback.setAttribute('role','alert'); dialog.append(feedback);} feedback.textContent=text; }
}
async function api(path, options={}) {
  const headers = {...options.headers};
  if (options.body) headers['Content-Type'] = 'application/json';
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = state.csrf || '';
  let response;
  try { response = await fetch('/v1/calendar' + path, {...options, headers, credentials:'same-origin'}); }
  catch { throw new Error('Không kết nối được server. Kiểm tra mạng rồi thử lại cùng yêu cầu.'); }
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') signedOut();
    throw new Error(errorText[result.error?.code] || (response.status === 401 ? 'Phiên đăng nhập đã hết hạn.' : 'Không thể hoàn tất yêu cầu. Kiểm tra dữ liệu rồi thử lại.'));
  }
  return result;
}
function signedOut() { state.user = null; state.csrf = null; $('#workspace').hidden = true; $('#account').hidden = true; $('#login-page').hidden = false; $('#admin-nav').hidden = true; document.querySelectorAll('dialog[open]').forEach(d => d.close()); }
async function signedIn(data) {
  state.user = data.user; state.csrf = data.csrfToken;
  $('#login-page').hidden = true; $('#workspace').hidden = false; $('#account').hidden = false;
  $('#account-name').textContent = data.user.displayName + (data.user.role === 'ADMIN' ? ' · Admin' : '');
  $('#admin-nav').hidden = data.user.role !== 'ADMIN';
  $('#timezone-label').textContent = 'Giờ hiển thị: ' + localZone;
  $('#today-label').textContent = new Date().toLocaleDateString('vi-VN', {weekday:'long',day:'numeric',month:'long',year:'numeric'});
  showPage('dashboard'); await refresh();
}
function showPage(page) {
  if (page === 'rooms' && state.user?.role !== 'ADMIN') return;
  state.page = page;
  for (const key of Object.keys(pageText)) $('#' + key + '-page').hidden = key !== page;
  document.querySelectorAll('[data-page]').forEach(b => b.classList.toggle('active', b.dataset.page === page));
  [$('#breadcrumb'), $('#page-title'), $('#page-subtitle')].forEach((e,i) => e.textContent = pageText[page][i]);
  if (page === 'calendar') {
    if (!state.calendar) initializeCalendar();
    requestAnimationFrame(() => state.calendar.updateSize());
  }
}
function fillRooms() {
  const filter = $('#calendar-room'), previous = filter.value;
  filter.replaceChildren(new Option('Tất cả phòng', ''));
  const booking = $('#booking-form').elements.roomId, selected = booking.value; booking.replaceChildren();
  for (const room of state.rooms) {
    filter.add(new Option(room.displayName, room.roomId));
    const option = new Option(room.displayName + (!room.bookable ? ' · chưa mở đặt lịch' : ''), room.roomId);
    option.disabled = !room.bookable || !room.enabled; booking.add(option);
  }
  filter.value = previous;
  if (selected) booking.value = selected;
}
async function refresh() {
  if (!state.user || state.loading) return;
  state.loading = true; document.body.setAttribute('aria-busy','true'); $('#refresh').disabled = true;
  try {
    const data = await api('/rooms'); state.rooms = data.rooms; fillRooms(); renderManagement();
    const start = new Date(), end = new Date(start.getTime()+60000);
    const evidence = await Promise.all(state.rooms.map(room => api('/availability?' + new URLSearchParams({roomId:room.roomId,start:start.toISOString(),end:end.toISOString()}))));
    renderRooms(evidence);
    if (state.calendar) state.calendar.refetchEvents();
  } catch(error) {
    $('#metric-free').textContent = '—'; $('#metric-busy').textContent = '—'; $('#updated-label').textContent = 'Chưa cập nhật được dữ liệu';
    $('#room-grid').replaceChildren(node('p','Không xác minh được lịch hiện tại. Vui lòng làm mới; không coi dữ liệu cũ là phòng rảnh.','muted'));
    notice(error.message,true);
  } finally { state.loading = false; document.body.setAttribute('aria-busy','false'); $('#refresh').disabled = false; }
}
function renderRooms(evidence) {
  $('#metric-total').textContent = state.rooms.length;
  $('#metric-free').textContent = evidence.filter(e => e.calendarStatus==='FREE').length;
  $('#metric-busy').textContent = evidence.filter(e => e.calendarStatus==='BUSY').length;
  $('#metric-bookable').textContent = state.rooms.filter(r => r.bookable && r.enabled).length;
  $('#updated-label').textContent = 'Cập nhật ' + new Date().toLocaleTimeString('vi-VN');
  const grid = $('#room-grid'); grid.replaceChildren();
  for (const [i, room] of state.rooms.entries()) {
    const status = evidence[i].calendarStatus;
    const card = node('article', undefined, 'room-card');
    card.append(node('span',room.roomId,'room-number'),node('span',status==='FREE'?'Lịch rảnh':status==='BUSY'?'Có lịch bận':'Chưa xác minh','badge ' + status.toLowerCase()), node('h3',room.displayName), node('p',room.description || 'Địa điểm ' + room.roomId + ' · ' + room.timezone));
    const foot = node('div',undefined,'card-footer'); foot.append(node('span',!room.enabled?'Đã tắt':room.bookable?'Đã mở đặt lịch':'Chưa mở đặt lịch'));
    const button = node('button','Xem lịch →','text-button'); button.onclick = () => { $('#calendar-room').value=room.roomId; showPage('calendar'); state.calendar.refetchEvents(); }; foot.append(button); card.append(foot); grid.append(card);
  }
}
function renderManagement() {
  const list = $('#room-management'); list.replaceChildren();
  for (const room of state.rooms) {
    const row = node('div',undefined,'management-row'), detail = node('div');
    detail.append(node('strong', room.roomId + ' · ' + room.displayName), node('p', (room.bookable?'Cho đặt lịch':'Chưa mở đặt lịch') + ' · ' + room.deliveryPolicy + ' · v' + room.calendarVersion));
    const button = node('button','Cấu hình','secondary'); button.onclick = () => openRoom(room); row.append(detail,button); list.append(row);
  }
}
function initializeCalendar() {
  state.calendar = new FullCalendar.Calendar($('#calendar-view'), {
    initialView: innerWidth < 600 ? 'timeGridDay' : 'timeGridWeek', locale:'vi', firstDay:1, timeZone:'local', height:'auto', nowIndicator:true,
    buttonIcons:false, buttonText:{prev:'‹',next:'›',today:'Hôm nay',month:'Tháng',week:'Tuần',day:'Ngày'}, allDayText:'Cả ngày',
    headerToolbar:{left:'prev,next today',center:'title',right:'dayGridMonth,timeGridWeek,timeGridDay'},
    selectable:true, selectMirror:true, select:info => openBooking(null,info.start,info.end),
    eventClick:info => openBooking(info.event.extendedProps.booking),
    events: async (range,success,failure) => {
      try {
        const query = new URLSearchParams({start:range.start.toISOString(),end:range.end.toISOString()});
        if ($('#calendar-room').value) query.set('roomId',$('#calendar-room').value);
        const data = await api('/bookings?' + query);
        success(data.bookings.filter(b => b.status==='CONFIRMED').map(b => ({id:b.bookingId,title:b.title + ' · ' + b.roomId,start:b.startAt,end:b.endAt,extendedProps:{booking:b}})));
      } catch(error) { success([]); notice(error.message,true); failure(error); }
    }
  });
  state.calendar.render();
}
function localInput(date) { const d = new Date(date); return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16); }
function openBooking(booking=null,start=null,end=null) {
  if (!state.rooms.length) return notice('Chưa tải được danh sách phòng.',true);
  const form = $('#booking-form'); form.reset(); $('#booking-dialog').querySelector('[role=alert]')?.remove(); state.key = crypto.randomUUID();
  const editable = !booking || booking.organizerId===state.user.userId || state.user.role==='ADMIN';
  for (const field of form.elements) if (!['button','submit','hidden'].includes(field.type)) field.disabled = !editable;
  form.elements.bookingId.value = booking?.bookingId || ''; form.elements.revision.value = booking?.revision || '';
  form.elements.title.value = booking?.title || '';
  form.elements.roomId.value = booking?.roomId || $('#calendar-room').value || state.rooms.find(r => r.enabled && r.bookable)?.roomId || '';
  form.elements.startAt.value = localInput(booking?.startAt || start || new Date());
  form.elements.endAt.value = localInput(booking?.endAt || end || new Date(Date.now()+3600000));
  $('#repeat-label').hidden = !!booking;
  $('#booking-title').textContent = booking ? editable?'Chỉnh sửa lịch':'Chi tiết lịch' : 'Đặt lịch mới';
  $('#booking-detail').textContent = booking ? 'Revision ' + booking.revision + (booking.seriesId?' · Một lần trong chuỗi lặp (chỉnh sửa riêng lần này).':'') + (!editable?' · Chỉ chủ lịch hoặc Admin có quyền sửa.':'') : 'Chuỗi lặp chỉ được lưu khi mọi lần đều không xung đột.';
  $('#form-zone').textContent = 'Nhập và hiển thị giờ theo ' + localZone + '. Server lưu thời điểm UTC.';
  $('#cancel-booking').hidden = !booking || !editable; $('#save-booking').hidden = !editable;
  $('#booking-dialog').showModal();
}
function openRoom(room) {
  $('#room-dialog').querySelector('[role=alert]')?.remove();
  const fields = $('#room-form').elements;
  for (const key of ['roomId','displayName','description','deliveryPolicy','timezone']) fields[key].value = room[key];
  fields.version.value = room.calendarVersion; fields.bookable.checked = room.bookable; fields.enabled.checked = room.enabled; fields.workingHours.value = JSON.stringify(room.workingHours,null,2);
  $('#room-dialog').showModal();
}
async function submit(form, work) {
  const button = form.querySelector('[type=submit]'); button.disabled = true;
  try { await work(); } catch(error) { notice(error.message,true); } finally { button.disabled = false; }
}
$('#login-form').onsubmit = event => {event.preventDefault(); submit(event.target, async () => {const fields=event.target.elements; const data=await api('/auth/login',{method:'POST',body:JSON.stringify({email:fields.email.value,password:fields.password.value})}); fields.password.value=''; $('#notice').hidden=true; await signedIn(data);});};
$('#logout').onclick = async () => {try {await api('/auth/logout',{method:'POST'}); signedOut();} catch(error) {notice(error.message,true);}};
document.querySelectorAll('[data-page]').forEach(button => button.onclick=()=> {if (state.user) showPage(button.dataset.page);});
document.querySelectorAll('[data-close]').forEach(button => button.onclick=()=>$('#'+button.dataset.close).close());
$('#refresh').onclick = refresh;
$('#new-booking').onclick = () => openBooking();
$('#calendar-room').onchange = () => state.calendar?.refetchEvents();
$('#booking-form').onsubmit = event => {event.preventDefault(); submit(event.target, async () => {
  const f = event.target.elements, id = f.bookingId.value;
  const body = {roomId:f.roomId.value,title:f.title.value,startAt:new Date(f.startAt.value).toISOString(),endAt:new Date(f.endAt.value).toISOString()};
  if (id) body.expectedRevision = Number(f.revision.value);
  else {body.idempotencyKey = state.key; if (Number(f.weeks.value)>1) body.recurrence={weeks:Number(f.weeks.value),timezone:localZone};}
  await api('/bookings'+(id?'/'+id:''),{method:id?'PATCH':'POST',body:JSON.stringify(body)});
  $('#booking-dialog').close(); notice('Đã lưu lịch.'); await refresh();
});};
$('#cancel-booking').onclick = async () => {
  if (!confirm('Hủy lịch đã chọn? Thao tác này giải phóng khoảng giờ cho người khác.')) return;
  const button = $('#cancel-booking'); button.disabled = true;
  try {const f=$('#booking-form').elements; await api('/bookings/'+f.bookingId.value+'?expectedRevision='+f.revision.value,{method:'DELETE'}); $('#booking-dialog').close(); notice('Đã hủy lịch.'); await refresh();} catch(error) {notice(error.message,true);} finally {button.disabled=false;}
};
$('#room-form').onsubmit = event => {event.preventDefault(); submit(event.target, async () => {
  const f=event.target.elements; let hours; try {hours=JSON.parse(f.workingHours.value);} catch {throw new Error('Giờ làm việc phải là JSON hợp lệ.');}
  const body={expectedVersion:Number(f.version.value),displayName:f.displayName.value,description:f.description.value,bookable:f.bookable.checked,enabled:f.enabled.checked,deliveryPolicy:f.deliveryPolicy.value,timezone:f.timezone.value,workingHours:hours};
  await api('/rooms/'+f.roomId.value,{method:'PATCH',body:JSON.stringify(body)}); $('#room-dialog').close(); notice('Đã cập nhật cấu hình phòng.'); await refresh();
});};
setInterval(()=>{if(state.user&&!document.hidden) refresh();},30000);
api('/auth/session').then(signedIn).catch(error=>{signedOut(); if(error.message!=='Phiên đăng nhập đã hết hạn.') notice(error.message,true);});
