# Diagnose Gemini failures behind the public 503

## Confirmed findings and limits

The actual path is:

```text
FastAPI POST /v1/sessions/{sessionId}/messages
  -> NlpSessionBridge.handle_user_message (session lock / transaction)
  -> TextConversationApplication.process_text
  -> GeminiLanguageUnderstandingParser.parse
  -> _http_request (urllib POST, configured GEMINI_BASE_URL + /interactions)
  -> decode response envelope, extract text, decode/validate structured CLU result
```

The request uses GEMINI_MODEL, the existing semantic instructions and structured-output schema, and the Gemini key in the x-goog-api-key header. No request format, API version, model default or NLU semantics was changed by this diagnostic fix.

An upstream HTTP failure becomes GeminiProviderRequestError. Network/timeouts also become that exception. A malformed response becomes MalformedGeminiOutputError; invalid structured CLU becomes InvalidGeminiResultError. All inherit GeminiAdapterError. `process_text` catches those errors and returns ProviderFailure. The bridge converts that into BridgeError(PROVIDER_UNAVAILABLE); FastAPI maps it to HTTP 503. The bridge transaction preserves earlier records/pending state and reserves the same message ID/text for retry. No fallback provider or successful task is fabricated.

Before this change, HTTP errors sometimes retained upstream status/code inside the exception, but none was logged at the adapter boundary. Different failures therefore looked identical to the HTTP client. `/health` tests the HTTP service, and successful session creation initializes configuration; neither exercises Gemini interpretation. Changing a model alone cannot establish which failure is occurring.

**Production root cause remains unknown.** There is no production log or live upstream response available in this session. Mocked tests reproduce that upstream 400/401/403/404/429/503, timeout, connection errors and malformed output can all yield the same public failure; those tests do not prove which one affects the deployed service. Do not infer an unavailable model or upstream 503 just from the application's 503.

## Safe diagnostics

Logger: `office_robot_nlp.gemini_adapter`, level WARNING. Each failed Gemini parse emits one single-line JSON record before the error is caught by the application. Standard Uvicorn/default Python logging sends WARNING records to server stderr without enabling DEBUG_CONVERSATION or access logs. Successes do not emit failure records.

Example **illustrative** record, not a production observation:

```json
{"exception_class":"HTTPError","model":"configured-model","provider":"gemini","provider_code":400,"provider_status":"INVALID_ARGUMENT","summary":"invalid_model_or_request","upstream_http_status":400}
```

Only these seven fields are logged. No exception object/traceback, provider message, complete response/request body, key/header, URL, user text, session/message ID or prompt is attached to the log record. Summary strings are fixed categories, not raw provider text. Canonical provider statuses/codes are allowlisted; untrusted string values are replaced with null. Model names are bounded and validated; unsafe values or embedded configured Gemini/server-token secrets are redacted. Status is null when unavailable, including network failures or injected providers that do not supply an HTTP status. Actual successful HTTP status is retained through response decoding, so a malformed HTTP-200 body is distinguishable from an HTTP error.

| summary | Evidence / investigation |
| --- | --- |
| `invalid_model_or_request` | Upstream 400: verify configured model, endpoint/API version and request/schema compatibility; the category alone does not distinguish these causes |
| `api_key_invalid_or_missing` | Upstream 401: verify the server-side key configuration privately |
| `permission_denied_or_api_key_rejected` | Upstream 403: verify project/key permissions and service/model access privately |
| `model_or_endpoint_not_found` | Upstream 404: check both model and API endpoint/version; it is not proof the model alone is wrong |
| `rate_limit_or_quota_exhausted` | Upstream 429: inspect quota/rate limits in the provider console; these metadata alone may not distinguish the two |
| `upstream_unavailable` | Upstream 503: upstream service unavailable for this request |
| `connection_timeout` | Direct timeout or timeout wrapped by URLError; no HTTP failure status implied |
| `connection_error` | Network/DNS/TLS/socket error; inspect the safe exception class and service networking, without logging raw exception strings |
| `response_json_decode_failed` | Response bytes are invalid JSON/UTF-8; successful HTTP status is retained when known |
| `unexpected_response_shape` | Response is not the expected completed Interactions envelope/text; inspect response-contract compatibility in a controlled investigation |
| `structured_output_invalid` | Model text fails existing JSON/CLU validation; no invalid text is logged |
| `upstream_http_error` | Another upstream HTTP status |
| `unexpected_provider_error` | Other exception at the request boundary; class is logged without its message |

The public API response is unchanged for all existing provider failures:

```json
{"error":{"code":"PROVIDER_UNAVAILABLE","message":"Language provider is temporarily unavailable. Retry the same message."}}
```

The existing optional NLG/template fallback is unchanged; these diagnostics instrument the Gemini NLU parser whose failures produce the reported 503. No automatic retries, provider switching, contract or registration changes were added.

## Exact deployment steps

1. Review and commit these files in the NLP repository: `office_robot_nlp/gemini_adapter.py`, `tests/test_gemini_diagnostics.py`, `docs/diagnose-gemini-503.md` and the link added to `docs/deploy-render.md`. Push to the branch configured for the existing Render NLP service. Do not push secrets or change the robot repository.
2. In Render, open the existing service and verify its deployment branch matches that commit. Auto-deploy is off in the checked-in Blueprint: choose **Manual Deploy → Deploy latest commit**. A redeploy discards all memory-only sessions/tasks/idempotency records.
3. Keep the existing build command `pip install -r requirements.txt` and start command:

   ```bash
   uvicorn office_robot_nlp.http_api:create_api --factory --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log --no-proxy-headers
   ```

4. Preserve API_ACCESS_TOKEN and GEMINI_API_KEY as Render secrets, existing provider/model/environment configuration, one instance/worker and default TCP health checks. No new dependency or logging environment variable is required. Do not enable debug/body/access logging.
5. Wait for the **new commit** to become live, then open its runtime Logs view. Confirm the deployed revision before comparing new diagnostic records. This session did not deploy or access Render logs.

## Exact production verification

1. Privately configure NLP_API_URL to the actual HTTPS service URL and NLP_API_TOKEN to the shared server token on a trusted client. Do not put secrets in chat, command output, source or log searches.
2. Run `python scripts/smoke_render.py` **explicitly**. It sends one real single-document interpretation request and consumes provider quota. It creates a new session, checks authenticated health, verifies auth rejection, and verifies task/read/idempotency if interpretation succeeds. Do not run `--multi` until the single case is understood.
3. If the message returns 503, inspect the corresponding new WARNING record in Render's runtime logs. Use provider, configured model, upstream_http_status, provider_status/code, exception_class and summary to choose the next investigation from the table above. Only share this sanitized record if further help is needed; do not share raw upstream bodies, environment dumps or tokens.
4. To retry the **same failed message** within a still-running session, retain its exact sessionId, messageId and text in your client. A successful retry must be returned unchanged on another identical POST, and GET task must show READY / NOT_SENT / UNCONFIRMED. A successful health/session call alone is not this verification. Running the smoke script again creates a new test session; it does not retry a previous session's message after a restart.
5. After a confirmed underlying configuration/provider fix, run `python scripts/smoke_render.py --multi` explicitly to verify three-document semantics/priority preview and GET batch. Keep original sequence indexes and physical monitoring defaults. These live checks are not run by ordinary tests, build or startup.

If the new deployed revision produces no diagnostic record for the reported failure, check whether CLU_PROVIDER is actually gemini and whether custom logging configuration suppresses this logger's WARNING level; also verify the error came from this running revision. Do not guess a model or request-format correction without evidence. Authentication failures do not invoke the parser and should not produce these diagnostics.

## Local verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_gemini_diagnostics.py" -v
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.venv\Scripts\python.exe -m compileall office_robot_nlp tests
git diff --check
```

The diagnostic tests mock the actual urllib response boundary for 400/401/403/404/429/503, direct/wrapped timeouts, connection failures, malformed UTF-8/JSON/envelopes and invalid structured output. They assert log field limits and secret/body/user-text exclusion. A full HTTP-path test verifies all six HTTP failures retain the exact public 503, preserve previous tasks and pending draft, and permit one atomic successful registration on same-message retry. No live provider request is made.

Verified locally: **19 diagnostic tests passed; 348 total tests passed**, including all 329 existing tests. `compileall office_robot_nlp tests`, `pip check` and `git diff --check` passed (only a Git CRLF normalization notice). The existing non-failing Starlette HTTPX deprecation warning remains. No Render redeploy, remote health verification or live Gemini call was performed for this fix.
