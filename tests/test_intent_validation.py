"Runtime schema and deterministic intent validation tests."

import json
import unittest
from dataclasses import asdict
from office_robot_nlp.domain.intent import Action, Constraints, IntentType, ParsedIntent, Priority
from office_robot_nlp.domain.map import MapCatalog, MapLocation
from office_robot_nlp.validation.intent_validator import (
    AmbiguousLocation, ResolvedLocation, UnknownLocation, ValidationError,
    decode_parsed_intent, validate_intent,
)

CATALOG = MapCatalog("1.0", [MapLocation("meeting_room_A", "Phòng họp A", ["phòng A"])])

def payload():
    return {
        "intent": "DELIVER_DOCUMENT",
        "destinationText": "phòng A",
        "documentText": "tài liệu này",
        "priority": "NORMAL",
        "constraints": {"ifBusy": "WAIT", "ifClosed": None},
        "missingFields": [],
    }

def errors(data):
    return set(data.validation.errors)

class IntentValidationTests(unittest.TestCase):
    def test_valid_delivery_round_trip_and_resolution(self):
        data = decode_parsed_intent(json.dumps(payload(), ensure_ascii=False))
        self.assertTrue(data.validation.valid)
        self.assertEqual(data.intent.priority, Priority.NORMAL)
        self.assertEqual(data.intent.constraints.ifBusy, Action.WAIT)
        self.assertEqual(json.loads(json.dumps(asdict(data.intent))), payload())
        self.assertEqual(validate_intent(data.intent, ResolvedLocation("RESOLVED", "meeting_room_A"), CATALOG).errors, ())

    def test_missing_destination(self):
        source = payload()
        source["destinationText"] = "  "
        result = validate_intent(decode_parsed_intent(source).intent)
        self.assertEqual(result.errors, (ValidationError.MISSING_DESTINATION,))
        self.assertFalse(result.valid)

    def test_unknown_intent_not_eligible_for_task(self):
        source = payload()
        source["intent"] = "UNKNOWN"
        self.assertEqual(validate_intent(decode_parsed_intent(source).intent).errors,
                         (ValidationError.UNSUPPORTED_INTENT,))
        source["intent"] = "FETCH_OBJECT"
        self.assertEqual(errors(decode_parsed_intent(source)), {ValidationError.UNSUPPORTED_INTENT})

    def test_invalid_priority(self):
        source = payload()
        source["priority"] = "URGENT"
        self.assertEqual(errors(decode_parsed_intent(source)), {ValidationError.INVALID_PRIORITY})
        source["priority"] = 3
        self.assertEqual(errors(decode_parsed_intent(source)), {ValidationError.INVALID_PRIORITY})

    def test_invalid_busy_policy(self):
        source = payload()
        source["constraints"]["ifBusy"] = "RETRY"
        self.assertEqual(errors(decode_parsed_intent(source)), {ValidationError.INVALID_POLICY})

    def test_invalid_closed_policy(self):
        source = payload()
        source["constraints"]["ifClosed"] = "CANCEL"
        self.assertEqual(errors(decode_parsed_intent(source)), {ValidationError.INVALID_POLICY})

    def test_malformed_payloads(self):
        for bad in ('{invalid', '[]', [], None, {"intent": "DELIVER_DOCUMENT"},
                    dict(payload(), extra="bad"), dict(payload(), constraints={"ifBusy": "WAIT"}),
                    dict(payload(), missingFields=["bad"]), dict(payload(), destinationText=7)):
            with self.subTest(bad=bad):
                result = decode_parsed_intent(bad)
                self.assertIsNone(result.intent)
                self.assertEqual(result.validation.errors, (ValidationError.INVALID_LLM_OUTPUT,))
        self.assertEqual(validate_intent(None).errors, (ValidationError.INVALID_LLM_OUTPUT,))

    def test_untrusted_dataclass_fields_are_revalidated(self):
        bad = ParsedIntent(IntentType.DELIVER_DOCUMENT, "phòng A", None,
                           "URGENT", Constraints(Action.CANCEL, None), [])
        self.assertEqual(set(validate_intent(bad).errors),
                         {ValidationError.INVALID_PRIORITY, ValidationError.INVALID_POLICY})

    def test_resolution_results_and_catalog_guard(self):
        intent = decode_parsed_intent(payload()).intent
        self.assertEqual(validate_intent(intent).errors, (ValidationError.UNRESOLVED_DESTINATION,))
        self.assertEqual(validate_intent(intent, UnknownLocation("NOT_FOUND"), CATALOG).errors,
                         (ValidationError.UNKNOWN_LOCATION,))
        self.assertEqual(validate_intent(intent, AmbiguousLocation("AMBIGUOUS", ["meeting_room_A"]), CATALOG).errors,
                         (ValidationError.AMBIGUOUS_LOCATION,))
        self.assertEqual(validate_intent(intent, ResolvedLocation("RESOLVED", "made_up"), CATALOG).errors,
                         (ValidationError.UNKNOWN_LOCATION,))
        self.assertFalse(validate_intent(intent, ResolvedLocation("RESOLVED", "meeting_room_A")).valid)

    def test_enum_and_string_json_behavior(self):
        source = payload()
        source["priority"] = Priority.HIGH
        source["constraints"]["ifClosed"] = Action.RETRY
        decoded = decode_parsed_intent(json.loads(json.dumps(source)))
        self.assertTrue(decoded.validation.valid)
        self.assertEqual(decoded.intent.priority, Priority.HIGH)
        self.assertEqual(decoded.intent.constraints.ifClosed, Action.RETRY)

if __name__ == "__main__":
    unittest.main()
