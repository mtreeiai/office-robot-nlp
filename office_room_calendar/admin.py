"""Explicit calendar-only migrations and account provisioning. Never prints secrets."""

import argparse
import getpass
import os

from office_room_calendar.auth import CalendarAuth
from office_room_calendar.config import CalendarSettings
from office_room_calendar.database import CalendarDatabase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate", "user", "robot"])
    parser.add_argument("--email")
    parser.add_argument("--name")
    parser.add_argument("--role", choices=["USER", "ADMIN"], default="USER")
    args = parser.parse_args()
    try:
        settings = CalendarSettings.from_environment()
        database = CalendarDatabase(settings.database_url)
        if args.command == "migrate":
            # Calendar consumes IDs/display names only, never navigation geometry.
            from office_robot_nlp.semantic_map import load_semantic_registry
            registry = load_semantic_registry()
            database.migrate({room_id: room.spoken_name for room_id, room in registry.rooms.items()})
        elif args.command == "user":
            if not args.email or not args.name:
                raise ValueError("Email and display name required")
            password = os.environ.get("CALENDAR_USER_PASSWORD") or getpass.getpass("Calendar account password: ")
            CalendarAuth(database, settings).create_user(args.email, args.name, password, args.role)
        else:
            if not args.name:
                raise ValueError("Robot name required")
            CalendarAuth(database, settings).create_robot(args.name, os.environ["CALENDAR_ROBOT_TOKEN"])
    except Exception:
        raise SystemExit("Calendar setup failed. Check dedicated CALENDAR_* configuration, database permissions and account inputs privately.") from None
    print("Calendar setup completed. No password or token printed.")


if __name__ == "__main__":
    main()
