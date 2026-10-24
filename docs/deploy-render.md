# Deploy Office Robot NLP to Render — Phase 8C.1-C

This deploys the existing semantic HTTP API for a trusted remote PyQt5 client. It does not connect ROS, execute deliveries, dispatch tasks or change the robot repository. The JSON contract remains [integration-contract-v1.md](integration-contract-v1.md).

## Current delivery status

Deployment files and local fake-provider tests are prepared. This workspace has no configured Render/GitHub deployment tool or credentials, so no service has been created, no Render build has been observed, and there is no verified public URL. Follow the Dashboard steps below using your own accounts. Do not paste API keys or tokens into chat.

Treat these as separate verification levels:

1. Local configuration/unit tests pass.
2. Render's actual build completes.
3. An authenticated public HTTPS health check succeeds.
4. Authenticated remote session/message/monitoring checks succeed.
5. Explicit live Gemini smoke checks succeed (consume provider quota).

Only level 1 is currently verified. A passing health check alone does not prove Gemini availability, task persistence or a connected robot.

## Repository preparation

Use the **NLP repository**, not `buinhuduc2006hd-star/office-robot`. Review the existing uncommitted Phase 8B/8C application files together with this phase; a deployment needs the complete application, not only `render.yaml`.

```bash
git status --short
git diff --check
git check-ignore .env .env.local .venv/example
git ls-files data/office_rooms.json data/office_room_aliases.json
```

`.gitignore` already ignores `.env`, `.env.*`, virtual environments, bytecode and logs, while allowing `.env.example`. Do not commit real credentials in any filename, request fixture or example. Ensure both seven-room JSON files are included without modifying geometry. Include `text_conversation.py`, the `office_robot_nlp` modules and all prior batch/order modules: the factory reuses them.

Review and stage the intended application/deployment files explicitly, then commit and push to the actual NLP remote/branch, for example:

```bash
git add office_robot_nlp tests text_conversation.py batch_conversation_demo.py http_conversation_demo.py
git add render.yaml requirements.txt requirements-dev.txt .env.example docs scripts/smoke_render.py
git diff --cached --stat
git diff --cached --check
git commit -m "Prepare authenticated NLP API deployment on Render"
git push origin YOUR_DEPLOYMENT_BRANCH
```

Replace the branch name and configure the NLP remote if necessary. Review the staged content privately before pushing; do not print secret-bearing files into shared logs. These Git mutations were not performed by this agent.

## Exact Render configuration

The existing module exposes `create_api`, **not** `app`. No second HTTP application was added.

| Setting | Value |
| --- | --- |
| Service type | Web Service |
| Runtime | Python |
| Plan | Free for integration testing; select a suitable paid plan for sustained use |
| Python | `3.11.17` via `PYTHON_VERSION` |
| Root directory | Repository root (leave blank) |
| Build command | `pip install -r requirements.txt` |
| Start command | See exact command below |
| Application workers | Exactly one, explicitly set by `--workers 1` |
| Service instances | Exactly one; no autoscaling |
| Auto deploy | Off; deploy deliberately because each restart loses sessions |
| HTTP Health Check Path | Leave unset/blank; use default TCP probe |

```bash
uvicorn office_robot_nlp.http_api:create_api --factory --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log --no-proxy-headers
```

The shell expands `$PORT` in the command, not in Blueprint environment-variable values. Do not use `office_robot_nlp.http_api:app`, do not hard-code port 8000, and do not add `--reload` or extra workers. The checked-in [render.yaml](../render.yaml) defines the configuration. `numInstances: 1` and explicit worker count preserve process-local state.

The Blueprint syntax was checked against the [current Render reference](https://render.com/docs/blueprint-spec). A Render CLI/API/account-side validation and a real Render build have not been performed. Python 3.11.17 is a [released Python 3.11 security patch](https://www.python.org/downloads/release/python-31117/); Render documents support for fully qualified released Python versions via [PYTHON_VERSION](https://render.com/docs/python-version). Local verification uses the installed Python 3.11.9 interpreter, not the cloud patch version.

### Blueprint Dashboard flow

1. Push the complete reviewed NLP code to GitHub.
2. Sign in to Render and choose **New → Blueprint**.
3. Connect the NLP GitHub repository and select the deployment branch containing root `render.yaml`.
4. Review that exactly one Python Web Service is created, with the settings above.
5. Enter `GEMINI_API_KEY` and `API_ACCESS_TOKEN` into Render's secret/environment prompts. Generate a random shared bearer token in a password manager or other private local tool; store and transfer it through a secure channel.
6. Deploy. Inspect the build log for successful dependency installation and runtime logs for successful Uvicorn startup/binding. Do not print environment values or provider request bodies for debugging.
7. In the created service's **Settings → Health Checks**, verify the HTTP path is empty (TCP checks). Do not set `/health` as an unauthenticated probe.
8. Copy the actual HTTPS service URL from Render only after the service is live. Run the explicit remote checks below.

`sync: false` prompts for secrets on initial Blueprint creation. When adding a secret to an existing Blueprint, set it manually on the service's Environment page; Render does not prompt again for that addition. Do not replace the two secret entries with literal values in YAML.

### Manual Dashboard flow

If using an existing service rather than a Blueprint:

1. Choose **New → Web Service**, connect GitHub and select the **NLP** repository.
2. Select the intended deployment branch and leave Root Directory blank.
3. Select Python runtime, name the service, choose Free if available for testing, and enter the exact build/start commands above.
4. Add all fixed environment values from the following table and add both mandatory secrets through Render's Environment controls.
5. Set one instance, disable auto-deploy for controlled session resets, and keep the HTTP health-check path blank.
6. Deploy, inspect build/runtime logs, then verify the actual HTTPS endpoint.

Do not create duplicate services for the same deployment, add a database/Redis/worker, or add a persistent disk in this phase. If reusing a service with an old HTTP health-check path, clear it explicitly; omission in a Blueprint must not be assumed to remove an existing setting.

## Environment variables and secure startup

| Name | Render value / role |
| --- | --- |
| `PYTHON_VERSION` | `3.11.17` |
| `PYTHONUNBUFFERED` | `1`, timely runtime logs |
| `API_HOST` | `0.0.0.0` |
| `PORT` | Supplied by Render; used instead of local API_PORT |
| `RENDER` | Automatically `true` on Render; enables cloud startup checks |
| `WEB_CONCURRENCY` | `1`; start command also explicitly forces one worker |
| `API_ACCESS_TOKEN` | **Mandatory secret**, nonblank with no whitespace; shared with trusted GUI clients |
| `CLU_PROVIDER` | `gemini` |
| `GEMINI_MODEL` | `gemini-3.8-flash`, matching the current adapter default; configurable for your provider account |
| `GEMINI_API_KEY` | **Mandatory secret**, stored only server-side |
| `NLG_ENABLED` | `false` by default; deterministic template feedback |
| `ROOM_REGISTRY_PATH` | `data/office_rooms.json` |
| `ROOM_ALIASES_PATH` | `data/office_room_aliases.json` |
| `GEMINI_BASE_URL` | Optional; existing default `https://generativelanguage.googleapis.com/v1` |
| `GEMINI_TIMEOUT` | Optional, existing default `30` seconds |
| `NLG_PROVIDER` | Optional `gemini`; required value if optional NLG is enabled |
| `API_PORT` | Local setting only; ignored on Render |

Do not manually set `PORT=8000`. Do not use a Blueprint value such as `API_PORT: "$PORT"`: Render does not interpolate environment values. The same `create_api` factory validates public host/token configuration whether invoked by Uvicorn or the module entry point. Explicit local settings cannot bypass these checks on Render. Local default binding remains `127.0.0.1`.

Render startup validates provider configuration and static semantic files **without a provider request**. Missing/blank token, invalid host/port, missing Gemini key, invalid provider/NLG configuration, or invalid semantic files prevents startup. Checks verify configuration structure, not whether a model/key is usable with a live provider account. Confirm model availability through an explicitly invoked live smoke test; change `GEMINI_MODEL` securely in Dashboard if needed. No provider adapter or NLU semantics were changed; `.env.example` was aligned with the current adapter's already-existing model default.

Every endpoint, including `/health`, requires `Authorization: Bearer <API_ACCESS_TOKEN>` in public deployment. Missing/wrong credentials return 401 before session creation or interpretation. `/docs`, `/redoc` and `/openapi.json` stay disabled and protected. HTTP errors remain structured and omit provider internals and stack traces. Access logs are disabled; never add token or API-key logging.

Render terminates HTTPS at its edge. Remote GUI clients must use the actual `https://...onrender.com` URL and normal certificate verification. `--no-proxy-headers` keeps the existing server behavior; this API does not use client IP/scheme headers to bypass auth or create redirects. See [Render port binding](https://render.com/docs/web-services#port-binding).

## Health checks

Render's [default TCP checks](https://render.com/docs/health-checks) need no authorization header. Keep `healthCheckPath` absent for a new service and clear an old configured path in Dashboard. An unauthenticated `/health` returns 401 by design and must not be used as Render's HTTP probe. Do not exempt health from authentication or add a Gemini connectivity call.

An authorized GET `/health` returns exactly:

```json
{"status":"ok","service":"office-robot-nlp","apiVersion":"v1"}
```

TCP checks confirm an open socket; authenticated health confirms HTTP process responsiveness. Neither confirms Gemini, a robot, or task durability.

## Remote smoke test — explicitly live

The script uses only the standard library; runtime dependencies are sufficient. Run it manually from a trusted computer, outside deployment startup/build or ordinary unit tests. It sends one real semantic request, plus an optional second batch request, so it consumes Gemini quota. Authentication and monitoring calls do not invoke Gemini. Optional NLG may consume additional quota if enabled.

Set `NLP_API_URL` to the **actual** deployed HTTPS URL and `NLP_API_TOKEN` to the shared server token using your local environment/secret manager. They are client settings, not replacements for server `API_ACCESS_TOKEN`. No URL or real token is hard-coded.

```bash
python scripts/smoke_render.py
python scripts/smoke_render.py --multi --timeout 120
```

Checks: authenticated health, missing/invalid token rejection, missing session, session creation, Vietnamese single TASK_READY to `02`, READY/NOT_SENT/UNCONFIRMED, GET task and exact same-message retry. `--multi` additionally verifies three document/room associations (02/05/07), dossier HIGH, original sequence indexes, PRIORITY preview (07/02/05), and GET batch consistency. The script rejects insecure URLs and does not forward bearer tokens through redirects. Output contains concise safe pass/fail labels, not credentials or response bodies.

The smoke script is not called by the Blueprint, API startup, compilation or ordinary tests. Unit tests exercise it only against a fake local in-process API. A request may continue after a client timeout: retry the **same messageId and exact text**, not a newly generated message for that intent.

### Complete Python client request flow

This example uses `requests` as a GUI/client dependency (install it in the client environment if needed); the server and standard-library smoke script do not require it. Set `NLP_API_URL`/`NLP_API_TOKEN` privately before invocation. This flow makes **two live provider requests**, so invoke deliberately:

```python
import os
import uuid
import requests

base = os.environ["NLP_API_URL"].rstrip("/")  # actual HTTPS URL
client = requests.Session()
client.headers["Authorization"] = "Bearer " + os.environ["NLP_API_TOKEN"]

health = client.get(base + "/health", timeout=120, allow_redirects=False)
health.raise_for_status()
session = client.post(base + "/v1/sessions", json={}, timeout=120, allow_redirects=False)
session.raise_for_status()
sid = session.json()["sessionId"]
path = base + f"/v1/sessions/{sid}/messages"

payload = {"messageId": str(uuid.uuid4()), "text": "Giao tài liệu đến phòng 2."}
single = client.post(path, json=payload, timeout=120, allow_redirects=False)
single.raise_for_status()
task_id = single.json()["batch"]["tasks"][0]["taskId"]
task = client.get(base + f"/v1/tasks/{task_id}", timeout=120, allow_redirects=False)
task.raise_for_status()
retry = client.post(path, json=payload, timeout=120, allow_redirects=False)
retry.raise_for_status()
assert retry.json() == single.json()

batch_payload = {
    "messageId": str(uuid.uuid4()),
    "text": "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hồ sơ trước.",
}
multi = client.post(path, json=batch_payload, timeout=120, allow_redirects=False)
multi.raise_for_status()
batch_id = multi.json()["batch"]["batchId"]
batch = client.get(base + f"/v1/batches/{batch_id}", timeout=120, allow_redirects=False)
batch.raise_for_status()

missing = client.post(base + "/v1/sessions/missing-session/messages", json=payload,
                      timeout=120, allow_redirects=False)
assert missing.status_code == 404
assert missing.json()["error"]["code"] == "SESSION_NOT_FOUND"
invalid = requests.get(base + "/health", headers={"Authorization": "Bearer deliberately-invalid"},
                       timeout=120, allow_redirects=False)
assert invalid.status_code == 401
```

`json=payload` produces standards-compliant JSON for Vietnamese. UTF-8 bytes and Unicode-escaped JSON are both covered by local tests. Do not remove accents to work around Git Bash quoting/encoding. For terminal curl, prefer a UTF-8 JSON file and `--data-binary @request.json`; see the shared contract for full schemas and curl/PowerShell examples. Invalid JSON/encoding maps to 400/422, while a provider failure maps to 503.

## In-memory state and GUI handoff

Sessions, drafts, tasks, batches and idempotency entries exist only in the current process. Restart, redeploy and idle spin-down can lose all of them; a new process cannot recover a pending delivery. [Render Free](https://render.com/docs/free) currently spins down after about 15 minutes idle and may take about a minute to start again. The Free plan is intended for testing. Paid compute avoids that idle behavior but does not make application memory durable across restarts.

Use one worker and one instance; no autoscaling or load balancing among independent application processes. Redeploys may replace the process, so treat them as conversation resets. There is no persistence simulation or automatic resubmission of lost delivery intents.

Once actual remote verification passes, give the GUI team:

```text
NLP_API_URL = <actual verified HTTPS service URL>
NLP_API_TOKEN = <shared through a secure channel>
```

The GUI must authenticate, create one session per conversation, retain sessionId for clarification turns, generate a unique messageId per user message and reuse it with the exact original text on retry. Handle 503/cold starts with suitable timeouts/backoff; keep network requests outside the PyQt5 main thread. On SESSION_NOT_FOUND, inform the user that the conversation was lost and create a new session when appropriate; do not claim recovery or automatically duplicate the old delivery.

Render the unchanged unified single/batch envelope and distinguish original order from the proposed orderPreview. TASK_READY/BATCH_READY imply semantic validity only. New tasks stay READY, NOT_SENT and UNCONFIRMED; never trigger ROS/navigation from those statuses. Never give the GUI the Gemini API key. No GUI code was changed in this phase.

## Troubleshooting

| Symptom | Check / action |
| --- | --- |
| `ModuleNotFoundError` | Correct repository/root directory; commit all package/application modules; build with `pip install -r requirements.txt`; do not install only dev fragments |
| Wrong import/factory path | Use `office_robot_nlp.http_api:create_api --factory`; there is no module-level `app` |
| Python incompatibility | Set `PYTHON_VERSION=3.11.17`; inspect actual build interpreter and wheel installation output |
| Missing Gemini key / startup config error | Set GEMINI_API_KEY as a Render secret; verify model/timeout/base URL names without printing their values |
| Missing public token / startup error | Set nonblank API_ACCESS_TOKEN, API_HOST=0.0.0.0; do not bypass factory security checks |
| Port binding failure | Bind 0.0.0.0 and shell-expanded `$PORT`; clear conflicting custom start command; do not use local API_PORT |
| Deployment start failure | Compare the build/start commands exactly; confirm both semantic JSON files exist and configured paths match case on Linux; inspect safe runtime errors |
| Unhealthy deploy despite running process | Remove an old `/health` HTTP probe; keep TCP checks rather than removing auth |
| HTTP 401/403 | Application wrong/missing token yields 401; verify Authorization Bearer format and matching shared token; an edge/proxy 403 may have a different source |
| HTTP 400/422 | Serialize JSON with a client library; send UTF-8/Content-Type application/json; inspect required field names, nonblank strings, extra fields and shell quoting; slash commands are not API controls |
| Gemini upstream 429/503 | API returns safe 503 PROVIDER_UNAVAILABLE; wait/back off and retry the same ID/text; check provider quota/model access privately, not by exposing credentials |
| Cold-start timeout/HTML page | Wait for service start and repeat health first; increase client timeout, keep UI responsive; live POST retries must retain ID/text |
| SESSION_NOT_FOUND / unknown task after restart | In-memory state is gone; inform user and establish a new session, without claiming recovery |
| Duplicate/inconsistent sessions | Restore one worker/instance, disable reload/autoscaling, retain session/message IDs; a second process has separate memory |

## Local verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_render_deployment.py" -v
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.venv\Scripts\python.exe -m compileall office_robot_nlp tests
.venv\Scripts\python.exe -m pip check
git diff --check
```

The deployment suite uses fake parsers and local HTTP clients, including UTF-8/escaped Vietnamese, auth before provider access, real Uvicorn CLI argument parsing, safe configuration failures and the smoke-script flow. Existing semantic, monitoring, ordering and CLI regression tests remain applicable. No real provider request is part of these checks. Scope ends at secure deployment readiness/verification; there is no robot execution phase here.
