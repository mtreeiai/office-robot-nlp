import copy
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from unittest.mock import patch

from office_robot_nlp.entity_resolver import resolve_destination
from office_robot_nlp.semantic_map import (
    PROJECT_ROOT,
    SemanticMapConfigurationError,
    load_semantic_registry,
)
from office_robot_nlp.validation.intent_validator import ResolvedLocation, UnknownLocation


class SemanticMapTests(unittest.TestCase):
    def setUp(self):
        self.source_path = PROJECT_ROOT / "data/office_rooms.json"
        self.alias_path = PROJECT_ROOT / "data/office_room_aliases.json"
        self.source = json.loads(self.source_path.read_text(encoding="utf-8"))
        self.aliases = json.loads(self.alias_path.read_text(encoding="utf-8"))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def load(self, source=None, aliases=None):
        source_path = Path(self.directory.name) / "rooms.json"
        alias_path = Path(self.directory.name) / "aliases.json"
        source_path.write_text(json.dumps(self.source if source is None else source), encoding="utf-8")
        alias_path.write_text(json.dumps(self.aliases if aliases is None else aliases), encoding="utf-8")
        return load_semantic_registry(source_path, alias_path)

    def test_all_official_ids_names_and_metadata(self):
        registry = self.load()
        self.assertEqual(list(registry.rooms), [f"{number:02d}" for number in range(1, 8)])
        self.assertEqual(registry.map_id, "office_amcl")
        self.assertEqual(registry.frame_id, "map")
        self.assertEqual([room.source_name for room in registry.rooms.values()],
                         ["phong1", "phong2", "phong3", "phong4", "phong5", "phong giai lao", "an"])
        for number, room in enumerate(registry.rooms.values(), 1):
            self.assertIs(type(room.room_id), str)
            self.assertEqual(room.spoken_name, f"Phòng {number}")
            self.assertEqual(room.aliases, (f"phòng {number}", f"phong {number}", f"phong{number}"))

    def test_exact_staging_poses(self):
        expected = [
            (8.700, 1.450, 1.720, 98.5), (23.900, -4.150, 1.347, 77.2),
            (13.600, 0.650, -2.570, -147.3), (12.800, 7.600, -1.658, -95.0),
            (5.250, -0.300, 1.422, 81.5), (-3.950, 1.600, -2.744, -157.2),
            (-8.600, -5.050, 0.438, 25.1),
        ]
        registry = self.load()
        for number, coordinates in enumerate(expected, 1):
            pose = registry.get_staging_pose(f"{number:02d}", "main_door")
            self.assertEqual((pose.x, pose.y, pose.yaw, pose.yaw_deg), coordinates)
            self.assertEqual(pose.frame_id, "map")

    def test_every_source_geometry_field_survives_loading(self):
        registry = self.load()
        for source in self.source["rooms"]:
            room = registry.get_room(source["room_id"])
            reconstructed = {
                "room_id": room.room_id, "display_name": room.source_name,
                "description": room.description, "room_anchor": asdict(room.room_anchor),
                "doors": [],
            }
            for door in room.doors:
                raw = asdict(door)
                raw["staging_pose"].pop("frame_id")
                reconstructed["doors"].append(raw)
            self.assertEqual(reconstructed, source)

    def test_read_only_lookup_and_no_numeric_coercion(self):
        registry = self.load()
        with self.assertRaises(TypeError):
            registry.rooms["08"] = registry.get_room("01")
        with self.assertRaises(FrozenInstanceError):
            registry.get_staging_pose("01").x = 100
        for key in (1, "1", "08"):
            with self.subTest(key=key), self.assertRaises(KeyError):
                registry.get_room(key)
        with self.assertRaises(KeyError):
            registry.get_staging_pose("01", "unknown_door")
        catalog = registry.map_catalog
        catalog.locations[0].aliases.append("unapproved")
        self.assertNotIn("unapproved", registry.map_catalog.locations[0].aliases)

    def test_rejects_duplicate_source_ids(self):
        self.source["rooms"].append(copy.deepcopy(self.source["rooms"][0]))
        with self.assertRaisesRegex(SemanticMapConfigurationError, "duplicate room_id"):
            self.load()

    def test_rejects_duplicate_alias_records(self):
        self.aliases["rooms"].append(copy.deepcopy(self.aliases["rooms"][0]))
        with self.assertRaisesRegex(SemanticMapConfigurationError, "duplicate room_id"):
            self.load()

    def test_rejects_normalized_alias_and_spoken_name_collisions(self):
        for key, value in (("aliases", ["PHÒNG   1."]), ("spoken_name", "PHONG 1")):
            with self.subTest(key=key):
                aliases = copy.deepcopy(self.aliases)
                aliases["rooms"][1][key] = value
                with self.assertRaisesRegex(SemanticMapConfigurationError, "colliding normalized alias"):
                    self.load(aliases=aliases)

    def test_rejects_unknown_alias_room_reference(self):
        self.aliases["rooms"][0]["room_id"] = "08"
        with self.assertRaisesRegex(SemanticMapConfigurationError, "unknown room_id"):
            self.load()

    def test_rejects_missing_alias_record(self):
        self.aliases["rooms"].pop()
        with self.assertRaisesRegex(SemanticMapConfigurationError, "missing alias records"):
            self.load()

    def test_rejects_integer_room_ids_in_either_file(self):
        for target in ("source", "aliases"):
            with self.subTest(target=target):
                source, aliases = copy.deepcopy(self.source), copy.deepcopy(self.aliases)
                (source if target == "source" else aliases)["rooms"][0]["room_id"] = 1
                with self.assertRaises(SemanticMapConfigurationError):
                    self.load(source, aliases)

    def test_rejects_malformed_roots_and_room_records(self):
        for malformed in (None, [], {}, {"rooms": []}, {"rooms": {}}, {"rooms": [None]}, {"rooms": [{}]}):
            for target in ("source", "aliases"):
                with self.subTest(target=target, malformed=malformed):
                    # Write directly so JSON null is not interpreted as the helper's default.
                    path = Path(self.directory.name) / "invalid.json"
                    path.write_text(json.dumps(malformed), encoding="utf-8")
                    with self.assertRaises(SemanticMapConfigurationError):
                        load_semantic_registry(path if target == "source" else self.source_path,
                                               path if target == "aliases" else self.alias_path)

    def test_rejects_malformed_room_geometry(self):
        mutations = [
            ("room_anchor", None), ("room_anchor", {"x": 1}), ("doors", []),
            ("doors", None), ("doors", [None]), ("description", None), ("display_name", ""),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                source = copy.deepcopy(self.source)
                source["rooms"][0][key] = value
                with self.assertRaises(SemanticMapConfigurationError):
                    self.load(source=source)

    def test_rejects_missing_and_malformed_staging_poses(self):
        for value in (None, [], {}, {"x": 1, "y": 2, "yaw": 0}, {"x": "1", "y": 2, "yaw": 0, "yaw_deg": 0}):
            with self.subTest(value=value):
                source = copy.deepcopy(self.source)
                source["rooms"][0]["doors"][0]["staging_pose"] = value
                with self.assertRaises(SemanticMapConfigurationError):
                    self.load(source=source)
        del self.source["rooms"][0]["doors"][0]["staging_pose"]
        with self.assertRaises(SemanticMapConfigurationError):
            self.load()

    def test_rejects_nonfinite_or_nonnumeric_values_everywhere(self):
        paths = [
            ("room_anchor", "x"), ("room_anchor", "y"),
            ("doors", 0, "p1", "x"), ("doors", 0, "p2", "y"),
            ("doors", 0, "zone_depth"),
            *(("doors", 0, "staging_pose", key) for key in ("x", "y", "yaw", "yaw_deg")),
        ]
        for path in paths:
            for invalid in (float("nan"), float("inf"), -float("inf"), True, "1", None):
                with self.subTest(path=path, invalid=invalid):
                    source = copy.deepcopy(self.source)
                    cursor = source["rooms"][0]
                    for key in path[:-1]:
                        cursor = cursor[key]
                    cursor[path[-1]] = invalid
                    with self.assertRaises(SemanticMapConfigurationError):
                        self.load(source=source)

    def test_rejects_nonpositive_zone_depth(self):
        for depth in (0, -0.3):
            with self.subTest(depth=depth):
                self.source["rooms"][0]["doors"][0]["zone_depth"] = depth
                with self.assertRaisesRegex(SemanticMapConfigurationError, "must be positive"):
                    self.load()

    def test_rejects_duplicate_door_ids(self):
        doors = self.source["rooms"][0]["doors"]
        doors.append(copy.deepcopy(doors[0]))
        with self.assertRaisesRegex(SemanticMapConfigurationError, "duplicate door IDs"):
            self.load()

    def test_rejects_inconsistent_yaw_without_correcting_source(self):
        pose = self.source["rooms"][0]["doors"][0]["staging_pose"]
        pose["yaw_deg"] += 1
        with self.assertRaisesRegex(SemanticMapConfigurationError, "inconsistent yaw"):
            self.load()
        self.assertEqual(pose["yaw"], 1.720)
        self.assertEqual(pose["yaw_deg"], 99.5)

    def test_rejects_invalid_alias_types_and_empty_normalized_aliases(self):
        for field, value in (("spoken_name", ""), ("aliases", "phòng 1"), ("aliases", [1]),
                             ("aliases", [""]), ("aliases", ["..."])):
            with self.subTest(field=field, value=value):
                aliases = copy.deepcopy(self.aliases)
                aliases["rooms"][0][field] = value
                with self.assertRaises(SemanticMapConfigurationError):
                    self.load(aliases=aliases)

    def test_missing_bad_json_and_duplicate_json_keys_fail_clearly(self):
        path = Path(self.directory.name) / "bad.json"
        with self.assertRaisesRegex(SemanticMapConfigurationError, "Cannot load.*bad.json"):
            load_semantic_registry(path, self.alias_path)
        for content in ('{"rooms":', '{"rooms": [], "rooms": []}'):
            path.write_text(content, encoding="utf-8")
            with self.assertRaises(SemanticMapConfigurationError):
                load_semantic_registry(path, self.alias_path)

    def test_explicit_paths_and_environment_overrides(self):
        self.load()  # Write temp files.
        with patch.dict(os.environ, {
            "ROOM_REGISTRY_PATH": str(Path(self.directory.name) / "rooms.json"),
            "ROOM_ALIASES_PATH": str(Path(self.directory.name) / "aliases.json"),
        }):
            self.assertEqual(len(load_semantic_registry().rooms), 7)
        for variable in ("ROOM_REGISTRY_PATH", "ROOM_ALIASES_PATH"):
            with patch.dict(os.environ, {variable: ""}):
                with self.assertRaisesRegex(SemanticMapConfigurationError, "path must not be empty"):
                    load_semantic_registry()

    def test_existing_resolver_matches_confirmed_names_only(self):
        catalog = self.load().map_catalog
        for number in range(1, 8):
            for text in (f"Phòng {number}", f"phong {number}", f"phong{number}", f" PHÒNG   {number}. "):
                with self.subTest(text=text):
                    self.assertEqual(resolve_destination(text, catalog), ResolvedLocation("RESOLVED", f"{number:02d}"))
        for text in ("Phòng 8", "Phòng A", "Phòng B", "Phòng 10", "01", "phong giai lao", "an",
                     "anh Phong", "phòng 1 và phòng 2"):
            with self.subTest(text=text):
                self.assertIsInstance(resolve_destination(text, catalog), UnknownLocation)


if __name__ == "__main__":
    unittest.main()
