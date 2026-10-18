"""Static automation geometry joined to explicit NLP aliases by string room ID.

This module performs local file I/O only. Geometry is never used to infer live
availability, and no navigation or provider requests are made here.
"""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from office_robot_nlp.domain.map import MapCatalog, MapLocation
from office_robot_nlp.entity_resolver import normalize_location_text


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAP_ID = "office_amcl"
FRAME_ID = "map"
# Supplied radians are rounded to three decimals and degrees to one decimal.
YAW_TOLERANCE_DEGREES = 0.1


class SemanticMapConfigurationError(ValueError):
    """An invalid or unreadable registry must prevent runtime startup."""


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class StagingPose:
    x: float
    y: float
    yaw: float
    yaw_deg: float
    frame_id: str = FRAME_ID


@dataclass(frozen=True)
class Door:
    id: str
    p1: Point
    p2: Point
    zone_depth: float
    staging_pose: StagingPose


@dataclass(frozen=True)
class SemanticRoom:
    room_id: str
    spoken_name: str
    source_name: str
    aliases: tuple[str, ...]
    description: str
    room_anchor: Point
    doors: tuple[Door, ...]


@dataclass(frozen=True)
class SemanticRoomRegistry:
    rooms: Mapping[str, SemanticRoom]
    map_id: str = MAP_ID
    frame_id: str = FRAME_ID

    @property
    def map_catalog(self) -> MapCatalog:
        # The NLP model remains unchanged; callers receive their own mutable copy.
        return MapCatalog(
            version=f"{self.map_id}-8a",
            locations=[
                MapLocation(room.room_id, room.spoken_name, list(room.aliases))
                for room in self.rooms.values()
            ],
        )

    def get_room(self, room_id: str) -> SemanticRoom:
        """Look up an exact official string ID; never coerce numbers or guess."""
        return self.rooms[room_id]

    def get_staging_pose(self, room_id: str, door_id: str = "main_door") -> StagingPose:
        for door in self.get_room(room_id).doors:
            if door.id == door_id:
                return door.staging_pose
        raise KeyError(f"Room {room_id!r} has no door {door_id!r}")


def _record(value: object, context: str, required: set[str]) -> dict:
    if not isinstance(value, dict) or not required.issubset(value):
        raise SemanticMapConfigurationError(f"{context}: expected object with {sorted(required)}")
    return value


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SemanticMapConfigurationError(f"{context}: expected non-empty string without outer whitespace")
    return value


def _number(value: object, context: str) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise SemanticMapConfigurationError(f"{context}: expected finite number")
    return value


def _point(value: object, context: str) -> Point:
    data = _record(value, context, {"x", "y"})
    return Point(*(_number(data[key], f"{context}.{key}") for key in ("x", "y")))


def _door(value: object, context: str) -> Door:
    data = _record(value, context, {"id", "p1", "p2", "zone_depth", "staging_pose"})
    pose_context = f"{context}.staging_pose"
    raw_pose = _record(data["staging_pose"], pose_context, {"x", "y", "yaw", "yaw_deg"})
    pose = StagingPose(*(
        _number(raw_pose[key], f"{pose_context}.{key}")
        for key in ("x", "y", "yaw", "yaw_deg")
    ))
    # Compare orientations modulo a full turn; keep both original values intact.
    degrees = math.degrees(pose.yaw)
    if not math.isfinite(degrees) or abs(math.remainder(degrees - pose.yaw_deg, 360)) > YAW_TOLERANCE_DEGREES:
        raise SemanticMapConfigurationError(f"{pose_context}: inconsistent yaw and yaw_deg")
    depth = _number(data["zone_depth"], f"{context}.zone_depth")
    if depth <= 0:
        raise SemanticMapConfigurationError(f"{context}.zone_depth: must be positive")
    return Door(
        _text(data["id"], f"{context}.id"),
        _point(data["p1"], f"{context}.p1"),
        _point(data["p2"], f"{context}.p2"),
        depth,
        pose,
    )


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SemanticMapConfigurationError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _read_rooms(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
        data = _record(data, str(path), {"rooms"})
        if not isinstance(data["rooms"], list) or not data["rooms"]:
            raise SemanticMapConfigurationError("rooms must be a non-empty array")
        return data["rooms"]
    except (OSError, UnicodeError, ValueError) as error:
        raise SemanticMapConfigurationError(f"Cannot load semantic map configuration {path}: {error}") from error


def _configuration_path(value: str | Path | None, variable: str, default: str) -> Path:
    configured = value if value is not None else os.environ.get(variable, default)
    if not str(configured).strip():
        raise SemanticMapConfigurationError(f"{variable}: path must not be empty")
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_semantic_registry(
    registry_path: str | Path | None = None,
    aliases_path: str | Path | None = None,
) -> SemanticRoomRegistry:
    """Load and validate both local configurations, with no fictional fallback.

    Relative paths (including environment overrides) are repository-relative.
    Each source room must have exactly one alias record. Only spoken names and
    explicitly configured aliases participate in existing EntityResolver matching.
    """
    source_path = _configuration_path(registry_path, "ROOM_REGISTRY_PATH", "data/office_rooms.json")
    alias_path = _configuration_path(aliases_path, "ROOM_ALIASES_PATH", "data/office_room_aliases.json")
    source_rooms = {}
    for index, value in enumerate(_read_rooms(source_path)):
        context = f"{source_path}: rooms[{index}]"
        room = _record(value, context, {"room_id", "display_name", "description", "room_anchor", "doors"})
        room_id = _text(room["room_id"], f"{context}.room_id")
        if room_id in source_rooms:
            raise SemanticMapConfigurationError(f"{context}: duplicate room_id {room_id!r}")
        _text(room["display_name"], f"{context}.display_name")
        if not isinstance(room["description"], str):
            raise SemanticMapConfigurationError(f"{context}.description: expected string")
        anchor = _point(room["room_anchor"], f"{context}.room_anchor")
        if not isinstance(room["doors"], list) or not room["doors"]:
            raise SemanticMapConfigurationError(f"{context}.doors: expected non-empty array")
        doors = tuple(_door(door, f"{context}.doors[{i}]") for i, door in enumerate(room["doors"]))
        if len({door.id for door in doors}) != len(doors):
            raise SemanticMapConfigurationError(f"{context}: duplicate door IDs")
        source_rooms[room_id] = (room, anchor, doors)

    rooms = {}
    alias_owners = {}
    for index, value in enumerate(_read_rooms(alias_path)):
        context = f"{alias_path}: rooms[{index}]"
        alias = _record(value, context, {"room_id", "spoken_name", "aliases"})
        room_id = _text(alias["room_id"], f"{context}.room_id")
        if room_id not in source_rooms:
            raise SemanticMapConfigurationError(f"{context}: unknown room_id {room_id!r}")
        if room_id in rooms:
            raise SemanticMapConfigurationError(f"{context}: duplicate room_id {room_id!r}")
        spoken_name = _text(alias["spoken_name"], f"{context}.spoken_name")
        if not isinstance(alias["aliases"], list):
            raise SemanticMapConfigurationError(f"{context}.aliases: expected array")
        aliases = tuple(_text(text, f"{context}.aliases") for text in alias["aliases"])
        for name in (spoken_name, *aliases):
            normalized = normalize_location_text(name)
            if not normalized:
                raise SemanticMapConfigurationError(f"{context}: empty normalized alias")
            if normalized in alias_owners and alias_owners[normalized] != room_id:
                raise SemanticMapConfigurationError(f"{context}: colliding normalized alias {name!r}")
            alias_owners[normalized] = room_id
        source, anchor, doors = source_rooms[room_id]
        rooms[room_id] = SemanticRoom(
            room_id, spoken_name, source["display_name"], aliases,
            source["description"], anchor, doors,
        )
    missing = source_rooms.keys() - rooms.keys()
    if missing:
        raise SemanticMapConfigurationError(f"{alias_path}: missing alias records for {sorted(missing)}")
    return SemanticRoomRegistry(MappingProxyType(rooms))
