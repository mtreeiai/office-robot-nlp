"""In-memory conversation bridge, independent of HTTP and Qt."""

from dataclasses import dataclass, field
from threading import Lock, RLock
from typing import Callable
from uuid import uuid4

from office_robot_nlp.api_dto import (
    BatchDTO, BatchSnapshot, MessageRequest, MessageResponse, PreviewDTO, SessionResponse,
    batch_dto, preview_dtos, robot_dto, task_dto, task_snapshot,
)
from office_robot_nlp.dialogue_manager import (
    BatchClarificationRequired, BatchReady, ClarificationRequired, DecisionReady, DialogueError,
    PendingCancelled, SocialAcknowledged, TaskReady, TaskUpdateAccepted, UnsupportedRequest,
)
from office_robot_nlp.gemini_adapter import GeminiAdapterError
from office_robot_nlp.ollama_adapter import OllamaAdapterError
from text_conversation import ProviderFailure, TextConversationApplication, create_application


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class MessageEntry:
    text: str
    response: MessageResponse | None = None


@dataclass
class Session:
    application: TextConversationApplication
    lock: Lock = field(default_factory=Lock)
    messages: dict[str, MessageEntry] = field(default_factory=dict)


_STATUSES = {
    TaskReady: "TASK_READY", BatchReady: "BATCH_READY", ClarificationRequired: "CLARIFICATION_REQUIRED",
    BatchClarificationRequired: "CLARIFICATION_REQUIRED", DecisionReady: "DECISION_READY",
    DialogueError: "DIALOGUE_ERROR", UnsupportedRequest: "UNSUPPORTED", SocialAcknowledged: "SOCIAL",
    TaskUpdateAccepted: "TASK_UPDATE_ACCEPTED", PendingCancelled: "PENDING_CANCELLED",
}


class NlpSessionBridge:
    def __init__(self, application_factory: Callable[[str], TextConversationApplication] | None = None):
        self._factory = application_factory or (lambda session_id: create_application(session_id=session_id))
        self._lock = RLock()
        self._sessions: dict[str, Session] = {}
        self._allocated_session_ids: set[str] = set()
        self._task_owners: dict[str, Session] = {}
        self._batch_owners: dict[str, Session] = {}

    def create_session(self) -> SessionResponse:
        # UUID allocation and publication share a lock, including the collision check.
        with self._lock:
            session_id = "session_" + uuid4().hex
            while session_id in self._allocated_session_ids:
                session_id = "session_" + uuid4().hex
            self._allocated_session_ids.add(session_id)
        # Provider setup (including existing Ollama health checks) never blocks
        # other sessions while holding the shared registry lock.
        try:
            app = self._factory(session_id)
        except (GeminiAdapterError, OllamaAdapterError, RuntimeError, TimeoutError):
            raise BridgeError("PROVIDER_UNAVAILABLE", "Language provider is unavailable.") from None
        if app.state.conversationId != session_id or app.tasks or app.batches or app.simulation_enabled:
            raise ValueError("Session factory must return a fresh, correctly named application with simulation disabled.")
        with self._lock:
            self._sessions[session_id] = Session(app)
        return SessionResponse(sessionId=session_id)

    def _lookup(self, index, resource_id, kind):
        with self._lock:
            session = index.get(resource_id)
        if session is None:
            raise BridgeError(f"{kind}_NOT_FOUND", f"{kind.title()} not found.")
        return session

    def handle_user_message(self, session_id: str, message_id: str, text: str) -> MessageResponse:
        request = MessageRequest(messageId=message_id, text=text)
        session = self._lookup(self._sessions, session_id, "SESSION")
        with session.lock:
            entry = session.messages.get(message_id)
            if entry is not None:
                if entry.text != text:
                    raise BridgeError("MESSAGE_ID_CONFLICT", "Message ID was already used with different content.")
                if entry.response is not None:
                    return entry.response.model_copy(deep=True)
            # Check reused IDs first, even when the changed text is a CLI command.
            if request.text.lstrip().startswith("/"):
                raise BridgeError("INVALID_MESSAGE", "CLI commands are not supported by the conversation API.")
            if entry is None:
                entry = MessageEntry(text)
                session.messages[message_id] = entry
            app = session.application
            with app.conversation_transaction():
                turn = app.process_text(text)
                if isinstance(turn.output, ProviderFailure):
                    raise BridgeError("PROVIDER_UNAVAILABLE", "Language provider is temporarily unavailable. Retry the same message.")
                envelope = None
                if isinstance(turn.output, TaskReady):
                    record = app.tasks[app.active_task.taskId]
                    envelope = BatchDTO(batchId=None, orderPolicy=None, tasks=[task_dto(record)],
                                        orderPreview=[PreviewDTO(position=0, taskId=record.taskId)])
                elif isinstance(turn.output, BatchReady):
                    batch = app.batches[turn.responsePlan.facts["batchId"]]
                    envelope = batch_dto(batch, app.tasks)
                response = MessageResponse(sessionId=session_id, messageId=message_id,
                                           dialogueStatus=_STATUSES[type(turn.output)], reply=turn.responseText,
                                           batch=envelope)
                with self._lock:
                    # Check every ID before publishing either index. No provider calls under this lock.
                    if any(key in self._task_owners and self._task_owners[key] is not session for key in app.tasks):
                        raise ValueError("Task namespace collision")
                    if any(key in self._batch_owners and self._batch_owners[key] is not session for key in app.batches):
                        raise ValueError("Batch namespace collision")
                    tasks = {**self._task_owners, **dict.fromkeys(app.tasks, session)}
                    batches = {**self._batch_owners, **dict.fromkeys(app.batches, session)}
                    cached = response.model_copy(deep=True)
                    self._task_owners, self._batch_owners, entry.response = tasks, batches, cached
                return response

    def get_task(self, task_id: str):
        session = self._lookup(self._task_owners, task_id, "TASK")
        with session.lock:
            return task_snapshot(session.application, session.application.tasks[task_id])

    def get_batch(self, batch_id: str) -> BatchSnapshot:
        session = self._lookup(self._batch_owners, batch_id, "BATCH")
        with session.lock:
            app = session.application
            batch = app.batches[batch_id]
            return BatchSnapshot(batchId=batch.batchId, sessionId=batch.sessionId,
                                 orderPolicy=batch.orderPolicy.value,
                                 tasks=[task_snapshot(app, app.tasks[member.taskId]) for member in batch.tasks],
                                 orderPreview=preview_dtos(batch, app.tasks), robotState=robot_dto(app.robot_state))
