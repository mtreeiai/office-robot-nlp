"""Legacy fictional catalog for isolated regression fixtures, never runtime defaults."""

from office_robot_nlp.domain.map import MapCatalog, MapLocation


def demo_map_catalog() -> MapCatalog:
    return MapCatalog(
        version="demo-1.0",
        locations=[
            MapLocation(
                id="meeting_room_A",
                name="Phòng họp A",
                aliases=["phòng A", "phòng họp A", "meeting room A"],
            ),
            MapLocation(
                id="meeting_room_B",
                name="Phòng họp B",
                aliases=["phòng B", "phòng họp B", "meeting room B"],
            ),
        ],
    )
