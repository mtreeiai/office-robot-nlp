"Semantic location catalog supplied by the map/robot subsystem."

from dataclasses import dataclass
@dataclass
class MapLocation:
    id: str
    name: str
    aliases: list[str]

@dataclass
class MapCatalog:
    version: str
    locations: list[MapLocation]
