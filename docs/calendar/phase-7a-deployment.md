# Phase 7A — Owner deployment and smoke verification

Target supplied by owner: `https://office-robot-nlp-1.onrender.com`. **These are instructions, not executed remote changes.** The agent has not pushed, deployed, read production credentials, migrated a remote DB or queried live data. Only the NLP repository changes. Phase 7A requires no schema migration, database, account/token rotation or new service.

## 1. Review and publish the intended source (owner action)

Base: `feature/office-room-calendar` at `a1cedc2571ffa313b9c1ca066af269ffa92fbadb`. Working branch: `feature/calendar-server-handoff`. Review only its server diff and the [test report](phase-7a-server-report.md). The unrelated pre-existing untracked directories/files are not release content.

After owner review/approval, commands from repository root:

```bash
git switch feature/calendar-server-handoff
git diff --check
git add data/office_room_aliases.json office_room_calendar/integration.py office_room_calendar/availability.py tests/test_calendar.py tests/test_calendar_execution_contract.py tests/test_semantic_map.py docs/calendar/
git diff --cached --check
git diff --cached --stat
git commit -m "feat(calendar): add preliminary execution contract and robot handoff"
git push -u origin feature/calendar-server-handoff
git rev-parse HEAD
```

Do not push/merge main or Integration v1 for this staging test. Record the reviewed commit SHA. These commands intentionally exclude `.env`, credentials, developer caches and unrelated untracked work.

## 2. Confirm existing staging configuration privately

In Render Dashboard, select the existing web service whose public URL matches the target, and confirm intended workspace/repo. Keep auto deploy off for a controlled release. Preserve provider/DB credentials, session secret, policies, bookings and user/token records.

Expected build/start configuration from this repository:

```text
Build: pip install -r requirements-calendar.txt
Start: uvicorn office_robot_nlp.http_api:create_api --factory --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log --no-proxy-headers
```

`render.yaml` remains the existing NLP-only profile with requirements.txt; it was not changed. If staging already overrides the build command, verify the Calendar requirement file is installed. If the service is Blueprint-managed, reconcile changes in its intended configuration source before the next Blueprint sync rather than relying on an unexplained Dashboard override.

| Key | Expected |
| --- | --- |
| API_HOST | 0.0.0.0 |
| API_ACCESS_TOKEN | Existing private backend bearer |
| CLU_PROVIDER and provider settings | Existing valid configuration; no model/provider switching |
| CALENDAR_ENABLED | true |
| CALENDAR_NLP_ENABLED | true |
| CALENDAR_PUBLIC_ORIGIN | https://office-robot-nlp-1.onrender.com (no trailing slash/path) |
| CALENDAR_DATABASE_URL | Existing dedicated Calendar DB, privately configured; no value in this document |
| CALENDAR_SESSION_SECRET | Existing distinct ≥32-character secret, reused |
| CALENDAR_TIMEZONE | Existing policy/display zone, normally Asia/Ho_Chi_Minh |
| Timing settings | Keep existing values; defaults travel 420s, handoff 180s, evidence 30s |

The robot read token must already be provisioned in the Calendar token table and supplied to the Robot privately. Runtime authentication reads its digest there, not directly from CALENDAR_ROBOT_TOKEN. If prerequisites are absent, stop and complete the separately reviewed initial Calendar setup; do not run migrations against an unidentified live database for this release.

## 3. Deploy reviewed source manually (owner action)

Save pending environment edits without deploying if preparing a single controlled release. In the intended staging service's Deploys page select **Manual Deploy → Deploy a specific commit** and provide the reviewed pushed SHA; verify branch/repo/source before triggering. This Dashboard method disables auto deploy; confirm it remains off. Wait for that specific deploy to become live, then run behavior checks; a queued/accepted deploy is not completion. Render documents these controls in [deploys](https://render.com/docs/deploys.md) and [environment changes](https://render.com/docs/configure-environment-variables.md).

Keep the existing TCP health-check profile: this app's `/health` requires the backend bearer, so do not configure an unauthenticated Render HTTP check there. Verify the authenticated endpoint yourself; the protected application route is unchanged. [Render health-check documentation](https://render.com/docs/health-checks.md).

No migration/pre-deploy command is needed for Phase 7A. Deploying/restarting the existing one-worker app loses in-memory NLP sessions; coordinate staging tests accordingly. Existing Calendar data remains in PostgreSQL. A normal deploy is needed for pending configuration, not merely a restart of the old live build.

## 4. Smoke-test from Git Bash / Linux (owner action)

Privately export API_ACCESS_TOKEN and CALENDAR_ROBOT_TOKEN into the test terminal. Do not paste them into tickets, commands recorded in documentation or shell tracing. Commands below reference variables only.

```bash
BASE='https://office-robot-nlp-1.onrender.com'
curl --fail --silent --show-error "$BASE/health" -H "Authorization: Bearer $API_ACCESS_TOKEN"
SESSION_JSON=$(curl --fail --silent --show-error -X POST "$BASE/v1/sessions" -H "Authorization: Bearer $API_ACCESS_TOKEN" -H 'Content-Type: application/json' -d '{}')
SID=$(printf '%s' "$SESSION_JSON" | python -c 'import json,sys; print(json.load(sys.stdin)["sessionId"])')
curl --fail --silent --show-error -X POST "$BASE/v1/sessions/$SID/messages" -H "Authorization: Bearer $API_ACCESS_TOKEN" -H 'Content-Type: application/json' -d '{"messageId":"phase7a-single-001","text":"Giao tài liệu đến phòng 01."}'
```

Expect 201 session then 200 TASK_READY; single task inside batch.tasks, canonical destinationId 01, one roomAvailability PRELIMINARY and one executionDecisions pending/allowed=false with the exact same taskId. FREE/BUSY/UNKNOWN depend on actual Calendar data; do not require FREE regardless of policy. UNKNOWN is not successful verification of DB readiness. Missing arrays means the reviewed source/feature configuration is not active.

Repeat the same messageId/text in that session: exact same body and taskId, no extra task or fresh Calendar lookup. Test a new session with the batch text in [fixture](examples/nlp-batch.json); match every array item by taskId and preserve preview/sequence/priority. These NLP smoke calls only prepare server-side tasks; no Robot command is sent by this server.

Use existing read-only client for final API smoke, choosing a fresh synthetic future window for the HTTP test (a real Robot must use its actual ETA/handover window):

```bash
ARRIVAL_START=$(python -c 'from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat())')
python -m scripts.calendar_fake_robot --base-url "$BASE" --room 01 --arrival-start "$ARRIVAL_START" --duration-seconds 300
python -m scripts.calendar_fake_robot --base-url "$BASE" --room 01
```

First response: ARRIVAL_WINDOW, matching room/window, all existing evidence fields and policy-consistent result. Second: PRELIMINARY with no deliveryAllowed=true. Neither call modifies the cached NLP executionDecisions. For controlled FREE/BUSY/policy scenarios use the disposable local suite or an explicitly coordinated staging test schedule; do not overwrite live bookings to force a result.

Use invalid token to confirm 401, never expose a real token when reporting failures. DB/token/provider failures must remain structured errors or UNKNOWN, never permission. Check validUntil/checkedAt/version, UTC offsets, and same-room task association. Record commit/deploy ID, timestamp, sanitized status/result and test cases. Do not report Robot/Nav2 E2E success from these server checks.

## 5. Exit criteria / fallback

Accept only after the intended deploy is live and both authenticated contracts are verified. If arrays, task associations, policies, auth or freshness differ, keep Robot execution disabled in the Robot team's test setup and investigate server/configuration first. Owner may turn off CALENDAR_NLP_ENABLED and deploy to restore the legacy NLP response while leaving the read API available; absence of enrichment still never means execution permission. No automatic rollback, credential change or data migration is part of this task.
