# Office Delivery Robot — NLP & Human–Robot Interaction Technical Specification

**Document purpose:** implementation specification for an AI coding agent.

**Primary ownership:** NLP / human–robot interaction layer.

**System context:** office document-delivery robot operating in a 3D simulated map. The robot receives tasks from a human through natural-language interaction, converts the request into a validated structured task, sends the task to the robot/planning side, and handles robot events that require user-facing feedback or clarification.

---

## 1. Core design principle

The architecture must enforce the following separation of responsibilities:

> **LLM understands language. Deterministic code validates meaning. Planner reasons about the world. Robot controller executes actions. Dialogue Manager connects the robot back to the human.**

The NLP subsystem MUST NOT directly control robot motion, generate navigation coordinates, infer physical world state, or decide whether a room is currently busy/closed.

The main flow is:

```text
Human speech/text
      ↓
Speech-to-Text (optional for text input)
      ↓
Dialogue Manager
      ↓
LLM Intent Parser
      ↓
Schema Validator
      ↓
Entity Resolver
      ↓
Validated TaskRequest / TaskUpdate
      ↓
Robot Task Gateway
================ ROBOT SIDE ================
Task Manager / World State / Planner / Robot Controller
      ↓
RobotEvent
============================================
      ↓
Dialogue Manager
      ↓
Human-facing response
```

---

# 2. Scope

## 2.1 In scope

The coding agent should implement the following modules:

1. Natural-language input handling.
2. Optional speech transcript input boundary.
3. Dialogue state management.
4. LLM-based intent parsing with structured output.
5. Intent schema validation.
6. Entity resolution against a supplied office map catalog.
7. Missing-information detection and clarification flow.
8. TaskRequest creation.
9. TaskUpdate creation for follow-up decisions.
10. RobotTaskGateway interface.
11. RobotEvent handling.
12. User-facing response generation from deterministic event/context data.
13. NLP debug/demo UI.
14. NLP Test Playground.
15. Unit tests.
16. NLP evaluation tests.
17. Integration tests.
18. End-to-end tests using a mock robot gateway.

## 2.2 Out of scope

The coding agent MUST NOT implement the following unless a later specification explicitly requests it:

- PDDL domain or problem generation.
- PDDL planner.
- Path planning.
- SLAM.
- Navigation.
- Collision avoidance.
- Robot motor control.
- ROS2 robot implementation.
- 3D simulator logic.
- Room occupancy detection.
- Door-state detection.
- Physical document manipulation.
- Coordinate conversion from semantic locations to 3D positions.

These belong to the robot/planning subsystem.

---

# 3. MVP use case

The first supported user task is:

```text
DELIVER_DOCUMENT
```

Example utterances:

```text
"Đưa tài liệu này đến phòng họp A."
"Mang tập hồ sơ này sang phòng A giúp tôi."
"Chuyển gấp tài liệu này tới phòng B."
"Đưa tài liệu đến phòng A, nếu phòng đang bận thì chờ."
```

The NLP subsystem should normalize different user expressions into the same structured task representation.

---

# 4. Important semantic distinction

The following concepts are NOT separate user intents:

```text
ROOM_BUSY
ROOM_CLOSED
NO_DESTINATION
DELIVER_DOCUMENT_PRIORITY
```

Instead:

- `ROOM_BUSY` is a robot/world-state condition.
- `ROOM_CLOSED` is a robot/world-state condition.
- missing destination is a validation/clarification condition.
- priority is an attribute of the delivery task.

Therefore, the MVP intent ontology is intentionally small:

```ts
type IntentType =
  | "DELIVER_DOCUMENT"
  | "UNKNOWN";
```

Future intents such as `FETCH_OBJECT`, `PUT_OBJECT`, `FIND_OBJECT`, and `FOLLOW_PERSON` are intentionally not part of this MVP.

---

# 5. Responsibility boundary

## 5.1 NLP subsystem owns

- understanding human language;
- conversation context;
- extracting semantic task fields;
- detecting missing required information;
- resolving human location names to known map IDs;
- validating structured intent;
- converting valid intent into `TaskRequest`;
- converting user answers to robot questions into `TaskUpdate`;
- displaying robot status/events;
- asking the user for clarification when necessary.

## 5.2 Robot subsystem owns

- world state;
- room occupancy;
- door state;
- current robot pose;
- task scheduling;
- priority execution policy;
- planning;
- navigation;
- action execution;
- simulator/ROS2 integration;
- determining when user intervention is required.

## 5.3 Shared contract

Both teams must agree on these five data models:

1. `MapCatalog`
2. `ParsedIntent`
3. `TaskRequest`
4. `TaskUpdate`
5. `RobotEvent`

---

# 6. Project structure

Recommended logical structure:

```text
src/
├── domain/
│   ├── intent.ts
│   ├── task.ts
│   ├── robot-event.ts
│   ├── map.ts
│   └── conversation.ts
│
├── speech/
│   └── speech-to-text.interface.ts
│
├── intent/
│   ├── intent-parser.service.ts
│   ├── intent-prompt.ts
│   └── intent-schema.ts
│
├── validation/
│   └── intent-validator.ts
│
├── entity/
│   ├── entity-resolver.ts
│   └── map-catalog.repository.ts
│
├── dialogue/
│   ├── dialogue-manager.ts
│   ├── conversation-store.ts
│   ├── clarification.service.ts
│   └── response-builder.ts
│
├── robot/
│   ├── robot-task.gateway.ts
│   ├── mock-robot-task.gateway.ts
│   ├── robot-event.handler.ts
│   └── dto/
│
├── application/
│   ├── interpret-command.usecase.ts
│   ├── submit-task.usecase.ts
│   └── handle-robot-event.usecase.ts
│
├── ui/
│   ├── conversation/
│   ├── nlp-debug/
│   ├── robot-status/
│   ├── event-log/
│   └── test-playground/
│
└── tests/
    ├── unit/
    ├── nlp-evaluation/
    ├── integration/
    └── e2e/
```

The exact framework and physical folder names may be adapted to the selected stack, but the module boundaries must remain equivalent.

---

# 7. Domain models

## 7.1 `ParsedIntent`

The LLM MUST return semantic text rather than internal robot/map IDs.

```ts
type ParsedIntent = {
  intent: "DELIVER_DOCUMENT" | "UNKNOWN";

  destinationText: string | null;
  documentText: string | null;

  priority: "LOW" | "NORMAL" | "HIGH";

  constraints: {
    ifBusy: "WAIT" | "ASK_USER" | null;
    ifClosed: "WAIT" | "RETRY" | "ASK_USER" | null;
  };

  missingFields: Array<
    | "destination"
    | "document"
    | "priority"
    | "busy_policy"
  >;
};
```

Example:

User:

```text
"Đưa tài liệu này đến phòng họp A nhưng nếu phòng đang bận thì chờ."
```

LLM output:

```json
{
  "intent": "DELIVER_DOCUMENT",
  "destinationText": "phòng họp A",
  "documentText": "tài liệu này",
  "priority": "NORMAL",
  "constraints": {
    "ifBusy": "WAIT",
    "ifClosed": null
  },
  "missingFields": []
}
```

The LLM MUST NOT return `meeting_room_A` unless the literal user language already contains that exact system identifier.

---

## 7.2 `MapCatalog`

The robot/map team supplies the semantic location catalog.

```ts
type MapLocation = {
  id: string;
  name: string;
  aliases: string[];
};

type MapCatalog = {
  version: string;
  locations: MapLocation[];
};
```

Example:

```json
{
  "version": "1.0",
  "locations": [
    {
      "id": "meeting_room_A",
      "name": "Phòng họp A",
      "aliases": [
        "phòng A",
        "phòng họp A",
        "meeting room A"
      ]
    },
    {
      "id": "meeting_room_B",
      "name": "Phòng họp B",
      "aliases": [
        "phòng B",
        "phòng họp B",
        "meeting room B"
      ]
    }
  ]
}
```

The NLP subsystem MUST NOT store or require map coordinates.

---

## 7.3 `ResolvedIntent`

After entity resolution:

```ts
type ResolvedIntent = {
  intent: "DELIVER_DOCUMENT";

  destination: {
    locationId: string;
    originalText: string;
  };

  document: {
    documentId: string | null;
    originalText: string | null;
  };

  priority: "LOW" | "NORMAL" | "HIGH";

  constraints: {
    ifBusy: "WAIT" | "ASK_USER" | null;
    ifClosed: "WAIT" | "RETRY" | "ASK_USER" | null;
  };
};
```

---

## 7.4 `TaskRequest`

This is the primary contract sent from NLP to the robot side.

```ts
type TaskRequest = {
  requestId: string;

  taskType: "DELIVER_DOCUMENT";

  payload: {
    documentId: string | null;
  };

  target: {
    locationId: string;
  };

  priority: "LOW" | "NORMAL" | "HIGH";

  policies: {
    onBusy: "WAIT" | "ASK_USER" | null;
    onClosed: "WAIT" | "RETRY" | "ASK_USER" | null;
  };
};
```

Example:

```json
{
  "requestId": "req_001",
  "taskType": "DELIVER_DOCUMENT",
  "payload": {
    "documentId": "document_01"
  },
  "target": {
    "locationId": "meeting_room_A"
  },
  "priority": "NORMAL",
  "policies": {
    "onBusy": "WAIT",
    "onClosed": null
  }
}
```

---

## 7.5 `TaskAccepted`

```ts
type TaskAccepted = {
  requestId: string;
  taskId: string;
  accepted: boolean;
  reason?: string;
};
```

---

## 7.6 `TaskUpdate`

Used when the user answers a question raised by the robot/planner.

```ts
type TaskUpdate = {
  taskId: string;

  decision:
    | "WAIT"
    | "RETRY"
    | "CANCEL";
};
```

Example:

```json
{
  "taskId": "task_001",
  "decision": "WAIT"
}
```

---

# 8. Robot gateway interface

The application layer MUST depend on an abstraction instead of ROS2, simulator, REST, or WebSocket directly.

```ts
interface RobotTaskGateway {
  submitTask(task: TaskRequest): Promise<TaskAccepted>;

  updateTask(update: TaskUpdate): Promise<void>;

  cancelTask(taskId: string): Promise<void>;
}
```

Implement at least:

```text
MockRobotTaskGateway
```

for local development and automated testing.

A later robot-side adapter may implement the same interface through REST, WebSocket, ROS2 service/topic, or another transport without changing the NLP application logic.

---

# 9. Robot events

Robot-side events are inputs to the NLP/dialogue subsystem.

```ts
type RobotEvent =
  | {
      event: "TASK_STATUS";
      taskId: string;
      status:
        | "QUEUED"
        | "MOVING"
        | "WAITING"
        | "DELIVERING"
        | "COMPLETED"
        | "FAILED"
        | "CANCELLED";
      reason?: string;
      locationId?: string;
    }
  | {
      event: "USER_DECISION_REQUIRED";
      taskId: string;
      reason: "ROOM_BUSY" | "ROOM_CLOSED" | "OTHER";
      locationId?: string;
      allowedOptions: Array<"WAIT" | "RETRY" | "CANCEL">;
    };
```

Example:

```json
{
  "event": "USER_DECISION_REQUIRED",
  "taskId": "task_001",
  "reason": "ROOM_BUSY",
  "locationId": "meeting_room_A",
  "allowedOptions": ["WAIT", "CANCEL"]
}
```

The NLP layer does not determine that the room is busy. It only receives this fact from the robot side and converts it into human interaction.

---

# 10. Dialogue Manager

The Dialogue Manager is the orchestration center of the human interaction layer.

It MUST distinguish between:

1. a new command;
2. an answer to a clarification question;
3. an answer to a robot decision request.

Conversation state example:

```ts
type ConversationState = {
  conversationId: string;

  mode:
    | "IDLE"
    | "AWAITING_TASK_INFORMATION"
    | "AWAITING_ROBOT_DECISION";

  pendingIntent: ParsedIntent | null;

  awaitingField:
    | "destination"
    | "document"
    | null;

  activeTaskId: string | null;

  allowedDecisions: Array<"WAIT" | "RETRY" | "CANCEL">;
};
```

---

# 11. Dialogue flows

## 11.1 Complete request

```text
User:
"Đưa tài liệu này tới phòng A."

Intent Parser:
DELIVER_DOCUMENT
Destination text = "phòng A"

Entity Resolver:
meeting_room_A

Validator:
VALID

TaskRequest:
submitted to RobotTaskGateway
```

---

## 11.2 Missing destination

```text
User:
"Đưa tài liệu tới phòng họp."

Parser:
intent = DELIVER_DOCUMENT
destinationText = null

Validator:
MISSING_DESTINATION

Dialogue Manager:
"Bạn muốn giao tài liệu tới phòng họp nào?"

State:
AWAITING_TASK_INFORMATION
awaitingField = destination

User:
"Phòng A."

Dialogue Manager:
fill destination only

Entity Resolver:
meeting_room_A

Validator:
VALID

TaskRequest:
submit
```

The second utterance `"Phòng A."` MUST NOT be interpreted as a completely new standalone robot task.

---

## 11.3 Unknown location

```text
User:
"Đưa tài liệu tới phòng Z."
```

If `phòng Z` does not exist in `MapCatalog`:

```text
Entity resolution result = UNKNOWN_LOCATION
```

The system MUST NOT invent `meeting_room_Z`.

The Dialogue Manager should ask the user to choose a valid destination.

---

## 11.4 Busy-room policy already provided

User:

```text
"Đưa tài liệu tới phòng A, nếu phòng bận thì chờ."
```

TaskRequest includes:

```json
{
  "policies": {
    "onBusy": "WAIT"
  }
}
```

If the robot later reports `ROOM_BUSY`, the robot/planner side may directly apply the supplied policy.

The NLP subsystem performs no additional world-state reasoning.

---

## 11.5 Robot requests a human decision

Robot event:

```json
{
  "event": "USER_DECISION_REQUIRED",
  "taskId": "task_001",
  "reason": "ROOM_BUSY",
  "locationId": "meeting_room_A",
  "allowedOptions": ["WAIT", "CANCEL"]
}
```

Dialogue Manager response:

```text
"Phòng họp A hiện đang bận. Bạn muốn tôi chờ hay hủy nhiệm vụ?"
```

State becomes:

```text
AWAITING_ROBOT_DECISION
```

User:

```text
"Chờ đi."
```

Expected TaskUpdate:

```json
{
  "taskId": "task_001",
  "decision": "WAIT"
}
```

The system MUST verify that `WAIT` exists in `allowedOptions` before sending the update.

---

# 12. LLM Intent Parser requirements

The LLM is responsible only for semantic interpretation of human language.

It MUST:

- use structured output;
- return only values allowed by the schema;
- preserve user language in `destinationText` and `documentText`;
- return `UNKNOWN` for unsupported tasks;
- return `null` for information that is not present;
- never invent physical world state;
- never invent room IDs;
- never create robot actions;
- never generate navigation instructions.

The prompt should explicitly state:

```text
You understand language only.
You do not control the robot.
You do not know the current physical world state.
Do not infer whether a room is busy, free, open, or closed.
Do not invent map IDs.
Return structured data matching the provided schema.
```

---

# 13. Deterministic validation

The following checks MUST be implemented in normal code, not delegated to the LLM:

```text
schema valid?
      ↓
intent supported?
      ↓
required fields present?
      ↓
entity exists in MapCatalog?
      ↓
priority value valid?
      ↓
policy values valid?
      ↓
TaskRequest can be generated
```

The system MUST NOT rely on an LLM self-reported confidence score to decide whether a robot task is safe to submit.

Recommended result type:

```ts
type ValidationResult =
  | {
      valid: true;
    }
  | {
      valid: false;
      errors: Array<
        | "UNSUPPORTED_INTENT"
        | "MISSING_DESTINATION"
        | "UNKNOWN_LOCATION"
        | "INVALID_PRIORITY"
        | "INVALID_POLICY"
      >;
    };
```

---

# 14. Entity Resolver

Input:

```text
ParsedIntent.destinationText
+
MapCatalog
```

Output:

```ts
type EntityResolutionResult =
  | {
      status: "RESOLVED";
      locationId: string;
    }
  | {
      status: "NOT_FOUND";
    }
  | {
      status: "AMBIGUOUS";
      candidates: string[];
    };
```

Required behavior:

```text
"phòng A"
→ meeting_room_A

"phòng họp A"
→ meeting_room_A

"meeting room A"
→ meeting_room_A
```

If multiple locations match, return `AMBIGUOUS`; do not guess.

---

# 15. UI — Robot Assistant Demo

The MVP UI should use one main screen.

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Office Delivery Robot                                               │
├───────────────────────┬─────────────────────────┬────────────────────┤
│ USER / CHAT           │ NLP INTERPRETATION      │ ROBOT STATUS       │
│                       │                         │                    │
│ User messages         │ Intent                  │ Connection status  │
│ Robot messages        │ Destination text        │ Current task       │
│                       │ Resolved entity         │ Robot state        │
│ Text input            │ Priority                │ Current location   │
│ Microphone control    │ Policies                │ Last event         │
│ Send                  │ Validation              │                    │
├───────────────────────┴─────────────────────────┴────────────────────┤
│ PIPELINE                                                            │
│ STT → Intent → Entity → Validation → Task → Robot                  │
├──────────────────────────────────────────────────────────────────────┤
│ EVENT LOG                                                            │
└──────────────────────────────────────────────────────────────────────┘
```

The UI is primarily a demo/debug tool, not a production operator dashboard.

---

# 16. NLP Debug Panel

For the latest user command, display:

```text
Transcript
Intent
Destination text
Resolved location ID
Priority
Busy policy
Closed policy
Validation status
Task submission status
```

Provide a `View raw JSON` action for:

- ParsedIntent;
- resolved intent;
- TaskRequest;
- latest RobotEvent.

---

# 17. Event log

Record major pipeline events with timestamp.

Example:

```text
10:32:01 USER_COMMAND_RECEIVED
10:32:02 SPEECH_TRANSCRIBED
10:32:03 INTENT_PARSED
10:32:03 ENTITY_RESOLVED
10:32:03 VALIDATION_SUCCESS
10:32:04 TASK_SUBMITTED
10:32:06 ROBOT_MOVING
10:32:10 ROOM_BUSY
10:32:10 ROBOT_WAITING
10:32:30 ROOM_AVAILABLE
10:32:31 ROBOT_DELIVERING
10:32:35 TASK_COMPLETED
```

This log should help identify whether a failure occurred in NLP, entity resolution, validation, task integration, or robot execution.

---

# 18. NLP Test Playground

Provide a separate tab or section where developers can enter text and inspect parser behavior without running the robot.

Example:

```text
Input
------------------------------------------------
Đưa tài liệu này đến phòng A, khá gấp.
------------------------------------------------

Expected                    Actual
Intent                      DELIVER_DOCUMENT
Destination                 meeting_room_A
Priority                    HIGH
Busy policy                 null

[Run Test]
```

When a test case contains an expected result, show field-level pass/fail output.

---

# 19. Test dataset format

Store NLP evaluation examples as data rather than hard-coding them into test code.

Example:

```json
[
  {
    "id": "INTENT_001",
    "input": "Đưa tài liệu tới phòng A",
    "expected": {
      "intent": "DELIVER_DOCUMENT",
      "destination": "meeting_room_A",
      "priority": "NORMAL"
    }
  },
  {
    "id": "INTENT_002",
    "input": "Chuyển gấp tài liệu sang phòng B",
    "expected": {
      "intent": "DELIVER_DOCUMENT",
      "destination": "meeting_room_B",
      "priority": "HIGH"
    }
  }
]
```

---

# 20. Required tests

## 20.1 Unit tests

### Entity Resolver

```text
"phòng A"
→ meeting_room_A
```

```text
"phòng họp A"
→ meeting_room_A
```

```text
"meeting room A"
→ meeting_room_A
```

```text
"phòng Z"
→ NOT_FOUND
```

### Validator

Test:

- valid delivery intent;
- missing destination;
- unsupported intent;
- invalid priority;
- invalid policy;
- unknown location.

---

## 20.2 NLP evaluation tests

At minimum cover:

### Basic command

```text
"Đưa tài liệu tới phòng A."
```

Expected:

```text
DELIVER_DOCUMENT
meeting_room_A
NORMAL
```

### Paraphrases

```text
"Đưa tài liệu tới phòng A."
"Mang hồ sơ này sang phòng A."
"Chuyển tài liệu này qua phòng họp A."
"Phiền robot đem tập hồ sơ này đến phòng A."
```

All should map to the same task intent and destination.

### Priority

```text
"Đưa tài liệu tới phòng A."
→ NORMAL

"Đưa gấp tài liệu này tới phòng A."
→ HIGH

"Tài liệu này rất gấp, chuyển tới phòng A ngay."
→ HIGH
```

### Conditional policy

```text
"Đưa tài liệu đến phòng A, nếu phòng đang bận thì chờ."
```

Expected:

```text
ifBusy = WAIT
```

### Missing information

```text
"Đưa tài liệu tới phòng họp."
```

Expected:

```text
destination = missing
clarification required
```

### Unsupported command

```text
"Mở Youtube cho tôi."
```

Expected:

```text
UNKNOWN
```

No TaskRequest is submitted.

---

## 20.3 Multi-turn tests

Test:

```text
User: "Đưa tài liệu tới phòng họp."
Robot: "Bạn muốn giao tới phòng họp nào?"
User: "Phòng A."
```

Expected final task:

```text
DELIVER_DOCUMENT
meeting_room_A
```

The final user utterance MUST fill the missing destination of the pending task.

---

## 20.4 Robot-event integration tests

Given:

```json
{
  "event": "USER_DECISION_REQUIRED",
  "taskId": "task_001",
  "reason": "ROOM_BUSY",
  "allowedOptions": ["WAIT", "CANCEL"]
}
```

Expected human-facing response:

```text
"Phòng hiện đang bận. Bạn muốn tôi chờ hay hủy nhiệm vụ?"
```

Then:

```text
User: "Chờ đi."
```

Expected:

```json
{
  "taskId": "task_001",
  "decision": "WAIT"
}
```

---

## 20.5 End-to-end tests using mock robot

Required scenario:

```text
User
│
│ "Đưa tài liệu này tới phòng họp A,
│  nếu phòng bận thì chờ."
│
▼
Intent Parser
│
├── intent = DELIVER_DOCUMENT
├── destinationText = "phòng họp A"
└── ifBusy = WAIT
│
▼
Entity Resolver
│
└── meeting_room_A
│
▼
Validator
│
▼
TaskRequest
│
▼
Mock Robot Gateway
│
▼
TASK_STATUS: MOVING
│
▼
TASK_STATUS: WAITING / ROOM_BUSY
│
▼
TASK_STATUS: COMPLETED
│
▼
Dialogue Manager
│
▼
"Đã giao tài liệu tới phòng họp A."
```

---

# 21. Evaluation metrics

For a labeled NLP test dataset, calculate:

## Intent Accuracy

```text
correct intent predictions / total examples
```

## Destination Resolution Accuracy

```text
correct destination IDs / examples requiring destination resolution
```

## Priority Accuracy

```text
correct priority values / applicable examples
```

## Policy Extraction Accuracy

```text
correct explicit policy extraction / applicable examples
```

## Unsafe Task Submission Rate

Definition:

```text
number of invalid/incomplete/unsupported commands that were still submitted
-----------------------------------------------------------------------
number of invalid/incomplete/unsupported commands
```

Target for the deterministic validation layer:

```text
0 unsafe submissions in the defined automated test suite.
```

This is a validation-layer safety property, not an LLM accuracy guarantee.

---

# 22. Error handling

The system should represent failures explicitly.

Recommended application-level errors:

```text
INVALID_LLM_OUTPUT
UNSUPPORTED_INTENT
MISSING_DESTINATION
UNKNOWN_LOCATION
AMBIGUOUS_LOCATION
ROBOT_UNAVAILABLE
TASK_REJECTED
INVALID_USER_DECISION
```

Behavior:

- never silently invent missing required task data;
- never submit unsupported intent;
- never convert unknown locations into made-up map IDs;
- never send a user decision that is not included in the robot event's allowed options;
- display integration errors in the debug UI/event log.

---

# 23. Logging requirements

Logs should preserve enough information for debugging without mixing robot control logic into the NLP service.

Useful fields:

```text
conversationId
requestId
taskId
eventType
parserResult
validationResult
entityResolutionResult
robotEvent
```

Avoid treating free-form LLM reasoning as application state.

---

# 24. Implementation order for the coding agent

Implement in this order:

```text
1. Domain models
      ↓
2. Schemas
      ↓
3. Validator
      ↓
4. MapCatalog + Entity Resolver
      ↓
5. LLM Intent Parser
      ↓
6. Conversation State
      ↓
7. Dialogue Manager
      ↓
8. RobotTaskGateway interface
      ↓
9. MockRobotTaskGateway
      ↓
10. RobotEvent handler
      ↓
11. Application use cases
      ↓
12. Automated tests
      ↓
13. NLP Test Playground
      ↓
14. Robot Assistant Demo UI
      ↓
15. End-to-end mock demo
```

Do not implement future intents before the MVP delivery flow is working and tested.

---

# 25. Acceptance criteria

The MVP is accepted when all of the following work:

### AC-01 — Basic delivery

Given:

```text
"Đưa tài liệu tới phòng A."
```

The system produces a valid delivery task targeting `meeting_room_A`.

### AC-02 — Paraphrase handling

Semantically equivalent Vietnamese delivery commands produce the same normalized intent.

### AC-03 — Priority extraction

Explicit urgency such as `gấp` or `ngay` can produce `HIGH` priority.

### AC-04 — Explicit busy policy

Given:

```text
"Nếu phòng bận thì chờ."
```

the task contains:

```text
onBusy = WAIT
```

### AC-05 — Missing destination

An incomplete delivery command does not generate a TaskRequest and triggers clarification.

### AC-06 — Multi-turn completion

A clarification answer fills the pending task instead of creating an unrelated command.

### AC-07 — Unknown room

A location absent from MapCatalog never results in an invented location ID.

### AC-08 — Unsupported intent

Unsupported commands do not produce robot tasks.

### AC-09 — Robot feedback

Robot status events are reflected in the UI and conversation state.

### AC-10 — Human decision loop

`USER_DECISION_REQUIRED` can trigger a question to the user and a validated `TaskUpdate` response.

### AC-11 — Debug visibility

The UI displays:

- user/transcript input;
- parsed intent;
- resolved entity;
- validation result;
- task request;
- robot status/event;
- pipeline event log.

### AC-12 — Test playground

Developers can manually enter an utterance and inspect the full NLP pipeline without requiring a real robot or simulator.

### AC-13 — Automated tests

Unit, NLP evaluation, integration, and end-to-end mock tests pass.

---

# 26. Do not implement without an explicit later decision

The following implementation choices are intentionally left open because they depend on the team's environment:

- programming language/framework;
- LLM provider/model;
- Speech-to-Text provider/model;
- persistence/database;
- REST vs WebSocket vs ROS2 transport;
- authentication;
- deployment platform;
- simulator technology;
- exact UI framework.

The coding agent should preserve interfaces so these choices can be plugged in later rather than hard-coding them into the domain layer.

---

# 27. Final system boundary

The NLP subsystem contract can be summarized as:

## Inputs

```text
Human speech/text
MapCatalog
RobotEvent
```

## Outputs

```text
TaskRequest
TaskUpdate
Human-facing dialogue response
```

## Explicitly not responsible for

```text
PDDL
world-state reasoning
navigation
motion control
robot hardware/simulator execution
```

---

# 28. Coding-agent instruction

When implementing this specification:

1. Do not expand the intent ontology beyond the defined MVP.
2. Do not add robot-control logic to the NLP subsystem.
3. Do not let the LLM generate or infer physical world state.
4. Do not allow free-form LLM output to bypass schema validation.
5. Do not invent unknown map entities.
6. Keep the robot integration behind `RobotTaskGateway`.
7. Implement deterministic validation before task submission.
8. Implement a mock robot adapter for tests and UI development.
9. Ensure multi-turn dialogue state is explicit and testable.
10. Keep data contracts strongly typed and serializable.
11. Add automated tests for every acceptance criterion.
12. If an implementation detail is not specified in this document, keep it configurable rather than inventing a domain requirement.

