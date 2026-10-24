# Phase 8C.1-C — delivery report

## Deployment status

**Deployment-ready configuration is prepared and locally verified. Actual deployment has not occurred.** No authorized Render/GitHub deployment tools, CLI or deployment credentials were available in this session. No GitHub push or Render service mutation was performed, no cloud build was observed, and no public HTTPS URL is claimed.

| Verification level | Status |
| --- | --- |
| Local automated tests | Passed: 329/329 |
| Actual local Uvicorn process with Render-style configuration | Passed: PORT binding, missing/invalid auth rejection and authenticated health |
| Render-side Blueprint validation/build | Not run; fields/syntax reviewed against current Render documentation |
| Actual public Render HTTPS health | Not run; no deployed endpoint available |
| Remote authenticated session/message/monitoring tests | Not run |
| Real Gemini/provider smoke | Not run; requires explicit invocation and consumes quota |

## Files created

- `render.yaml`: one Python Web Service, one instance, one worker, no HTTP health-check path, no embedded secrets.
- `scripts/smoke_render.py`: explicitly invoked live HTTPS smoke test, standard library only, optional three-document batch check, safe concise output.
- `tests/test_render_deployment.py`: 27 focused tests using fake providers and local HTTP clients.
- `docs/deploy-render.md`: repository preparation, exact Blueprint/manual Dashboard setup, environment/secrets, remote client examples, troubleshooting and GUI handoff.
- This report.

## Files modified

- `office_robot_nlp/http_api.py`: Render-aware PORT/host parsing and mandatory public token checks applied in the existing factory, local-only provider/static-map startup validation, explicit no-reload module startup.
- `.env.example`: Render/client environment guidance; sample model aligned with the current Gemini adapter's already-existing `gemini-3.8-flash` default.
- `docs/integration-contract-v1.md`: Render factory reference and configuration notes, preserving all HTTP fields.

`requirements.txt`, `requirements-dev.txt`, `.gitignore`, session bridge, DTOs, NLU adapters and domain semantics were inspected and did not need deployment changes. Existing work in the repository, including the current adapter default, was preserved. No robot repository or semantic-map geometry was modified.

## Exact Render configuration

| Setting | Value |
| --- | --- |
| Type/runtime | Web Service / Python |
| Plan | Free for integration testing |
| Python target | 3.11.17 (released security patch, pinned by PYTHON_VERSION) |
| Local verified Python | 3.11.9 |
| Build | `pip install -r requirements.txt` |
| Root | Repository root |
| Instances/workers | One instance, one application worker |
| Reload | Disabled |
| Auto deploy | Off; manual deployments because process state is disposable |
| Health | Render default TCP probe; HTTP health-check path unset |

Exact start command:

```bash
uvicorn office_robot_nlp.http_api:create_api --factory --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log --no-proxy-headers
```

There is no module-level `app`. This command invokes the existing `create_api` factory and uses Render's PORT. The factory enforces the same public binding/token boundary, including when called by Uvicorn. On Render it also validates provider/NLG configuration and static map files without a network/provider health request. Local loopback startup remains compatible.

Blueprint fixed variables: PYTHON_VERSION, PYTHONUNBUFFERED, API_HOST, WEB_CONCURRENCY, CLU_PROVIDER, GEMINI_MODEL, NLG_ENABLED, ROOM_REGISTRY_PATH, ROOM_ALIASES_PATH. Secret prompts: GEMINI_API_KEY and API_ACCESS_TOKEN (`sync: false`). Render supplies RENDER=true and PORT. Optional existing variables: GEMINI_BASE_URL, GEMINI_TIMEOUT, NLG_PROVIDER. Local API_PORT is ignored on Render. Remote smoke client variables: NLP_API_URL and NLP_API_TOKEN.

Missing/wrong authorization returns 401 before session/parser access. Authenticated health preserves the exact service-only JSON contract. All endpoints, including health and disabled documentation paths, stay protected. No Gemini API key goes to the GUI. Render uses TCP probes because its HTTP probe does not carry the shared bearer token; authentication was not relaxed.

## Verification results

Run with `.venv\Scripts\python.exe`:

| Command/check | Result |
| --- | --- |
| `python -m unittest discover -s tests -p "test_*.py" -v` | 329 tests passed, including all 302 existing tests |
| `python -m unittest discover -s tests -p "test_render_deployment.py" -v` | 27 tests passed |
| Existing HTTP suite | 40 tests passed |
| `python -m compileall office_robot_nlp tests` | Passed; scripts additionally compiled |
| `python -m pip check` | No broken requirements |
| `pip install -r requirements.txt` | Passed against the existing local virtual environment; not a fresh Linux/Render build |
| `git diff --check` | Passed; only existing CRLF normalization notices |
| Existing offline HTTP demo | Passed |
| Existing batch CLI demo | Passed: four batches, nine READY tasks |
| Existing text CLI `--list-rooms` | Passed: seven configured rooms |
| Real local Uvicorn factory process | Passed: Render-style public bind/PORT, absent/wrong token 401, correct-token health 200; stopped afterward |

Tests use fake parsers/providers and prohibit actual provider calls. Vietnamese text is tested both as raw UTF-8 JSON and Unicode escapes, preserving the exact text delivered to the parser. The existing evaluation test deliberately prints its offline provider failure case and passes. Starlette still emits its existing non-failing HTTPX deprecation warning; no tests are skipped and warnings are not hidden.

Coverage includes real Blueprint-command Uvicorn CLI parsing under a conflicting WEB_CONCURRENCY value (explicit worker count remains one), importable factory path, startup validation/secret-safe errors, auth before provider access, disabled docs, single/multi serialization, original indexes and priority preview, multi-turn/Option A behavior, provider error mapping, current independent monitoring and absence of robot execution endpoints. The live smoke script is tested only against a local fake authenticated API, never a live provider.

## Handoff, limitations and remaining steps

Follow [deploy-render.md](deploy-render.md): push the reviewed complete NLP repository, create the Blueprint or Web Service, select the deployment branch, privately configure GEMINI_API_KEY and API_ACCESS_TOKEN, keep TCP health checks, deploy and inspect build/runtime logs. Once live, use the actual HTTPS URL and run `python scripts/smoke_render.py` (optionally `--multi`) explicitly with NLP_API_URL/NLP_API_TOKEN configured locally. Record the separate remote/provider verification levels before claiming deployment success.

The GUI team receives the actual verified HTTPS URL and shared token through a secure channel. Use the existing JSON contract, preserve sessionId across turns and messageId/text across retries, keep network calls off the Qt main thread, and handle cold starts/session loss honestly. READY or a proposed order preview must not trigger navigation or imply physical acceptance.

Sessions, pending drafts, tasks, batches and idempotency are memory-only. Restart/redeploy/idle spin-down can discard them; another process cannot recover them. Use one process/instance with no autoscaling. The cloud Python patch, real model/key access and remote HTTPS behavior remain unverified until an actual deployment is run. No scheduling, ROS, navigation, dispatch, persistent database, worker queue, GUI changes or new chatbot behavior was added. Work stops at Phase 8C.1-C.

## References checked

- [Render Blueprint reference](https://render.com/docs/blueprint-spec)
- [Render Python versions](https://render.com/docs/python-version)
- [Render environment variables](https://render.com/docs/environment-variables)
- [Render TCP/HTTP health checks](https://render.com/docs/health-checks)
- [Render Web Service port binding](https://render.com/docs/web-services#port-binding)
- [Render Free limitations](https://render.com/docs/free)
- [Python 3.11.17 release](https://www.python.org/downloads/release/python-31117/)
