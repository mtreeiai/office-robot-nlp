"""Mock the actual urllib boundary; no provider requests or real credentials."""

import copy
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from http_conversation_demo import SINGLE, UNKNOWN_ROOM, demo_responses
from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.gemini_adapter import (
    GeminiAdapterError, GeminiConfig, GeminiLanguageUnderstandingParser,
)
from office_robot_nlp.http_api import ApiSettings, create_api
from office_robot_nlp.semantic_map import load_semantic_registry
from office_robot_nlp.session_bridge import NlpSessionBridge
from text_conversation import TextConversationApplication


LOGGER = "office_robot_nlp.gemini_adapter"
API_KEY = "test-private-gemini-key"
TOKEN = "test-private-bearer-token"
USER_TEXT = "Giao hồ sơ riêng tư cho anh Nam đến phòng 2."
BODY_MARKER = "private-response-body-marker"


def http_error(status, provider_status=None, *, body=None):
    if body is None:
        body = json.dumps({"error": {
            "code": status, "status": provider_status,
            "message": f"{BODY_MARKER} {API_KEY} Authorization: Bearer {TOKEN} {USER_TEXT}",
        }}).encode("utf-8")
    return urllib.error.HTTPError("https://test.invalid/interactions?key=" + API_KEY, status,
                                  "private-reason-" + TOKEN, {"Authorization": TOKEN}, io.BytesIO(body))


def http_response(body, status=200):
    response = Mock()
    response.status = status
    response.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def model_response(payload):
    return {"status": "completed", "steps": [{"type": "model_output", "content": [
        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
    ]}]}


class GeminiDiagnosticTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"API_ACCESS_TOKEN": TOKEN, "NLG_ENABLED": "false"})
        environment.start()
        self.addCleanup(environment.stop)
        self.config = GeminiConfig(API_KEY, model="configured-test-model", endpoint="https://test.invalid/interactions")
        self.parser = GeminiLanguageUnderstandingParser(self.config)
        self.context = ConversationState("diagnostics", ConversationMode.IDLE, None, None, None, [])

    def diagnostic(self, *, error=None, body=None):
        with patch("urllib.request.urlopen", side_effect=error, return_value=http_response(body)), self.assertLogs(LOGGER, level="WARNING") as logs:
            with self.assertRaises(GeminiAdapterError):
                self.parser.parse(USER_TEXT, self.context)
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertFalse(record.exc_info)
        self.assertIsNone(record.stack_info)
        event = json.loads(record.getMessage())
        self.assertEqual(set(event), {"provider", "model", "upstream_http_status", "provider_status",
                                      "provider_code", "exception_class", "summary"})
        self.assertEqual(event["provider"], "gemini")
        for secret in (API_KEY, TOKEN, USER_TEXT, BODY_MARKER, "Authorization", "https://test.invalid"):
            self.assertNotIn(secret, record.getMessage())
        self.assertNotIn("\n", record.getMessage())
        return event

    def test_http_400_invalid_argument_is_not_reported_as_upstream_503_in_logs(self):
        event = self.diagnostic(error=http_error(400, "INVALID_ARGUMENT"))
        self.assertEqual(event["upstream_http_status"], 400)
        self.assertEqual(event["provider_status"], "INVALID_ARGUMENT")
        self.assertEqual(event["provider_code"], 400)
        self.assertEqual(event["summary"], "invalid_model_or_request")
        self.assertEqual(event["exception_class"], "HTTPError")
        self.assertEqual(event["model"], "configured-test-model")

    def test_http_401_authentication_failure(self):
        event = self.diagnostic(error=http_error(401, "UNAUTHENTICATED"))
        self.assertEqual(event["upstream_http_status"], 401)
        self.assertEqual(event["summary"], "api_key_invalid_or_missing")

    def test_http_403_permission_failure(self):
        event = self.diagnostic(error=http_error(403, "PERMISSION_DENIED"))
        self.assertEqual(event["upstream_http_status"], 403)
        self.assertEqual(event["summary"], "permission_denied_or_api_key_rejected")

    def test_http_404_model_or_endpoint_not_found(self):
        event = self.diagnostic(error=http_error(404, "NOT_FOUND"))
        self.assertEqual(event["upstream_http_status"], 404)
        self.assertEqual(event["summary"], "model_or_endpoint_not_found")

    def test_http_429_rate_limit_or_quota_exhaustion(self):
        event = self.diagnostic(error=http_error(429, "RESOURCE_EXHAUSTED"))
        self.assertEqual(event["upstream_http_status"], 429)
        self.assertEqual(event["summary"], "rate_limit_or_quota_exhausted")

    def test_http_503_upstream_unavailability(self):
        event = self.diagnostic(error=http_error(503, "UNAVAILABLE"))
        self.assertEqual(event["upstream_http_status"], 503)
        self.assertEqual(event["summary"], "upstream_unavailable")

    def test_direct_timeout(self):
        event = self.diagnostic(error=TimeoutError(API_KEY + USER_TEXT))
        self.assertIsNone(event["upstream_http_status"])
        self.assertEqual(event["exception_class"], "TimeoutError")
        self.assertEqual(event["summary"], "connection_timeout")

    def test_urlerror_wrapping_timeout(self):
        event = self.diagnostic(error=urllib.error.URLError(TimeoutError(TOKEN)))
        self.assertEqual(event["summary"], "connection_timeout")
        self.assertEqual(event["exception_class"], "TimeoutError")

    def test_connection_failure_without_http_response(self):
        event = self.diagnostic(error=urllib.error.URLError(OSError(API_KEY)))
        self.assertIsNone(event["upstream_http_status"])
        self.assertEqual(event["summary"], "connection_error")
        self.assertEqual(event["exception_class"], "OSError")

    def test_malformed_response_json_preserves_successful_http_status(self):
        event = self.diagnostic(body=("not-json " + BODY_MARKER + API_KEY).encode())
        self.assertEqual(event["upstream_http_status"], 200)
        self.assertEqual(event["exception_class"], "JSONDecodeError")
        self.assertEqual(event["summary"], "response_json_decode_failed")

    def test_invalid_response_utf8(self):
        event = self.diagnostic(body=b"\xff")
        self.assertEqual(event["upstream_http_status"], 200)
        self.assertEqual(event["exception_class"], "UnicodeDecodeError")
        self.assertEqual(event["summary"], "response_json_decode_failed")

    def test_unexpected_response_shapes(self):
        for body in ({"status": "completed", "steps": []}, [], {"candidates": []}, {"status": "failed"}):
            with self.subTest(body=body):
                event = self.diagnostic(body=body)
                self.assertEqual(event["upstream_http_status"], 200)
                self.assertEqual(event["summary"], "unexpected_response_shape")

    def test_invalid_structured_output(self):
        for payload in ({"type": "SECRET-" + USER_TEXT}, "not-json-" + API_KEY):
            with self.subTest(payload=payload):
                body = model_response(payload)
                if isinstance(payload, str):
                    body["steps"][0]["content"][0]["text"] = payload
                event = self.diagnostic(body=body)
                self.assertEqual(event["summary"], "structured_output_invalid")
                self.assertEqual(event["exception_class"], "InvalidGeminiResultError")
                self.assertEqual(event["upstream_http_status"], 200)

    def test_error_body_not_json_still_logs_upstream_http_status(self):
        event = self.diagnostic(error=http_error(503, body=("<html>" + BODY_MARKER + TOKEN).encode()))
        self.assertEqual(event["upstream_http_status"], 503)
        self.assertIsNone(event["provider_status"])
        self.assertIsNone(event["provider_code"])

    def test_untrusted_provider_metadata_is_not_logged(self):
        body = json.dumps({"error": {"code": API_KEY, "status": USER_TEXT + "\n" + TOKEN}}).encode()
        event = self.diagnostic(error=http_error(400, body=body))
        self.assertIsNone(event["provider_code"])
        self.assertIsNone(event["provider_status"])

    def test_unsafe_configured_model_is_redacted(self):
        for model in ("model\n" + USER_TEXT, "model/" + API_KEY, "model/" + TOKEN):
            with self.subTest(model=model):
                self.parser = GeminiLanguageUnderstandingParser(GeminiConfig(API_KEY, model=model))
                self.assertEqual(self.diagnostic(error=http_error(400))["model"], "[REDACTED]")

    def test_unexpected_request_exception_does_not_log_its_message(self):
        event = self.diagnostic(error=ValueError(BODY_MARKER + API_KEY + USER_TEXT))
        self.assertEqual(event["summary"], "unexpected_provider_error")
        self.assertEqual(event["exception_class"], "ValueError")

    def test_successful_response_emits_no_failure_log(self):
        with patch("urllib.request.urlopen", return_value=http_response(model_response({"type": "SOCIAL"}))), self.assertNoLogs(LOGGER):
            self.assertEqual(self.parser.parse("Chào bạn", self.context).type, "SOCIAL")

    def test_full_http_path_preserves_public_error_state_and_same_message_retry(self):
        registry = load_semantic_registry("data/office_rooms.json", "data/office_room_aliases.json")
        apps = {}

        def factory(sid):
            app = TextConversationApplication(FakeLanguageUnderstandingParser(demo_responses()), registry.map_catalog,
                                              room_registry=registry, session_id=sid)
            apps[sid] = app
            return app

        with TestClient(create_api(NlpSessionBridge(factory), ApiSettings(access_token=TOKEN))) as client:
            headers = {"Authorization": "Bearer " + TOKEN}
            sid = client.post("/v1/sessions", json={}, headers=headers).json()["sessionId"]
            path = f"/v1/sessions/{sid}/messages"
            client.post(path, json={"messageId": "single", "text": SINGLE}, headers=headers)
            client.post(path, json={"messageId": "draft", "text": UNKNOWN_ROOM}, headers=headers)
            app = apps[sid]
            before = copy.deepcopy((app.state, dict(app.tasks), dict(app.batches)))
            app.parser = self.parser
            for upstream_status in (400, 401, 403, 404, 429, 503):
                with self.subTest(status=upstream_status), patch("urllib.request.urlopen", side_effect=http_error(upstream_status)), self.assertLogs(LOGGER) as logs:
                    response = client.post(path, json={"messageId": "retry", "text": "Phòng 5."}, headers=headers)
                self.assertEqual(len(logs.records), 1)
                self.assertEqual(json.loads(logs.records[0].getMessage())["upstream_http_status"], upstream_status)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": {"code": "PROVIDER_UNAVAILABLE",
                                 "message": "Language provider is temporarily unavailable. Retry the same message."}})
                self.assertEqual((app.state, dict(app.tasks), dict(app.batches)), before)
                self.assertNotIn(API_KEY, response.text)
            app.parser = FakeLanguageUnderstandingParser(demo_responses())
            ready = client.post(path, json={"messageId": "retry", "text": "Phòng 5."}, headers=headers)
            self.assertEqual(ready.json()["dialogueStatus"], "BATCH_READY")
            self.assertEqual(len(app.tasks), 3)
            self.assertEqual(len(app.batches), 1)
            self.assertEqual(client.post(path, json={"messageId": "retry", "text": "Phòng 5."}, headers=headers).json(), ready.json())
            self.assertEqual(len(app.tasks), 3)


if __name__ == "__main__":
    unittest.main()
