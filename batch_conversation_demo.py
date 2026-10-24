"""Deterministic Phase 8B.2 CLI demonstration using only fake NLU responses."""

from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.dialogue_manager import BatchReady
from office_robot_nlp.semantic_map import load_semantic_registry
from office_robot_nlp.order_preview import build_order_preview
from text_conversation import LocalCommandResult, TextConversationApplication


def delivery(document: str, destination: str, priority: str = "NORMAL") -> dict:
    return {"documentText": document, "recipientText": None, "destinationText": destination,
            "priority": priority, "constraints": {"ifBusy": None, "ifClosed": None}}


def demo_application() -> tuple[TextConversationApplication, tuple[str, ...]]:
    initial = "Giao hợp đồng đến phòng 2, báo cáo đến phòng 5 và hồ sơ đến phòng 7. Ưu tiên hồ sơ trước."
    unknown = "Giao biên bản đến phòng 2 và chứng từ đến phòng 8."
    conflict = "Giao đúng thứ tự báo cáo đến phòng 5 rồi hợp đồng đến phòng 2, nhưng ưu tiên hợp đồng cao hơn."
    registry = load_semantic_registry()
    parser = FakeLanguageUnderstandingParser({
        initial: {"type": "BATCH_COMMAND", "tasks": [
            delivery("hợp đồng", "phòng 2"), delivery("báo cáo", "phòng 5"), delivery("hồ sơ", "phòng 7", "HIGH"),
        ], "orderPolicy": "PRIORITY", "allocationStatus": "CLEAR"},
        unknown: {"type": "BATCH_COMMAND", "tasks": [
            delivery("biên bản", "phòng 2"), delivery("chứng từ", "phòng 8"),
        ], "orderPolicy": "PRIORITY", "allocationStatus": "CLEAR"},
        "Phòng 5.": {"type": "CLARIFICATION_ANSWER", "field": "destination", "value": "Phòng 5"},
        conflict: {"type": "BATCH_COMMAND", "tasks": [
            delivery("báo cáo", "phòng 5"), delivery("hợp đồng", "phòng 2", "HIGH"),
        ], "orderPolicy": "STRICT_ORDER", "allocationStatus": "CLEAR"},
        "theo mức ưu tiên": {"type": "BATCH_ORDER", "orderPolicy": "PRIORITY"},
        "giữ đúng thứ tự": {"type": "BATCH_ORDER", "orderPolicy": "STRICT_ORDER"},
    })
    # Construct the existing console application directly: no provider factory or
    # environment-controlled optional NLG can run in this offline demonstration.
    app = TextConversationApplication(parser, registry.map_catalog, room_registry=registry)
    return app, (initial, "/batches", "/batch batch_001", unknown, "/tasks", "Phòng 5.",
                 "/batches", "/batch batch_002", conflict, "theo mức ưu tiên", "/batch batch_003",
                 conflict, "giữ đúng thứ tự", "/batch batch_004")


def main() -> None:
    app, inputs = demo_application()
    print("Phase 8B.2 deterministic fake-parser CLI demo")
    for text in inputs:
        print(f"\nYou: {text}")
        before = (len(app.tasks), len(app.batches))
        turn = app.process_text(text)
        if isinstance(turn.output, LocalCommandResult):
            print(turn.responseText)
        else:
            print(f"Assistant: {turn.responseText}")
        if isinstance(turn.output, BatchReady):
            print(f"Registered batch: {turn.responsePlan.facts['batchId']}")
        if app.state.pendingBatch is not None:
            assert (len(app.tasks), len(app.batches)) == before, "A draft must not register partial tasks"
    assert len(app.tasks) == 9 and len(app.batches) == 4
    assert all(record.lifecycle.state.value == "READY" for record in app.tasks.values())
    assert app.robot_state.connectionState.value == "NOT_CONNECTED"
    for batch_id, expected in (("batch_001", ["hồ sơ", "hợp đồng", "báo cáo"]),
                               ("batch_003", ["hợp đồng", "báo cáo"]),
                               ("batch_004", ["báo cáo", "hợp đồng"])):
        preview = build_order_preview(app.batches[batch_id], app.tasks)
        assert [item.documentText for item in preview] == expected
    assert app.batches["batch_003"].orderPolicy.value == "PRIORITY"
    assert app.batches["batch_004"].orderPolicy.value == "STRICT_ORDER"
    print("\nDemo verified: 4 batches, 9 READY tasks; both order previews correct; no second confirmation.")


if __name__ == "__main__":
    main()
