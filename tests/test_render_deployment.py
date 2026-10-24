import copy
import importlib
import io
import json
import os
import shlex
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from click.testing import CliRunner
from fastapi.testclient import TestClient
from uvicorn.importer import import_from_string

from http_conversation_demo import CONFLICT, SINGLE, THREE, UNKNOWN_ROOM, demo_responses
from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.gemini_adapter import GeminiProviderRequestError
from office_robot_nlp.http_api import ApiSettings, create_api, main
from office_robot_nlp.runtime_monitor import MonitoringEventType, StateSource, TaskMonitoringEvent
from office_robot_nlp.semantic_map import load_semantic_registry
from office_robot_nlp.session_bridge import NlpSessionBridge
from office_robot_nlp.task_lifecycle import TaskExecutionEvent
from scripts import smoke_render
from text_conversation import TextConversationApplication


ROOT = Path(__file__).resolve().parent.parent


class RenderDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "RENDER": "true", "PORT": "10000", "API_HOST": "0.0.0.0",
            "API_ACCESS_TOKEN": "unit-test-bearer", "GEMINI_API_KEY": "unit-test-provider-key",
            "GEMINI_MODEL": "gemini-3.8-flash", "CLU_PROVIDER": "gemini", "NLG_ENABLED": "false",
            "ROOM_REGISTRY_PATH": "data/office_rooms.json", "ROOM_ALIASES_PATH": "data/office_room_aliases.json",
        }
        environment = patch.dict(os.environ, self.environment, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        network = patch("urllib.request.urlopen", side_effect=AssertionError("Real provider forbidden"))
        network.start()
        self.addCleanup(network.stop)
        registry = load_semantic_registry()
        self.apps = {}

        def factory(session_id):
            app = TextConversationApplication(Mock(wraps=FakeLanguageUnderstandingParser(demo_responses())),
                                              registry.map_catalog, room_registry=registry, session_id=session_id)
            self.apps[session_id] = app
            return app

        self.bridge = NlpSessionBridge(factory)
        self.headers = {"Authorization": "Bearer unit-test-bearer"}

    def client(self):
        client = TestClient(create_api(self.bridge))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def session(self, client):
        response = client.post("/v1/sessions", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["sessionId"]

    def send(self, client, sid, text, mid="m1", **kwargs):
        return client.post(f"/v1/sessions/{sid}/messages", json={"messageId": mid, "text": text},
                           headers=self.headers, **kwargs)

    def test_uvicorn_imports_actual_factory_and_health_without_provider_call(self):
        self.assertIs(import_from_string("office_robot_nlp.http_api:create_api"), create_api)
        factory = Mock(side_effect=AssertionError("Health must not construct a provider"))
        with TestClient(create_api(NlpSessionBridge(factory))) as client:
            response = client.get("/health", headers=self.headers)
            self.assertEqual(response.json(), {"status": "ok", "service": "office-robot-nlp", "apiVersion": "v1"})
        factory.assert_not_called()

    def test_render_port_wins_over_local_api_port_and_host_defaults_public(self):
        os.environ["API_PORT"] = "8000"
        del os.environ["API_HOST"]
        settings = ApiSettings.from_environment()
        self.assertEqual((settings.host, settings.port), ("0.0.0.0", 10000))
        self.assertNotIn(self.environment["API_ACCESS_TOKEN"], repr(settings))

    def test_local_defaults_and_api_port_remain_compatible(self):
        with patch.dict(os.environ, {"API_PORT": "8123"}, clear=True):
            self.assertEqual(ApiSettings.from_environment(), ApiSettings("127.0.0.1", 8123))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ApiSettings.from_environment(), ApiSettings())

    def test_render_rejects_missing_invalid_and_out_of_range_port(self):
        for value in (None, "", "abc", "0", "65536"):
            with self.subTest(value=value), patch.dict(os.environ, self.environment, clear=True):
                if value is None:
                    del os.environ["PORT"]
                else:
                    os.environ["PORT"] = value
                with self.assertRaises(ValueError):
                    create_api(self.bridge)

    def test_factory_cannot_bypass_missing_public_token_with_explicit_settings(self):
        for token in (None, "", " "):
            with self.subTest(token=token), patch.dict(os.environ, self.environment, clear=True):
                if token is None:
                    del os.environ["API_ACCESS_TOKEN"]
                else:
                    os.environ["API_ACCESS_TOKEN"] = token
                with self.assertRaisesRegex(ValueError, "API_ACCESS_TOKEN"):
                    create_api(self.bridge, ApiSettings())

    def test_render_rejects_loopback_binding_and_settings_override(self):
        os.environ["API_HOST"] = "127.0.0.1"
        with self.assertRaisesRegex(ValueError, "API_HOST"):
            create_api(self.bridge)
        os.environ["API_HOST"] = "0.0.0.0"
        with self.assertRaisesRegex(ValueError, "match"):
            create_api(self.bridge, ApiSettings())

    def test_missing_key_fails_factory_before_requests_and_without_secret_disclosure(self):
        del os.environ["GEMINI_API_KEY"]
        with self.assertRaises(ValueError) as raised:
            create_api(self.bridge)
        self.assertIn("gemini", str(raised.exception))
        self.assertNotIn(self.environment["API_ACCESS_TOKEN"], str(raised.exception))
        self.assertFalse(self.apps)

    def test_invalid_provider_nlg_and_map_configuration_fail_safely(self):
        for updates in ({"CLU_PROVIDER": "unknown"}, {"GEMINI_MODEL": ""}, {"GEMINI_TIMEOUT": "private-invalid"},
                        {"NLG_ENABLED": "maybe"}, {"NLG_ENABLED": "true", "NLG_PROVIDER": "unknown"},
                        {"ROOM_REGISTRY_PATH": "private-invalid-map-path"}):
            with self.subTest(updates=updates), patch.dict(os.environ, updates):
                with self.assertRaises(ValueError) as raised:
                    create_api(self.bridge)
                self.assertNotIn("private-invalid", str(raised.exception))

    def test_factory_setup_checks_no_provider_connectivity_even_with_nlg_enabled(self):
        with patch.dict(os.environ, {"NLG_ENABLED": "true"}):
            self.assertIsNotNone(create_api(self.bridge))

    def test_blueprint_start_command_runs_actual_uvicorn_cli_with_one_worker(self):
        command = next(line.split(": ", 1)[1] for line in (ROOT / "render.yaml").read_text().splitlines()
                       if line.strip().startswith("startCommand:"))
        arguments = [part.replace("$PORT", "10000") for part in shlex.split(command)]
        self.assertEqual(arguments.pop(0), "uvicorn")
        uvicorn_main = importlib.import_module("uvicorn.main")
        with patch.dict(os.environ, {"WEB_CONCURRENCY": "8"}), patch.object(uvicorn_main, "run") as run:
            result = CliRunner().invoke(uvicorn_main.main, arguments)
        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], "office_robot_nlp.http_api:create_api")
        self.assertTrue(kwargs["factory"])
        self.assertEqual((kwargs["host"], kwargs["port"], kwargs["workers"]), ("0.0.0.0", 10000, 1))
        self.assertFalse(kwargs["reload"])
        self.assertFalse(kwargs["access_log"])
        self.assertFalse(kwargs["proxy_headers"])

    def test_module_startup_uses_render_port_and_single_worker(self):
        with patch("uvicorn.run") as run:
            main()
        self.assertEqual(run.call_args.kwargs["port"], 10000)
        self.assertEqual(run.call_args.kwargs["workers"], 1)
        self.assertFalse(run.call_args.kwargs["reload"])

    def test_module_startup_missing_token_exits_with_safe_configuration_error(self):
        del os.environ["API_ACCESS_TOKEN"]
        with patch("uvicorn.run") as run, self.assertRaises(SystemExit) as raised:
            main()
        self.assertIn("API_ACCESS_TOKEN", str(raised.exception))
        run.assert_not_called()

    def test_missing_and_invalid_auth_cannot_enter_any_protected_route(self):
        factory = Mock(side_effect=AssertionError("Unauthorized request reached session factory"))
        bridge = NlpSessionBridge(factory)
        with TestClient(create_api(bridge)) as client:
            for headers in ({}, {"Authorization": "Bearer invalid"}):
                for method, path, body in (("GET", "/health", None), ("POST", "/v1/sessions", {}),
                                           ("POST", "/v1/sessions/missing/messages", {"messageId": "m", "text": SINGLE}),
                                           ("GET", "/v1/tasks/missing", None), ("GET", "/v1/batches/missing", None)):
                    response = client.request(method, path, json=body, headers=headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()["error"]["code"], "UNAUTHORIZED")
        factory.assert_not_called()

    def test_unauthorized_message_in_existing_session_cannot_call_parser(self):
        client = self.client()
        sid = self.session(client)
        response = client.post(f"/v1/sessions/{sid}/messages", json={"messageId": "m", "text": THREE})
        self.assertEqual(response.status_code, 401)
        self.apps[sid].parser.parse.assert_not_called()

    def test_public_documentation_routes_remain_disabled_and_protected(self):
        client = self.client()
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get(path, headers=self.headers).status_code, 404)

    def test_utf8_and_unicode_escaped_json_preserve_exact_vietnamese_and_contract(self):
        client = self.client()
        for escaped in (False, True):
            for text, expected in ((SINGLE, "TASK_READY"), (THREE, "BATCH_READY")):
                with self.subTest(escaped=escaped, text=text):
                    sid = self.session(client)
                    payload = {"messageId": "unicode", "text": text}
                    headers = {**self.headers, "Content-Type": "application/json; charset=utf-8"}
                    response = client.post(f"/v1/sessions/{sid}/messages",
                                           content=json.dumps(payload, ensure_ascii=escaped).encode("utf-8"), headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["dialogueStatus"], expected)
                    self.apps[sid].parser.parse.assert_called_once()
                    self.assertEqual(self.apps[sid].parser.parse.call_args.args[0], text)
                    tasks = response.json()["batch"]["tasks"]
                    self.assertEqual([t["sequenceIndex"] for t in tasks], list(range(len(tasks))))
                    if expected == "TASK_READY":
                        self.assertIsNone(response.json()["batch"]["batchId"])
                    else:
                        self.assertEqual([t["destinationId"] for t in tasks], ["02", "05", "07"])
                        preview = response.json()["batch"]["orderPreview"]
                        self.assertEqual([p["taskId"] for p in preview], [tasks[i]["taskId"] for i in (2, 0, 1)])

    def test_invalid_json_is_distinct_from_safe_provider_failure(self):
        client = self.client()
        sid = self.session(client)
        path = f"/v1/sessions/{sid}/messages"
        for content in (b"{bad", b'{"messageId":"m","text":"\xff"}'):
            response = client.post(path, content=content, headers={**self.headers, "Content-Type": "application/json"})
            self.assertIn(response.status_code, (400, 422))
            self.assertIn(response.json()["error"]["code"], ("INVALID_REQUEST", "HTTP_ERROR"))
        self.apps[sid].parser.parse.assert_not_called()
        self.apps[sid].parser.parse.side_effect = GeminiProviderRequestError("private-provider-detail", status_code=429)
        response = self.send(client, sid, SINGLE)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "PROVIDER_UNAVAILABLE")
        self.assertNotIn("private-provider-detail", response.text)
        self.assertFalse(self.apps[sid].tasks)

    def test_authenticated_batch_get_uses_current_independent_monitor(self):
        client = self.client()
        sid = self.session(client)
        ready = self.send(client, sid, THREE).json()
        batch = ready["batch"]
        task_id = batch["tasks"][0]["taskId"]
        app = self.apps[sid]
        app.process_execution_event(TaskExecutionEvent.TASK_QUEUED, task_id=task_id)
        app.process_monitoring_event(TaskMonitoringEvent(task_id, MonitoringEventType.TASK_ACCEPTED, StateSource.AUTOMATION))
        current = client.get("/v1/batches/" + batch["batchId"], headers=self.headers).json()
        self.assertEqual([t["lifecycle"] for t in current["tasks"]], ["QUEUED", "READY", "READY"])
        self.assertEqual([t["handoff"] for t in current["tasks"]], ["ACCEPTED", "NOT_SENT", "NOT_SENT"])
        task = client.get("/v1/tasks/" + task_id, headers=self.headers).json()
        self.assertEqual(task["handoffSource"], "AUTOMATION")
        self.assertEqual(app.parser.parse.call_count, 1)

    def test_remote_configuration_keeps_atomic_clarification_and_option_a(self):
        client = self.client()
        sid = self.session(client)
        pending = self.send(client, sid, UNKNOWN_ROOM).json()
        self.assertIsNone(pending["batch"])
        self.assertFalse(self.apps[sid].tasks)
        self.send(client, sid, "Chào bạn", "social")
        ready = self.send(client, sid, "Phòng 5.", "room").json()
        self.assertEqual(ready["dialogueStatus"], "BATCH_READY")
        self.send(client, sid, CONFLICT, "conflict")
        chosen = self.send(client, sid, "theo mức ưu tiên", "choice").json()
        self.assertEqual(chosen["dialogueStatus"], "BATCH_READY")
        self.assertEqual(chosen["batch"]["orderPolicy"], "PRIORITY")

    def test_http_cannot_dispatch_load_or_simulate_robot_execution(self):
        client = self.client()
        sid = self.session(client)
        ready = self.send(client, sid, SINGLE).json()
        before = copy.deepcopy(dict(self.apps[sid].tasks))
        for path in ("/v1/dispatch", "/v1/robot/events", "/v1/tasks/" + ready["batch"]["tasks"][0]["taskId"] + "/load"):
            self.assertEqual(client.post(path, json={}, headers=self.headers).status_code, 404)
        self.assertEqual(self.send(client, sid, "/sim task-queued", "slash").status_code, 422)
        self.assertEqual(dict(self.apps[sid].tasks), before)


class SmokeScriptTests(unittest.TestCase):
    def test_live_script_against_fake_authenticated_http_app_without_real_provider(self):
        from http_conversation_demo import demo_bridge
        with TestClient(create_api(demo_bridge(), ApiSettings(access_token="offline-test-token"))) as client:
            class LocalClient:
                def request(self, method, path, payload=None, *, expected_status=200, authenticated=True, token_override=None):
                    headers = {"Authorization": "Bearer " + (token_override or "offline-test-token")} if authenticated else {}
                    response = client.request(method, path, json=payload, headers=headers)
                    if response.status_code != expected_status:
                        raise smoke_render.SmokeFailure("Unexpected local HTTP status.")
                    return response.json()

            passed = smoke_render.run_checks(LocalClient(), multi=True)
        self.assertEqual(len(passed), 7)

    def test_client_serializes_utf8_json_and_attaches_token_without_logging(self):
        client = smoke_render.JsonClient("https://example.invalid", "dummy-token")
        response = Mock()
        response.code = 200
        response.read.return_value = b'{}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(client._opener, "open", return_value=response) as opened:
            client.request("POST", "/v1/sessions/x/messages", {"text": SINGLE})
        request = opened.call_args.args[0]
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"text": SINGLE})
        self.assertIn("tài liệu".encode("utf-8"), request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer dummy-token")

    def test_client_rejects_insecure_or_credential_bearing_urls_and_invalid_tokens(self):
        for url in ("", "http://example.invalid", "https://key@example.invalid", "https://example.invalid?key=private", "https://[bad"):
            with self.subTest(url=url), self.assertRaises(smoke_render.SmokeFailure):
                smoke_render.JsonClient(url, "dummy-token")
        for token in ("", "a b", "\r\nkey"):
            with self.subTest(token=token), self.assertRaises(smoke_render.SmokeFailure):
                smoke_render.JsonClient("https://example.invalid", token)

    def test_client_does_not_follow_redirect_with_bearer_token(self):
        self.assertIsNone(smoke_render.NoRedirects().redirect_request(None, None, 302, "redirect", {}, "https://elsewhere.invalid"))

    def test_main_missing_env_prints_safe_error_without_network(self):
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(smoke_render.main([]), 1)
        self.assertIn("NLP_API_URL", output.getvalue())

    def test_main_does_not_print_unexpected_error_details_or_secrets(self):
        with patch("scripts.smoke_render.JsonClient", side_effect=RuntimeError("private-secret")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(smoke_render.main([]), 1)
        self.assertNotIn("private-secret", output.getvalue())

    def test_main_rejects_nonfinite_timeout(self):
        with patch.dict(os.environ, {"NLP_API_URL": "https://example.invalid", "NLP_API_TOKEN": "dummy-token"}), redirect_stdout(io.StringIO()):
            self.assertEqual(smoke_render.main(["--timeout", "nan"]), 1)


if __name__ == "__main__":
    unittest.main()
