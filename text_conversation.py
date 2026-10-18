"""Thin text-console composition for the office robot NLP pipeline."""

import argparse
import os
import re
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

from office_robot_nlp.clu import (
    ClarificationAnswerResult,
    LanguageUnderstandingParser,
    TaskCommandResult,
    TaskUpdateResult,
    UnknownUtteranceResult,
)
from office_robot_nlp.dialogue_manager import (
    ClarificationRequired,
    DecisionReady,
    DialogueError,
    DialogueManager,
    DialogueResult,
    TaskReady,
    UnsupportedRequest,
)
from office_robot_nlp.domain.conversation import ConversationMode, ConversationState
from office_robot_nlp.domain.map import MapCatalog
from office_robot_nlp.entity_resolver import normalize_location_text
from office_robot_nlp.evaluation import ProviderRuntime, build_runtime
from office_robot_nlp.gemini_adapter import GeminiAdapterError
from office_robot_nlp.ollama_adapter import OllamaAdapterError
from office_robot_nlp.response_plan import (
    GeminiNaturalResponseGenerator,
    NaturalResponseGenerator,
    ResponseAct,
    ResponsePlan,
    TemplateNaturalResponseGenerator,
    generate_with_fallback,
    response_plan_for,
    response_plan_for_lifecycle,
)
from office_robot_nlp.task_lifecycle import (
    DeliveryTaskLifecycle,
    InvalidTaskTransitionError,
    TaskExecutionEvent,
    create_ready_task,
)
from office_robot_nlp.semantic_map import SemanticRoomRegistry, load_semantic_registry
from office_robot_nlp.runtime_monitor import (
    MonitoringEventType,
    RobotStateSnapshot,
    StateSource,
    TaskMonitoringEvent,
    TaskMonitoringRecord,
    apply_monitoring_event,
    render_robot_state,
    render_task_monitor,
    require_event_source,
)


@dataclass(frozen=True)
class ProviderFailure:
    message: str


@dataclass(frozen=True)
class LocalCommandResult:
    text: str


@dataclass(frozen=True)
class ExecutionEventResult:
    lifecycle: DeliveryTaskLifecycle


ConversationOutput = DialogueResult | ProviderFailure | LocalCommandResult | ExecutionEventResult


@dataclass(frozen=True)
class ConversationTurn:
    state: ConversationState
    output: ConversationOutput
    responsePlan: ResponsePlan | None
    responseText: str


def initial_conversation_state() -> ConversationState:
    return ConversationState(
        conversationId="text-demo",
        mode=ConversationMode.IDLE,
        pendingIntent=None,
        awaitingField=None,
        activeTaskId=None,
        allowedDecisions=[],
    )


class TextConversationApplication:
    """Persistent application session for one text conversation."""

    def __init__(
        self,
        parser: LanguageUnderstandingParser,
        map_catalog: MapCatalog,
        provider_runtime: ProviderRuntime | None = None,
        response_generator: NaturalResponseGenerator | None = None,
        room_registry: SemanticRoomRegistry | None = None,
        simulation_enabled: bool = False,
    ):
        self.parser = parser
        self.map_catalog = map_catalog
        self.room_registry = room_registry
        self.dialogue_manager = DialogueManager()
        self.state = initial_conversation_state()
        self.provider_runtime = provider_runtime
        self.response_generator = response_generator or TemplateNaturalResponseGenerator()
        self.template_generator = TemplateNaturalResponseGenerator()
        self._tasks: dict[str, TaskMonitoringRecord] = {}
        self._active_task_id: str | None = None
        self.robot_state = RobotStateSnapshot()
        self.simulation_enabled = simulation_enabled
        self._task_sequence = 0

    @property
    def tasks(self) -> Mapping[str, TaskMonitoringRecord]:
        return MappingProxyType(self._tasks)

    @property
    def active_task(self) -> DeliveryTaskLifecycle | None:
        """Compatibility view of the most recent task, backed by one registry."""
        if self._active_task_id is None:
            return None
        return self._tasks[self._active_task_id].lifecycle

    @active_task.setter
    def active_task(self, task: DeliveryTaskLifecycle | None) -> None:
        # Preserve the Phase 7C fixture API without a second lifecycle store.
        if task is None:
            self._active_task_id = None
            return
        previous = self._tasks.get(task.taskId)
        self._tasks[task.taskId] = (
            replace(previous, lifecycle=task, lifecycleSource=StateSource.NONE, lastEvent=None)
            if previous else TaskMonitoringRecord(task)
        )
        self._active_task_id = task.taskId

    def process_text(self, text: str) -> ConversationTurn:
        if text.lstrip().startswith("/"):
            response = self._process_local_command(text.strip())
            return ConversationTurn(self.state, LocalCommandResult(response), None, response)
        previous_state = self.state
        try:
            clu_result = self.parser.parse(text, previous_state)
        except (GeminiAdapterError, OllamaAdapterError, RuntimeError) as error:
            output = ProviderFailure(str(error))
            plan = ResponsePlan(ResponseAct.PROVIDER_TEMPORARILY_UNAVAILABLE, {})
            return ConversationTurn(
                previous_state,
                output,
                plan,
                self.template_generator.generate(plan, previous_state),
            )

        # Defend against a parser truncating an explicit numeric room list.
        # Corrections such as "đổi từ phòng 1 sang phòng 2" remain CLU's job.
        if isinstance(clu_result, (TaskCommandResult, TaskUpdateResult, ClarificationAnswerResult)):
            if re.search(
                r"\bphong\s*\d+\b\s*(?:va|hoac|&|,|/)\s*(?:phong\s*)?\d+\b",
                normalize_location_text(text),
            ):
                clu_result = UnknownUtteranceResult("Multiple destinations are unsupported")

        handled = self.dialogue_manager.handle(
            previous_state,
            clu_result,
            self.map_catalog,
        )
        self.state = handled.state
        if isinstance(handled.result, TaskReady):
            self._task_sequence += 1
            while f"task_{self._task_sequence:03d}" in self._tasks:
                self._task_sequence += 1
            self.active_task = create_ready_task(
                f"task_{self._task_sequence:03d}",
                handled.result.resolvedIntent,
            )
            plan = response_plan_for_lifecycle(self.active_task)
        else:
            plan = response_plan_for(handled.result)
        response = generate_with_fallback(
            plan,
            self.state,
            self.response_generator,
            self.template_generator,
        )
        return ConversationTurn(self.state, handled.result, plan, response)

    def process_monitoring_event(self, event: TaskMonitoringEvent) -> TaskMonitoringRecord:
        """In-process event boundary only; no sending, transport, or scheduling."""
        require_event_source(event.source)
        if event.source is StateSource.SIMULATED and not self.simulation_enabled:
            raise ValueError("Simulation is disabled. Start with --enable-simulation.")
        record = self._tasks[event.taskId]
        updated = apply_monitoring_event(record, event)
        # Commit only after all validation, including lifecycle transitions, succeeds.
        self._tasks[event.taskId] = updated
        return updated

    def update_robot_state(self, snapshot: RobotStateSnapshot) -> None:
        """Accept an explicitly sourced snapshot; task intake never calls this."""
        require_event_source(snapshot.source)
        if snapshot.source is StateSource.SIMULATED and not self.simulation_enabled:
            raise ValueError("Simulation is disabled. Start with --enable-simulation.")
        self.robot_state = snapshot

    def process_execution_event(
        self, event: TaskExecutionEvent, *, task_id: str | None = None,
        source: StateSource = StateSource.AUTOMATION,
    ) -> ConversationTurn:
        if task_id is None:
            if self.active_task is None:
                raise InvalidTaskTransitionError("No active delivery task is registered.")
            task_id = self.active_task.taskId
        record = self.process_monitoring_event(TaskMonitoringEvent(
            task_id, MonitoringEventType.EXECUTION, source, executionEvent=event,
        ))
        plan = response_plan_for_lifecycle(record.lifecycle)
        plan = replace(plan, facts={**plan.facts, "stateSource": source.value})
        response = generate_with_fallback(
            plan,
            self.state,
            self.response_generator,
            self.template_generator,
        )
        if source is StateSource.SIMULATED:
            response = f"SIMULATED: {response}"
        return ConversationTurn(
            self.state,
            ExecutionEventResult(record.lifecycle),
            plan,
            response,
        )

    def task_snapshot(self, task_id: str) -> str:
        return render_task_monitor(self._tasks[task_id], self.robot_state,
                                   self.room_registry, self.map_catalog)

    def _process_local_command(self, text: str) -> str:
        parts = text.split()
        command = parts[0].casefold()
        if command == "/help" and len(parts) == 1:
            simulation = "ENABLED" if self.simulation_enabled else "DISABLED (use --enable-simulation)"
            return "\n".join([
                "/status              Latest robot state and current/most recent task",
                "/tasks               List locally registered tasks (intake order)",
                "/task <taskId>       Detailed task and map snapshot",
                "/help                Show local commands",
                "/sim document-loaded <taskId>  Explicit SIMULATED load event",
                "/sim task-queued <taskId>      Explicit SIMULATED lifecycle event",
                "/sim task-accepted <taskId>    Explicit SIMULATED acknowledgement",
                f"Simulation: {simulation}",
                "exit / quit          End this in-memory session",
            ])
        if command == "/status" and len(parts) == 1:
            task_id = self.robot_state.currentTaskId
            if task_id not in self._tasks:
                task_id = self._active_task_id or next(reversed(self._tasks), None)
            if task_id is None:
                return render_robot_state(self.robot_state) + "\n\nNo locally registered task. Chưa có nhiệm vụ được đăng ký."
            return "[CURRENT / MOST RECENT TASK]\n" + self.task_snapshot(task_id)
        if command == "/tasks" and len(parts) == 1:
            if not self._tasks:
                return "No locally registered tasks. Chưa có nhiệm vụ được đăng ký."
            rows = ["Task ID | Room ID | Priority | Lifecycle"]
            for record in self._tasks.values():
                task = record.lifecycle
                intent = task.resolvedIntent
                rows.append(f"{task.taskId} | {intent.destination.locationId} | {intent.priority.value} | {task.state.value}")
            return "\n".join(rows)
        if command == "/task":
            if len(parts) != 2:
                return "Usage: /task <taskId>"
            if parts[1] not in self._tasks:
                return f"Unknown task ID: {parts[1]}"
            return self.task_snapshot(parts[1])
        if command == "/sim":
            if not self.simulation_enabled:
                return "Simulation is disabled. Start with --enable-simulation."
            kinds = {
                "document-loaded": MonitoringEventType.DOCUMENT_LOADED,
                "task-queued": MonitoringEventType.EXECUTION,
                "task-accepted": MonitoringEventType.TASK_ACCEPTED,
            }
            if len(parts) != 3 or parts[1] not in kinds:
                return "Usage: /sim {document-loaded|task-queued|task-accepted} <taskId>"
            task_id = parts[2]
            if task_id not in self._tasks:
                return f"Unknown task ID: {task_id}"
            event = TaskMonitoringEvent(
                task_id, kinds[parts[1]], StateSource.SIMULATED,
                executionEvent=TaskExecutionEvent.TASK_QUEUED if parts[1] == "task-queued" else None,
            )
            try:
                self.process_monitoring_event(event)
            except (InvalidTaskTransitionError, ValueError) as error:
                return f"SIMULATED event rejected: {error}"
            return f"SIMULATED event recorded: {parts[1]} for {task_id}\n" + self.task_snapshot(task_id)
        return "Unknown local command or invalid arguments. Use /help."


def create_application(
    *,
    parser: LanguageUnderstandingParser | None = None,
    provider: str | None = None,
    map_catalog: MapCatalog | None = None,
    registry_path: str | None = None,
    aliases_path: str | None = None,
    simulation_enabled: bool = False,
) -> TextConversationApplication:
    if map_catalog is not None and (registry_path is not None or aliases_path is not None):
        raise ValueError("Supply either map_catalog or registry paths, not both.")
    # Validate static data before constructing any provider (Ollama checks health).
    registry = None
    if map_catalog is None:
        registry = load_semantic_registry(registry_path, aliases_path)
        map_catalog = registry.map_catalog
    runtime = None
    if parser is None:
        runtime = build_runtime(provider)
        parser = runtime.parser
    response_generator: NaturalResponseGenerator = TemplateNaturalResponseGenerator()
    if os.environ.get("NLG_ENABLED", "false").casefold() == "true":
        if os.environ.get("NLG_PROVIDER", "gemini").casefold() != "gemini":
            raise ValueError("NLG_PROVIDER must be 'gemini' when NLG_ENABLED=true.")
        response_generator = GeminiNaturalResponseGenerator()
    return TextConversationApplication(
        parser=parser,
        map_catalog=map_catalog,
        provider_runtime=runtime,
        response_generator=response_generator,
        room_registry=registry,
        simulation_enabled=simulation_enabled,
    )


def render_result(output: ConversationOutput) -> str:
    if isinstance(output, LocalCommandResult):
        return output.text
    if isinstance(output, ProviderFailure):
        return "Mình đang gặp sự cố kết nối. Bạn có thể thử lại tin nhắn này sau nhé."
    if isinstance(output, ClarificationRequired):
        if output.candidates:
            candidates = ", ".join(output.candidates)
            return f"{output.responseText} Candidates: {candidates}"
        return output.responseText
    if isinstance(output, TaskReady):
        resolved = output.resolvedIntent
        return "\n".join([
            "Task ready:",
            f"  intent: {resolved.intent.value}",
            f"  destinationText: {resolved.destination.originalText}",
            f"  destinationId: {resolved.destination.locationId}",
            f"  priority: {resolved.priority.value}",
            f"  document: {resolved.document.originalText}",
            f"  recipient: {resolved.recipientText}",
            f"  ifBusy: {resolved.constraints.ifBusy.value if resolved.constraints.ifBusy else None}",
            f"  ifClosed: {resolved.constraints.ifClosed.value if resolved.constraints.ifClosed else None}",
        ])
    if isinstance(output, DecisionReady):
        return f"Decision ready: taskId={output.taskId}, decision={output.decision.value}"
    if isinstance(output, UnsupportedRequest):
        return output.responseText
    if isinstance(output, DialogueError):
        return f"Dialogue error ({output.reason.value}): {output.responseText}"
    return "Conversation result unavailable."


def run_console(application: TextConversationApplication) -> None:
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text.casefold() in {"exit", "quit"}:
            return
        if not text:
            continue
        turn = application.process_text(text)
        if isinstance(turn.output, LocalCommandResult):
            print(turn.responseText)
            continue
        print(f"Assistant: {turn.responseText}")
        if os.environ.get("DEBUG_CONVERSATION", "false").casefold() == "true":
            if isinstance(turn.output, TaskReady):
                print(render_result(turn.output))
                print(application.task_snapshot(application.active_task.taskId))
            if isinstance(turn.output, ProviderFailure):
                # Provider error bodies may contain credentials or request details.
                print("[debug] Provider unavailable; previous runtime state preserved.")
            else:
                print(f"[debug] {turn.output!r}")
            print(f"[debug] TaskLifecycleState: {turn.responsePlan.facts.get('lifecycleState')}")
            print(f"[debug] ResponsePlan: {turn.responsePlan!r}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the office robot text conversation demo.")
    parser.add_argument("--provider", choices=["gemini", "openai", "ollama"])
    parser.add_argument("--list-rooms", action="store_true", help="Validate and list local rooms without a provider.")
    parser.add_argument("--enable-simulation", action="store_true", help="Opt in to explicitly marked local simulated events.")
    arguments = parser.parse_args(argv)
    try:
        if arguments.list_rooms:
            registry = load_semantic_registry()
            print(f"Map: {registry.map_id}; frame: {registry.frame_id}")
            for room in registry.rooms.values():
                print(f"{room.room_id}: {room.spoken_name}")
            return
        application = create_application(provider=arguments.provider, simulation_enabled=arguments.enable_simulation)
    except (GeminiAdapterError, OllamaAdapterError, RuntimeError, ValueError) as error:
        print(f"Setup error: {error}")
        return

    if application.provider_runtime:
        runtime = application.provider_runtime
        print(f"Provider: {runtime.provider}")
        print(f"Model: {runtime.model}")
    run_console(application)


if __name__ == "__main__":
    main()
