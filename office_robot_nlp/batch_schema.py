"""Shared structured-output fields for delivery batch candidates (no robot IDs)."""


BATCH_TYPES = ["BATCH_COMMAND", "BATCH_ORDER", "BATCH_CONFIRM", "CANCEL_PENDING"]

ORDER_POLICY_GUIDANCE = (
    "PRIORITY is the default. STRICT_ORDER requires an explicit fixed sequence for the entire batch. "
    "'Ưu tiên hợp đồng trước' alone means that task is HIGH with orderPolicy PRIORITY; "
    "it does not fix the remaining deliveries. 'Giao lần lượt theo đúng thứ tự đã nêu' "
    "is a whole-batch STRICT_ORDER mandate. Preserve legitimate strict ordering, even when it "
    "conflicts with task priority; deterministic dialogue will clarify. Never reorder the tasks array."
)
TASK_PRIORITY_GUIDANCE = (
    "Per-task urgency only: default NORMAL, explicit urgency/preference HIGH for that task only. "
    "Strict sequence or being listed first does not imply HIGH. Keep original tasks array order."
)
BUSY_POLICY_GUIDANCE = (
    "Explicit user policy if the room is busy. Return null when unspecified. "
    "ASK_USER is only for an explicit request to ask the user, never an inferred default."
)
CLOSED_POLICY_GUIDANCE = (
    "Explicit user policy if the room is closed. Return null when unspecified. "
    "ASK_USER is only for an explicit request to ask the user, never an inferred default."
)


def batch_properties(*, json_schema: bool = False) -> dict:
    text = {"type": ["string", "null"]} if json_schema else {"type": "string", "nullable": True}
    policy = {
        "type": "object", "properties": {
            "ifBusy": {**text, "enum": ["WAIT", "ASK_USER", None] if json_schema else ["WAIT", "ASK_USER"],
                       "description": BUSY_POLICY_GUIDANCE},
            "ifClosed": {**text, "enum": ["WAIT", "RETRY", "ASK_USER", None] if json_schema else ["WAIT", "RETRY", "ASK_USER"],
                         "description": CLOSED_POLICY_GUIDANCE},
        }, "required": ["ifBusy", "ifClosed"],
    }
    item = {
        "type": "object", "properties": {
            "documentText": dict(text), "recipientText": dict(text), "destinationText": dict(text),
            "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"], "description": TASK_PRIORITY_GUIDANCE},
            "constraints": policy,
        },
        "required": ["documentText", "recipientText", "destinationText", "priority", "constraints"],
    }
    if json_schema:
        item["additionalProperties"] = False
        policy["additionalProperties"] = False
    return {
        "tasks": {"type": "array", "minItems": 1, "items": item,
                  "description": "Keep original mention order; task priority must not reorder this array."},
        "orderPolicy": {"type": "string", "enum": ["PRIORITY", "STRICT_ORDER"], "description": ORDER_POLICY_GUIDANCE},
        "allocationStatus": {"type": "string", "enum": ["CLEAR", "AMBIGUOUS"]},
    }
