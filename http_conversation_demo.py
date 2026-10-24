"""Offline HTTP contract demonstration. No sockets, credentials, or providers."""

import json

from fastapi.testclient import TestClient

from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.http_api import ApiSettings, create_api
from office_robot_nlp.semantic_map import load_semantic_registry
from office_robot_nlp.session_bridge import NlpSessionBridge
from text_conversation import TextConversationApplication


SINGLE = "Giao tài liệu đến phòng 2."
TWO = "Giao hợp đồng đến phòng 2 và báo cáo đến phòng 5."
THREE = "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hồ sơ trước."
UNKNOWN_ROOM = "Giao hợp đồng đến phòng 2 và báo cáo đến phòng 8."
CONFLICT = "Giao đúng thứ tự báo cáo đến phòng 5 rồi hợp đồng đến phòng 2, nhưng ưu tiên hợp đồng cao hơn."
STRICT = "Giao hợp đồng đến phòng 2 và báo cáo đến phòng 5. Bắt buộc giữ đúng toàn bộ thứ tự này."


def item(document, room, priority="NORMAL"):
    return {"documentText": document, "recipientText": None, "destinationText": room,
            "priority": priority, "constraints": {"ifBusy": None, "ifClosed": None}}


def batch(items, policy="PRIORITY"):
    return {"type": "BATCH_COMMAND", "tasks": items, "orderPolicy": policy, "allocationStatus": "CLEAR"}


def demo_responses():
    return {
        SINGLE: {"type": "TASK_COMMAND", "parsedIntent": {
            "intent": "DELIVER_DOCUMENT", **item("tài liệu", "phòng 2"), "missingFields": []}},
        TWO: batch([item("hợp đồng", "phòng 2"), item("báo cáo", "phòng 5")]),
        THREE: batch([item("hợp đồng", "phòng 2"), item("báo cáo", "phòng 5"), item("hồ sơ", "phòng 7", "HIGH")]),
        UNKNOWN_ROOM: batch([item("hợp đồng", "phòng 2"), item("báo cáo", "phòng 8")]),
        CONFLICT: batch([item("báo cáo", "phòng 5"), item("hợp đồng", "phòng 2", "HIGH")], "STRICT_ORDER"),
        STRICT: batch([item("hợp đồng", "phòng 2", "HIGH"), item("báo cáo", "phòng 5")], "STRICT_ORDER"),
        "Phòng 5.": {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng 5"},
        "theo mức ưu tiên": {"type": "BATCH_ORDER", "orderPolicy": "PRIORITY"},
        "giữ đúng thứ tự": {"type": "BATCH_ORDER", "orderPolicy": "STRICT_ORDER"},
        "Chào bạn": {"type": "SOCIAL"},
        "Hủy": {"type": "CANCEL_PENDING"},
    }


def demo_bridge():
    registry = load_semantic_registry()

    def factory(session_id):
        return TextConversationApplication(FakeLanguageUnderstandingParser(demo_responses()), registry.map_catalog,
                                           room_registry=registry, session_id=session_id)

    return NlpSessionBridge(factory)


def run_demo(client):
    def request(method, path, body=None):
        response = client.request(method, path, json=body) if body is not None else client.request(method, path)
        response.raise_for_status()
        print(f"\n{method} {path} -> {response.status_code}")
        if body is not None:
            print("Request: " + json.dumps(body, ensure_ascii=False))
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        return response.json()

    request("GET", "/health")
    session_id = request("POST", "/v1/sessions", {})["sessionId"]
    path = f"/v1/sessions/{session_id}/messages"
    single = request("POST", path, {"messageId": "single", "text": SINGLE})
    assert single["dialogueStatus"] == "TASK_READY" and single["batch"]["batchId"] is None
    request("GET", "/v1/tasks/" + single["batch"]["tasks"][0]["taskId"])
    pending = request("POST", path, {"messageId": "draft", "text": UNKNOWN_ROOM})
    assert pending["dialogueStatus"] == "CLARIFICATION_REQUIRED" and pending["batch"] is None
    request("POST", path, {"messageId": "social", "text": "Chào bạn"})
    ready = request("POST", path, {"messageId": "room", "text": "Phòng 5."})
    assert ready["dialogueStatus"] == "BATCH_READY"
    request("GET", "/v1/batches/" + ready["batch"]["batchId"])
    priority = request("POST", path, {"messageId": "three", "text": THREE})
    assert priority["batch"]["orderPreview"][0]["taskId"] == priority["batch"]["tasks"][2]["taskId"]
    request("POST", path, {"messageId": "conflict", "text": CONFLICT})
    chosen = request("POST", path, {"messageId": "choice", "text": "theo mức ưu tiên"})
    assert chosen["dialogueStatus"] == "BATCH_READY"
    assert request("POST", path, {"messageId": "choice", "text": "theo mức ưu tiên"}) == chosen
    assert all(task["lifecycle"] == "READY" for task in chosen["batch"]["tasks"])
    print("\nVerified HTTP demo: single, clarification + social, batches, previews, idempotency, current monitoring.")


def main():
    with TestClient(create_api(demo_bridge(), ApiSettings())) as client:
        run_demo(client)


if __name__ == "__main__":
    main()
