import io
import os
import unittest
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.dialogue_manager import ClarificationRequired, TaskReady
from office_robot_nlp.gemini_adapter import GeminiProviderRequestError
from office_robot_nlp.runtime_monitor import (
    ConnectionState, DocumentLoadState, MonitoringEventType, RobotStateSnapshot,
    StateSource, TaskHandoffState, TaskMonitoringEvent, apply_monitoring_event,
    render_robot_state,
)
from office_robot_nlp.task_lifecycle import (
    InvalidTaskTransitionError, TaskExecutionEvent, TaskLifecycleState,
)
from text_conversation import LocalCommandResult, ProviderFailure, create_application, main, run_console


def command(destination="phòng 2", priority="HIGH", recipient="anh Phong", document="tài liệu này"):
    return {
        "type": "TASK_COMMAND",
        "parsedIntent": {
            "intent": "DELIVER_DOCUMENT", "destinationText": destination,
            "documentText": document, "recipientText": recipient, "priority": priority,
            "constraints": {"ifBusy": "WAIT", "ifClosed": "RETRY"},
            "missingFields": ["destination"] if destination is None else [],
        },
    }


class Phase8B1Tests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "NLG_ENABLED": "false", "DEBUG_CONVERSATION": "false",
            "ROOM_REGISTRY_PATH": "data/office_rooms.json",
            "ROOM_ALIASES_PATH": "data/office_room_aliases.json",
        })
        environment.start()
        self.addCleanup(environment.stop)
        network = patch("urllib.request.urlopen", side_effect=AssertionError("No live API in tests"))
        network.start()
        self.addCleanup(network.stop)

    def app(self, responses=None, simulation=False):
        parser = Mock(wraps=FakeLanguageUnderstandingParser(responses or {"deliver": command()}))
        return create_application(parser=parser, simulation_enabled=simulation)

    def registered(self, simulation=False):
        app = self.app(simulation=simulation)
        app.process_text("deliver")
        return app

    def event(self, app, kind, task_id="task_001", source=StateSource.AUTOMATION, execution=None):
        return app.process_monitoring_event(TaskMonitoringEvent(task_id, kind, source, execution))

    def test_task_ready_registers_actual_semantics_and_conservative_defaults(self):
        app = self.app()
        before = app.robot_state
        turn = app.process_text("deliver")
        self.assertIsInstance(turn.output, TaskReady)
        self.assertEqual(list(app.tasks), ["task_001"])
        record = app.tasks["task_001"]
        self.assertIs(record.lifecycle, app.active_task)
        self.assertIs(record.lifecycle.resolvedIntent, turn.output.resolvedIntent)
        self.assertEqual(record.lifecycle.state, TaskLifecycleState.READY)
        self.assertEqual(record.handoff, TaskHandoffState.NOT_SENT)
        self.assertEqual(record.documentLoad, DocumentLoadState.UNCONFIRMED)
        self.assertEqual(record.lifecycleSource, StateSource.NONE)
        self.assertIsNone(record.lastEvent)
        intent = record.lifecycle.resolvedIntent
        self.assertEqual(intent.destination.locationId, "02")
        self.assertEqual(intent.destination.originalText, "phòng 2")
        self.assertEqual(intent.priority.value, "HIGH")
        self.assertEqual(intent.recipientText, "anh Phong")
        self.assertEqual(intent.document.originalText, "tài liệu này")
        self.assertIsNone(intent.document.documentId)
        self.assertEqual(intent.constraints.ifBusy.value, "WAIT")
        self.assertEqual(intent.constraints.ifClosed.value, "RETRY")
        self.assertIs(app.robot_state, before)
        self.assertEqual(before, RobotStateSnapshot())
        self.assertEqual(before.connectionState, ConnectionState.NOT_CONNECTED)
        self.assertEqual(before.operatingState, "UNKNOWN")
        self.assertIsNone(before.locationId)
        self.assertIsNone(before.currentTaskId)
        self.assertIsNone(before.carryingDocumentIds)
        self.assertEqual(before.source, StateSource.NONE)

    def test_room_two_snapshot_uses_real_geometry_and_state(self):
        app = self.registered()
        snapshot = app.process_text("/task task_001").responseText
        for text in (
            "Task ID          : task_001", "Destination ID   : 02", "Priority         : HIGH",
            "Recipient Text   : anh Phong", "Document ID      : UNKNOWN", "Map ID           : office_amcl",
            "Frame            : map", "Door             : main_door", "Staging X        : 23.900",
            "Staging Y        : -4.150", "Staging Yaw      : 1.347", "Lifecycle        : READY",
            "Handoff          : NOT_SENT", "Document Loaded  : UNCONFIRMED",
            "Connection       : NOT_CONNECTED", "Operating State  : UNKNOWN",
            "Location         : UNKNOWN", "Current Task     : NONE", "Carrying Docs    : UNKNOWN",
            "Semantic request : VALID", "Room registered  : YES", "Robot accepted   : NO",
            "Physical delivery: NOT_STARTED", "Execution checks : NOT_CONFIRMED",
        ):
            self.assertIn(text, snapshot)
        self.assertNotIn("Lobby", snapshot)

    def test_snapshot_is_not_the_fixed_room_two_example(self):
        app = self.app({"different": command("phòng 7", "LOW", "chị Lan", "hồ sơ kế toán")})
        app.process_text("different")
        snapshot = app.task_snapshot("task_001")
        for text in ("Destination ID   : 07", "Priority         : LOW", "Recipient Text   : chị Lan",
                     "Document Text    : hồ sơ kế toán", "Staging X        : -8.600", "Staging Yaw      : 0.438"):
            self.assertIn(text, snapshot)
        self.assertNotIn("23.900", snapshot)

    def test_task_registry_keeps_intake_order_without_priority_scheduling(self):
        app = self.app({"low": command("phòng 1", "LOW"), "high": command("phòng 2", "HIGH")})
        app.process_text("low")
        first = app.tasks["task_001"]
        app.process_text("high")
        self.assertEqual(list(app.tasks), ["task_001", "task_002"])
        self.assertIs(app.tasks["task_001"], first)
        self.assertEqual(app.active_task.taskId, "task_002")
        listed = app.process_text("/tasks").responseText
        self.assertIn("task_001 | 01 | LOW | READY", listed)
        self.assertIn("task_002 | 02 | HIGH | READY", listed)
        self.assertLess(listed.index("task_001"), listed.index("task_002"))

    def test_local_commands_never_call_nlu_or_nlg_or_modify_conversation(self):
        app = self.registered()
        app.parser.reset_mock()
        app.response_generator = Mock()
        before = (app.state, dict(app.tasks), app.robot_state)
        for command_text in ("/status", "/tasks", "/task task_001", "/help", "/unknown",
                             "/task absent", "/task", "/status unexpected", "  /tasks  "):
            with self.subTest(command=command_text):
                turn = app.process_text(command_text)
                self.assertIsInstance(turn.output, LocalCommandResult)
                self.assertIsNone(turn.responsePlan)
                self.assertTrue(turn.responseText)
        app.parser.parse.assert_not_called()
        app.response_generator.generate.assert_not_called()
        self.assertEqual((app.state, dict(app.tasks), app.robot_state), before)

    def test_status_and_tasks_before_intake_handle_no_task(self):
        app = self.app()
        status = app.process_text("/status").responseText
        self.assertIn("No locally registered task", status)
        self.assertIn("Connection       : NOT_CONNECTED", status)
        self.assertIn("No locally registered tasks", app.process_text("/tasks").responseText)
        self.assertIn("Unknown task ID", app.process_text("/task missing").responseText)
        app.parser.parse.assert_not_called()

    def test_status_selects_confirmed_current_task_then_most_recent(self):
        app = self.app({"first": command("phòng 2"), "second": command("phòng 5")})
        app.process_text("first")
        app.process_text("second")
        self.assertIn("Task ID          : task_002", app.process_text("/status").responseText)
        snapshot = RobotStateSnapshot(
            robotId="robot_A", connectionState=ConnectionState.CONNECTED,
            operatingState="IDLE", locationId="01", currentTaskId="task_001",
            lastUpdatedAt=datetime(2026, 10, 18, tzinfo=timezone.utc), source=StateSource.AUTOMATION,
        )
        app.update_robot_state(snapshot)
        status = app.process_text("/status").responseText
        self.assertIn("Task ID          : task_001", status)
        self.assertIn("Location         : 01", status)
        self.assertIn("State Source     : AUTOMATION", status)
        self.assertEqual(app.active_task.taskId, "task_002")
        self.assertEqual(app.tasks["task_001"].handoff, TaskHandoffState.NOT_SENT)

    def test_simulation_is_disabled_by_default_at_command_and_event_boundaries(self):
        app = self.registered()
        before = dict(app.tasks)
        self.assertIn("Simulation is disabled", app.process_text("/sim document-loaded task_001").responseText)
        with self.assertRaisesRegex(ValueError, "Simulation is disabled"):
            self.event(app, MonitoringEventType.DOCUMENT_LOADED, source=StateSource.SIMULATED)
        with self.assertRaisesRegex(ValueError, "Simulation is disabled"):
            app.update_robot_state(RobotStateSnapshot(source=StateSource.SIMULATED))
        self.assertEqual(dict(app.tasks), before)
        self.assertEqual(app.robot_state, RobotStateSnapshot())

    def test_simulated_load_is_labeled_and_does_not_verify_document_identity(self):
        app = self.registered(simulation=True)
        robot = app.robot_state
        conversation = app.state
        snapshot = app.process_text("/sim document-loaded task_001").responseText
        record = app.tasks["task_001"]
        self.assertEqual(record.documentLoad, DocumentLoadState.LOADED)
        self.assertEqual(record.documentLoadSource, StateSource.SIMULATED)
        self.assertEqual(record.lastEvent.source, StateSource.SIMULATED)
        self.assertIsNotNone(record.lastEvent.occurredAt.tzinfo)
        self.assertEqual(record.lifecycle.state, TaskLifecycleState.READY)
        self.assertEqual(record.handoff, TaskHandoffState.NOT_SENT)
        self.assertIsNone(record.lifecycle.resolvedIntent.document.documentId)
        self.assertIs(app.robot_state, robot)
        self.assertIsNone(app.robot_state.carryingDocumentIds)
        self.assertEqual(app.state, conversation)
        self.assertIn("SIMULATED event recorded", snapshot)
        self.assertIn("Load Source      : SIMULATED", snapshot)
        self.assertIn("Execution checks : NOT_CONFIRMED", snapshot)

    def test_simulated_queue_reuses_existing_transition_validation(self):
        app = self.registered(simulation=True)
        app.process_text("/sim task-queued task_001")
        record = app.tasks["task_001"]
        self.assertEqual(app.active_task.state, TaskLifecycleState.QUEUED)
        self.assertEqual(record.lifecycleSource, StateSource.SIMULATED)
        self.assertEqual(record.handoff, TaskHandoffState.NOT_SENT)
        self.assertEqual(record.documentLoad, DocumentLoadState.UNCONFIRMED)
        self.assertEqual(app.robot_state, RobotStateSnapshot())
        rejected = app.process_text("/sim task-queued task_001").responseText
        self.assertIn("SIMULATED event rejected", rejected)
        self.assertIs(app.tasks["task_001"], record)

    def test_invalid_skipped_transition_does_not_mutate_monitoring_record(self):
        app = self.registered(simulation=True)
        before = app.tasks["task_001"]
        with self.assertRaises(InvalidTaskTransitionError):
            self.event(app, MonitoringEventType.EXECUTION, source=StateSource.SIMULATED,
                       execution=TaskExecutionEvent.DELIVERY_COMPLETED)
        self.assertIs(app.tasks["task_001"], before)

    def test_simulated_acceptance_remains_separate_from_queue_and_load(self):
        app = self.registered(simulation=True)
        snapshot = app.process_text("/sim task-accepted task_001").responseText
        self.assertEqual(app.tasks["task_001"].handoff, TaskHandoffState.ACCEPTED)
        self.assertEqual(app.tasks["task_001"].handoffSource, StateSource.SIMULATED)
        self.assertEqual(app.active_task.state, TaskLifecycleState.READY)
        self.assertEqual(app.tasks["task_001"].documentLoad, DocumentLoadState.UNCONFIRMED)
        self.assertIn("Robot accepted   : YES (SIMULATED)", snapshot)
        self.assertIn("Execution checks : NOT_CONFIRMED", snapshot)

    def test_simulation_targets_only_named_task_and_preserves_recent_pointer(self):
        app = self.app({"first": command(), "second": command("phòng 3")}, simulation=True)
        app.process_text("first")
        app.process_text("second")
        second = app.tasks["task_002"]
        app.parser.reset_mock()
        app.process_text("/sim document-loaded task_001")
        self.assertIs(app.tasks["task_002"], second)
        self.assertEqual(app.active_task.taskId, "task_002")
        before = dict(app.tasks)
        for command_text in ("/sim task-queued missing", "/sim nonsense task_001", "/sim"):
            response = app.process_text(command_text).responseText
            self.assertTrue("Unknown task ID" in response or "Usage:" in response)
        self.assertEqual(dict(app.tasks), before)
        app.parser.parse.assert_not_called()

    def test_explicit_handoff_events_do_not_queue_or_load_or_change_robot(self):
        app = self.registered()
        for kind, expected in (
            (MonitoringEventType.HANDOFF_PENDING, TaskHandoffState.PENDING_ACK),
            (MonitoringEventType.TASK_ACCEPTED, TaskHandoffState.ACCEPTED),
            (MonitoringEventType.TASK_REJECTED, TaskHandoffState.REJECTED),
        ):
            with self.subTest(kind=kind):
                record = self.event(app, kind)
                self.assertEqual(record.handoff, expected)
                self.assertEqual(record.handoffSource, StateSource.AUTOMATION)
                self.assertEqual(record.lifecycle.state, TaskLifecycleState.READY)
                self.assertEqual(record.documentLoad, DocumentLoadState.UNCONFIRMED)
                self.assertEqual(app.robot_state, RobotStateSnapshot())

    def test_load_confirmation_and_sources_are_independent_of_last_event(self):
        app = self.registered(simulation=True)
        self.event(app, MonitoringEventType.TASK_ACCEPTED)
        self.event(app, MonitoringEventType.DOCUMENT_LOADED, source=StateSource.SIMULATED)
        record = app.tasks["task_001"]
        self.assertEqual(record.handoffSource, StateSource.AUTOMATION)
        self.assertEqual(record.documentLoadSource, StateSource.SIMULATED)
        self.assertEqual(record.lastEvent.source, StateSource.SIMULATED)
        record = self.event(app, MonitoringEventType.DOCUMENT_NOT_LOADED)
        self.assertEqual(record.documentLoad, DocumentLoadState.NOT_LOADED)
        self.assertEqual(record.documentLoadSource, StateSource.AUTOMATION)
        self.assertEqual(record.handoff, TaskHandoffState.ACCEPTED)
        self.assertIsNone(record.lifecycle.resolvedIntent.document.documentId)

    def test_readiness_summary_never_authorizes_or_starts_execution(self):
        app = self.registered()
        self.event(app, MonitoringEventType.TASK_ACCEPTED)
        self.event(app, MonitoringEventType.DOCUMENT_LOADED)
        app.update_robot_state(RobotStateSnapshot(
            connectionState=ConnectionState.CONNECTED, operatingState="IDLE",
            currentTaskId="task_001", source=StateSource.AUTOMATION,
        ))
        snapshot = app.task_snapshot("task_001")
        self.assertIn("PREREQUISITES_RECORDED; execution still requires explicit lifecycle events", snapshot)
        self.assertEqual(app.active_task.state, TaskLifecycleState.READY)

    def test_robot_snapshot_unknown_carrying_differs_from_confirmed_empty(self):
        unknown = RobotStateSnapshot()
        empty = replace(unknown, carryingDocumentIds=(), source=StateSource.AUTOMATION)
        self.assertIn("Carrying Docs    : UNKNOWN", render_robot_state(unknown))
        self.assertIn("Carrying Docs    : NONE (confirmed empty)", render_robot_state(empty))
        with self.assertRaises(FrozenInstanceError):
            unknown.locationId = "01"

    def test_new_task_does_not_change_an_existing_confirmed_robot_snapshot(self):
        app = self.app()
        robot = RobotStateSnapshot(
            robotId="actual-robot", connectionState=ConnectionState.CONNECTED, operatingState="IDLE",
            locationId="07", currentTaskId="external-task", carryingDocumentIds=(),
            lastUpdatedAt=datetime(2026, 10, 18, tzinfo=timezone.utc), source=StateSource.AUTOMATION,
        )
        app.update_robot_state(robot)
        app.process_text("deliver")
        self.assertIs(app.robot_state, robot)
        self.assertEqual(app.tasks["task_001"].documentLoad, DocumentLoadState.UNCONFIRMED)
        self.assertEqual(app.tasks["task_001"].handoff, TaskHandoffState.NOT_SENT)

    def test_provider_429_and_503_preserve_every_existing_state(self):
        for code in (429, 503):
            with self.subTest(code=code):
                app = self.registered(simulation=True)
                app.process_text("/sim document-loaded task_001")
                app.process_text("/sim task-queued task_001")
                robot = RobotStateSnapshot(robotId="R", source=StateSource.AUTOMATION)
                app.update_robot_state(robot)
                before = (app.state, dict(app.tasks), app.active_task, app.robot_state, app._task_sequence)
                app.parser.parse.side_effect = GeminiProviderRequestError("temporary failure", status_code=code)
                turn = app.process_text("another delivery")
                self.assertIsInstance(turn.output, ProviderFailure)
                self.assertIn("sự cố kết nối", turn.responseText)
                self.assertEqual((app.state, dict(app.tasks), app.active_task, app.robot_state, app._task_sequence), before)

    def test_unknown_room_never_creates_monitor_record_or_clears_previous_task(self):
        app = self.app({"known": command(), "unknown": command("phòng 8")})
        app.process_text("known")
        existing = app.active_task
        turn = app.process_text("unknown")
        self.assertIsInstance(turn.output, ClarificationRequired)
        self.assertEqual(list(app.tasks), ["task_001"])
        self.assertIs(app.active_task, existing)
        self.assertEqual(app._task_sequence, 1)

    def test_social_and_task_update_preserve_monitoring_until_ready(self):
        app = self.app({
            "missing": command(None), "hello": {"type": "SOCIAL"},
            "urgent": {"type": "TASK_UPDATE", "priority": "HIGH"},
            "room": {"type": "TASK_UPDATE", "destinationText": "phòng 2"},
        })
        for text in ("missing", "hello", "urgent", "/status"):
            app.process_text(text)
            self.assertEqual(len(app.tasks), 0)
        turn = app.process_text("room")
        self.assertIsInstance(turn.output, TaskReady)
        self.assertEqual(len(app.tasks), 1)
        self.assertEqual(app.tasks["task_001"].lifecycle.resolvedIntent.recipientText, "anh Phong")

    def test_monitoring_validation_is_atomic_and_rejects_missing_provenance(self):
        app = self.registered()
        before = app.tasks["task_001"]
        invalid_events = [
            TaskMonitoringEvent("task_001", MonitoringEventType.DOCUMENT_LOADED, StateSource.NONE),
            TaskMonitoringEvent("task_001", MonitoringEventType.EXECUTION, StateSource.AUTOMATION),
            TaskMonitoringEvent("task_001", MonitoringEventType.DOCUMENT_LOADED, StateSource.AUTOMATION,
                                TaskExecutionEvent.TASK_STARTED),
        ]
        for event in invalid_events:
            with self.subTest(event=event), self.assertRaises(ValueError):
                app.process_monitoring_event(event)
            self.assertIs(app.tasks["task_001"], before)
        with self.assertRaises(KeyError):
            self.event(app, MonitoringEventType.DOCUMENT_LOADED, task_id="unknown")
        with self.assertRaises(ValueError):
            apply_monitoring_event(before, TaskMonitoringEvent("another", MonitoringEventType.TASK_ACCEPTED,
                                                               StateSource.AUTOMATION))
        with self.assertRaises(ValueError):
            app.update_robot_state(RobotStateSnapshot())
        self.assertEqual(app.robot_state, RobotStateSnapshot())

    def test_execution_api_updates_same_record_and_can_target_older_task(self):
        app = self.app({"one": command(), "two": command("phòng 4")})
        app.process_text("one")
        app.process_text("two")
        second = app.tasks["task_002"]
        app.process_execution_event(TaskExecutionEvent.TASK_QUEUED, task_id="task_001")
        self.assertEqual(app.tasks["task_001"].lifecycle.state, TaskLifecycleState.QUEUED)
        self.assertEqual(app.tasks["task_001"].lifecycleSource, StateSource.AUTOMATION)
        self.assertIs(app.tasks["task_002"], second)
        self.assertEqual(app.active_task.taskId, "task_002")

    def test_simulated_execution_api_response_cannot_masquerade_as_automation(self):
        app = self.registered(simulation=True)
        turn = app.process_execution_event(TaskExecutionEvent.TASK_QUEUED, source=StateSource.SIMULATED)
        self.assertTrue(turn.responseText.startswith("SIMULATED:"))
        self.assertEqual(turn.responsePlan.facts["stateSource"], "SIMULATED")

    def test_registry_view_is_read_only_and_ids_cannot_overwrite_legacy_task(self):
        app = self.registered()
        with self.assertRaises(TypeError):
            app.tasks["task_001"] = app.tasks["task_001"]
        first = app.active_task
        app._task_sequence = 0  # Mimic a previously injected lifecycle fixture.
        app.process_text("deliver")
        self.assertEqual(app.active_task.taskId, "task_002")
        self.assertIs(app.tasks["task_001"].lifecycle, first)

    def test_terminal_debug_toggle_and_local_commands(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                app = self.app()
                output = io.StringIO()
                with patch.dict(os.environ, {"DEBUG_CONVERSATION": str(enabled).lower()}), \
                        patch("builtins.input", side_effect=["deliver", "exit"]), redirect_stdout(output):
                    run_console(app)
                self.assertIn("Assistant:", output.getvalue())
                self.assertEqual("ROBOT TASK MONITOR" in output.getvalue(), enabled)
        app = self.registered()
        app.parser.reset_mock()
        output = io.StringIO()
        with patch("builtins.input", side_effect=["/status", "/tasks", "/task task_001", "/help", "exit"]), \
                redirect_stdout(output):
            run_console(app)
        self.assertIn("ROBOT TASK MONITOR", output.getvalue())
        self.assertNotIn("Assistant:", output.getvalue())
        app.parser.parse.assert_not_called()

    def test_provider_failure_debug_does_not_print_error_body_or_credentials(self):
        app = self.app()
        app.parser.parse.side_effect = GeminiProviderRequestError("secret-key-from-provider-error", status_code=429)
        output = io.StringIO()
        with patch.dict(os.environ, {"DEBUG_CONVERSATION": "true", "GEMINI_API_KEY": "secret-env-key"}), \
                patch("builtins.input", side_effect=["deliver", "exit"]), redirect_stdout(output):
            run_console(app)
        self.assertNotIn("secret-key-from-provider-error", output.getvalue())
        self.assertNotIn("secret-env-key", output.getvalue())
        self.assertIn("previous runtime state preserved", output.getvalue())

    def test_cli_passes_explicit_simulation_opt_in(self):
        with patch("text_conversation.create_application") as create, patch("text_conversation.run_console") as run:
            create.return_value.provider_runtime = None
            main(["--enable-simulation"])
            create.assert_called_once_with(provider=None, simulation_enabled=True)
            run.assert_called_once_with(create.return_value)


if __name__ == "__main__":
    unittest.main()
