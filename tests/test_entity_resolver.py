"Exact catalog-based destination resolution and validator integration."

import json
import unittest
from pathlib import Path
from office_robot_nlp.domain.map import MapCatalog, MapLocation
from office_robot_nlp.entity_resolver import resolve_destination
from office_robot_nlp.validation.intent_validator import (
    AmbiguousLocation, ResolvedLocation, UnknownLocation, ValidationError,
    decode_parsed_intent, validate_intent,
)

FIXTURES = Path(__file__).parent / "fixtures"


def catalog(filename: str) -> MapCatalog:
    raw = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    return MapCatalog(raw["version"], [MapLocation(**location) for location in raw["locations"]])

class EntityResolverTests(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog("map_catalog.json")

    def test_configured_aliases_and_name(self):
        for text, expected_id in (
            ("phòng A", "meeting_room_A"),
            ("phòng họp A", "meeting_room_A"),
            ("meeting room A", "meeting_room_A"),
            (" PHÒNG   A ", "meeting_room_A"),
            ("phòng B", "meeting_room_B"),
            ("  PHÒNG    HỌP  B  ", "meeting_room_B"),
        ):
            with self.subTest(text=text):
                self.assertEqual(resolve_destination(text, self.catalog),
                                 ResolvedLocation("RESOLVED", expected_id))

    def test_not_found_never_synthesizes_id(self):
        for text in ("phòng Z", "", "  ", None):
            with self.subTest(text=text):
                self.assertEqual(resolve_destination(text, self.catalog), UnknownLocation("NOT_FOUND"))

    def test_accentless_and_punctuated_aliases_match(self):
        for text in ("phong A", " PHONG   A ", "phòng a.", "phòng A!"):
            with self.subTest(text=text):
                self.assertEqual(resolve_destination(text, self.catalog),
                                 ResolvedLocation("RESOLVED", "meeting_room_A"))

    def test_unrelated_room_does_not_match_after_normalization(self):
        self.assertEqual(resolve_destination("phòng C", self.catalog), UnknownLocation("NOT_FOUND"))

    def test_ambiguous_alias_returns_both_catalog_ids(self):
        ambiguous = catalog("ambiguous_map_catalog.json")
        self.assertEqual(resolve_destination(" PHÒNG  CHUNG ", ambiguous),
                         AmbiguousLocation("AMBIGUOUS", ["meeting_room_A", "meeting_room_B"]))

    def test_duplicate_aliases_within_one_location_do_not_create_ambiguity(self):
        self.assertEqual(resolve_destination("phòng họp A", self.catalog),
                         ResolvedLocation("RESOLVED", "meeting_room_A"))

    def test_duplicate_ids_cannot_silently_choose_between_catalog_entries(self):
        duplicate = MapCatalog("1.0", [
            MapLocation("meeting_room_A", "Phòng A", []),
            MapLocation("meeting_room_A", "Another room", ["phòng A"]),
        ])
        self.assertEqual(resolve_destination("phòng A", duplicate),
                         AmbiguousLocation("AMBIGUOUS", ["meeting_room_A", "meeting_room_A"]))

    def test_parser_result_resolver_validator_happy_path(self):
        raw = {
            "intent": "DELIVER_DOCUMENT", "destinationText": "phòng A",
            "documentText": "tài liệu này", "priority": "NORMAL",
            "constraints": {"ifBusy": None, "ifClosed": None}, "missingFields": [],
        }
        decoded = decode_parsed_intent(raw)
        self.assertTrue(decoded.validation.valid)
        resolution = resolve_destination(decoded.intent.destinationText, self.catalog)
        self.assertEqual(resolution, ResolvedLocation("RESOLVED", "meeting_room_A"))
        self.assertTrue(validate_intent(decoded.intent, resolution, self.catalog).valid)

    def test_parser_result_resolver_validator_unknown_location(self):
        raw = {
            "intent": "DELIVER_DOCUMENT", "destinationText": "phòng Z",
            "documentText": "tài liệu này", "priority": "NORMAL",
            "constraints": {"ifBusy": None, "ifClosed": None}, "missingFields": [],
        }
        decoded = decode_parsed_intent(raw)
        resolution = resolve_destination(decoded.intent.destinationText, self.catalog)
        self.assertEqual(resolution, UnknownLocation("NOT_FOUND"))
        result = validate_intent(decoded.intent, resolution, self.catalog)
        self.assertFalse(result.valid)
        self.assertEqual(result.errors, (ValidationError.UNKNOWN_LOCATION,))

    def test_resolver_ambiguity_is_rejected_by_validator(self):
        ambiguous = catalog("ambiguous_map_catalog.json")
        raw = {
            "intent": "DELIVER_DOCUMENT", "destinationText": "phòng chung",
            "documentText": None, "priority": "NORMAL",
            "constraints": {"ifBusy": None, "ifClosed": None}, "missingFields": [],
        }
        parsed = decode_parsed_intent(raw).intent
        result = validate_intent(parsed, resolve_destination(parsed.destinationText, ambiguous), ambiguous)
        self.assertEqual(result.errors, (ValidationError.AMBIGUOUS_LOCATION,))

if __name__ == "__main__":
    unittest.main()
