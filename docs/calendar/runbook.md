# Development runbook

Run from the repository root on `feature/office-room-calendar`. Python 3.11+, PostgreSQL 17 with `btree_gist`, optional Docker Desktop. Do not point these setup/test commands at production or Integration v1 staging. Existing `.env` files are not automatically loaded.

## Dedicated local database

PowerShell example, persistent development volume and loopback-only port. Trust authentication here is for local development only; remote environments need private authenticated PostgreSQL connectivity.

```powershell
docker volume create office-room-calendar-dev-data
docker run -d --name office-room-calendar-dev -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=office_calendar_dev -p 127.0.0.1:55441:5432 -v office-room-calendar-dev-data:/var/lib/postgresql/data postgres:17-alpine
docker exec office-room-calendar-dev pg_isready -U postgres -d office_calendar_dev

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-calendar.txt
$env:CALENDAR_DATABASE_URL='postgresql://postgres@127.0.0.1:55441/office_calendar_dev'
$env:CALENDAR_SESSION_SECRET = .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
$env:CALENDAR_PUBLIC_ORIGIN='http://127.0.0.1:8000'
$env:CALENDAR_TIMEZONE='Asia/Ho_Chi_Minh'
$env:CALENDAR_ENABLED='true'
$env:CALENDAR_NLP_ENABLED='false'
$env:API_HOST='127.0.0.1'
$env:API_PORT='8000'
```

Store the generated session secret privately and reuse it across normal application restarts. Do not print or commit real credentials. Database name must contain `calendar` for migration safety; there is no fallback to `DATABASE_URL` or an Integration database.

## Explicit migration and provisioning

```powershell
.venv\Scripts\python.exe -m office_room_calendar.admin migrate
.venv\Scripts\python.exe -m office_room_calendar.admin user --email admin@example.org --name 'Calendar Admin' --role ADMIN
.venv\Scripts\python.exe -m office_room_calendar.admin user --email operator@example.org --name 'Operator' --role USER
```

The user command securely prompts for a password (15–256 characters). An automation may supply `CALENDAR_USER_PASSWORD` privately, then remove it. No self-registration, password reset or public role-grant API. Keep migration/provisioning access restricted. Migration is transactional/checksummed and repeatable; it preserves existing room configuration. Provisioning duplicate accounts/tokens fails safely.

Provision robot token independently:

```powershell
$env:CALENDAR_ROBOT_TOKEN = .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
.venv\Scripts\python.exe -m office_room_calendar.admin robot --name office-robot-read
```

Transfer that token to the authorized robot privately. Only its digest is stored in the DB. Use distinct session secret, robot token and backend API_ACCESS_TOKEN; CLI rejects shared credentials. Revoke a compromised credential through a restricted DB administration channel by setting the relevant robot token's enabled=false; disabled users likewise lose session access immediately. Account/robot lifecycle management UI is outside this version.

## Backend and website

```powershell
.venv\Scripts\python.exe -m office_robot_nlp.http_api
```

Open `http://127.0.0.1:8000/calendar/` using that exact origin (localhost is a different configured Origin). Log in as Admin, open **Quản lý phòng**, and explicitly approve **Đã được duyệt cho phép đặt lịch** for actual meeting rooms. All seven seed false. Configure delivery policy independently; policy changes do not make a room bookable.

Use dashboard/calendar to create, view, edit and cancel; day/week/month views and weekly create are available. UI uses the browser's labelled local timezone; policies/recurrence carry an explicit IANA zone and the DB stores UTC. Revoked/expired sessions return to login. Network failures remove stale dashboard evidence; loading states disable repeated submits. A refresh preserves the selected room in an open booking form.

Calendar website/API works locally without a live language provider. To exercise the existing NLP routes, configure the provider exactly as before. Optional calendar enrichment: set `CALENDAR_NLP_ENABLED=true` and restart. The flag never changes registration, priority, strict-order or clarification behavior. Disable it to retain the exact old message contract.

Calendar is off by default. The existing NLP-only dependency/start profile still works. One worker remains required for existing in-memory NLP sessions; Calendar state itself is in PostgreSQL.

### Frontend assets

Generated standard FullCalendar 6.1.22 assets + Vietnamese locale and MIT license are included. Python deployment needs no Node or external CDN. Only if regenerating assets:

```powershell
Push-Location office_room_calendar/web
npm.cmd ci --ignore-scripts --no-audit --no-fund
npm.cmd run build
Pop-Location
```

`package-lock.json` pins dependencies/integrity. Node modules are ignored. Static browser requests require no backend bearer. Custom CSS/JS use native forms/dialogs/DOM; no frontend app server or framework.

## Full tests on a disposable database

These tests create/drop random schemas. They require an explicit loopback database whose name contains both `calendar` and `test`. Never use a valuable development/production database, even if its name matches. Without the test variable, PostgreSQL tests are skipped; that is not a full verification.

```powershell
docker run --rm -d --name office-room-calendar-pg-test -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=office_calendar_test -p 127.0.0.1:55440:5432 postgres:17-alpine
docker exec office-room-calendar-pg-test pg_isready -U postgres -d office_calendar_test
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:CALENDAR_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:55440/office_calendar_test'
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
.venv\Scripts\python.exe -m compileall -q office_room_calendar office_robot_nlp tests scripts
node --check office_room_calendar/web/app.js
git diff --check
```

Optional actual-browser suite (run before stopping the test DB):

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-calendar-browser.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m scripts.calendar_browser_smoke
docker stop office-room-calendar-pg-test
```

Browser suite starts/stops its own loopback application servers, uses a fresh schema, never calls a language provider and drops only its test schema. Screenshots/logs are under ignored `.venv/calendar-browser/`. Separate Robot-client instructions: [robot guide](robot-integration.md).

## Future staging checklist (not executed)

After owner approval, use an independent staging service/DB, build with `pip install -r requirements-calendar.txt`, export CALENDAR_* variables with the exact HTTPS public origin and independent credentials, migrate explicitly, provision users/token, then start the existing one-worker factory. On Render, `RENDER=true` requires an explicit HTTPS Calendar origin. Keep DB private; do not expose its URL or backend token to the website. Run the acceptance scenarios in the robot guide before enabling NLP enrichment for an existing HMI. Existing `render.yaml` was not modified and no deployment occurred in this task.
