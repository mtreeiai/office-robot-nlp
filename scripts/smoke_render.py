"""Explicit live HTTPS smoke test; invoking this script consumes provider quota."""

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit
from uuid import uuid4


SINGLE_TEXT = "Giao tài liệu đến phòng 2."
MULTI_TEXT = "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hồ sơ trước."


class SmokeFailure(Exception):
    """Messages are fixed safe diagnostics, never response bodies or credentials."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward the shared bearer token to a redirect target.
        return None


class JsonClient:
    def __init__(self, url, token, timeout=90):
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == "https" and parsed.hostname and parsed.port != 0
                     and parsed.username is None and parsed.password is None
                     and not parsed.query and not parsed.fragment
                     and not any(c.isspace() for c in url))
        except ValueError:
            valid = False
        if not valid:
            raise SmokeFailure("NLP_API_URL must be an HTTPS service URL without credentials, query or fragment.")
        if not token or any(c.isspace() for c in token) or not token.isascii():
            raise SmokeFailure("NLP_API_TOKEN must be a nonempty ASCII bearer token without whitespace.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise SmokeFailure("Timeout must be a positive finite number.")
        self.url = url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self._opener = urllib.request.build_opener(NoRedirects())

    def request(self, method, path, payload=None, *, expected_status=200, authenticated=True, token_override=None):
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + (token_override if token_override is not None else self._token)
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(self.url + path, data=body, headers=headers, method=method)
        try:
            response = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, ValueError):
            raise SmokeFailure("HTTPS request failed; check URL, network, cold start and timeout.") from None
        with response:
            if response.code != expected_status:
                raise SmokeFailure(f"Unexpected HTTP status: expected {expected_status}, received {response.code}.")
            try:
                return json.loads(response.read().decode("utf-8"))
            except (UnicodeError, ValueError):
                raise SmokeFailure("Server did not return valid UTF-8 JSON.") from None


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def check_intake(task, destination):
    require(task["destinationId"] == destination, "Unexpected document/destination association.")
    require(task["lifecycle"] == "READY", "Semantic intake must remain READY.")
    require(task["handoff"] == "NOT_SENT", "Semantic intake must remain NOT_SENT.")
    require(task["documentLoad"] == "UNCONFIRMED", "Semantic intake must remain UNCONFIRMED.")


def run_checks(client, *, multi=False):
    health = client.request("GET", "/health")
    require(health == {"status": "ok", "service": "office-robot-nlp", "apiVersion": "v1"},
            "Unexpected health contract.")
    for kwargs in ({"authenticated": False}, {"token_override": "invalid-" + uuid4().hex}):
        rejected = client.request("GET", "/health", expected_status=401, **kwargs)
        require(rejected["error"]["code"] == "UNAUTHORIZED", "Unauthenticated access was not rejected.")
    missing = client.request("POST", "/v1/sessions/missing-" + uuid4().hex + "/messages",
                             {"messageId": "unknown", "text": SINGLE_TEXT}, expected_status=404)
    require(missing["error"]["code"] == "SESSION_NOT_FOUND", "Unexpected missing-session response.")
    created = client.request("POST", "/v1/sessions", {}, expected_status=201)
    require(created["status"] == "ACTIVE" and bool(created["sessionId"]), "Session was not created.")
    session_id = created["sessionId"]
    path = "/v1/sessions/" + quote(session_id, safe="") + "/messages"
    message = {"messageId": "smoke-single-" + uuid4().hex, "text": SINGLE_TEXT}
    single = client.request("POST", path, message)
    require(single["dialogueStatus"] == "TASK_READY", "Single request did not produce TASK_READY.")
    require(single["sessionId"] == session_id and single["messageId"] == message["messageId"], "Message identity mismatch.")
    envelope = single["batch"]
    require(envelope["batchId"] is None and envelope["orderPolicy"] is None and len(envelope["tasks"]) == 1,
            "Single-task envelope changed.")
    task = envelope["tasks"][0]
    check_intake(task, "02")
    require(task["sequenceIndex"] == 0 and envelope["orderPreview"] == [{"position": 0, "taskId": task["taskId"]}],
            "Single-task preview changed.")
    snapshot = client.request("GET", "/v1/tasks/" + quote(task["taskId"], safe=""))
    check_intake(snapshot, "02")
    require(snapshot["sessionId"] == session_id and snapshot["batchId"] is None, "Task association changed.")
    require(snapshot["taskId"] == task["taskId"], "GET task ID mismatch.")
    require(client.request("POST", path, message) == single, "Idempotent retry did not return the original result.")
    passed = ["authenticated health", "missing/invalid token rejection", "missing session", "session creation",
              "Vietnamese single TASK_READY + monitoring", "same-message retry"]
    if multi:
        message = {"messageId": "smoke-multi-" + uuid4().hex, "text": MULTI_TEXT}
        result = client.request("POST", path, message)
        require(result["dialogueStatus"] == "BATCH_READY", "Multi request did not produce BATCH_READY.")
        require(result["sessionId"] == session_id and result["messageId"] == message["messageId"], "Batch message identity mismatch.")
        envelope = result["batch"]
        tasks = envelope["tasks"]
        require(bool(envelope["batchId"]) and envelope["orderPolicy"] == "PRIORITY" and len(tasks) == 3,
                "Unexpected registered batch contract.")
        ids = [task["taskId"] for task in tasks]
        require(len(set(ids)) == 3, "Batch task IDs must be distinct.")
        for index, (task, room) in enumerate(zip(tasks, ("02", "05", "07"))):
            check_intake(task, room)
            require(task["sequenceIndex"] == index, "Original sequence indexes changed.")
        require([task["priority"] for task in tasks] == ["NORMAL", "NORMAL", "HIGH"], "Unexpected task priorities.")
        for task, document in zip(tasks, ("hợp đồng", "báo cáo", "hồ sơ")):
            require(document in task["documentText"].casefold(), "Document text/room association changed.")
        expected_preview = [{"position": pos, "taskId": ids[index]} for pos, index in enumerate((2, 0, 1))]
        require(envelope["orderPreview"] == expected_preview, "Priority preview changed.")
        current = client.request("GET", "/v1/batches/" + quote(envelope["batchId"], safe=""))
        require(current["batchId"] == envelope["batchId"] and current["sessionId"] == session_id
                and current["orderPolicy"] == envelope["orderPolicy"], "GET batch association changed.")
        require(current["orderPreview"] == expected_preview and len(current["tasks"]) == 3, "GET batch preview/membership changed.")
        for original, actual in zip(tasks, current["tasks"]):
            require(all(actual[key] == value for key, value in original.items()), "GET batch monitoring differs from intake.")
        passed.append("Vietnamese three-document BATCH_READY + priority preview + GET batch")
    return passed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--multi", action="store_true", help="Also send a three-document request (additional live provider quota).")
    parser.add_argument("--timeout", type=float, default=90, help="HTTPS timeout in seconds, including Render cold starts (default: 90).")
    args = parser.parse_args(argv)
    try:
        client = JsonClient(os.environ.get("NLP_API_URL", ""), os.environ.get("NLP_API_TOKEN", ""), args.timeout)
        passed = run_checks(client, multi=args.multi)
    except SmokeFailure as error:
        print(f"FAIL: {error}")
        return 1
    except (KeyError, TypeError, ValueError, AttributeError):
        print("FAIL: Response did not match the API contract.")
        return 1
    except Exception:
        print("FAIL: Smoke test could not complete; diagnostic details were suppressed.")
        return 1
    for label in passed:
        print("PASS: " + label)
    print(f"PASS: {len(passed)} smoke checks; live semantic requests only, no robot dispatch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
