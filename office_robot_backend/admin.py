"""Explicit schema/bootstrap commands; credentials come from environment, never output."""

import argparse
import json
import os
from pathlib import Path

from office_robot_backend.repository import PostgresRepository
from office_robot_backend.service import DeliveryService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate", "provision"])
    parser.add_argument("--locations", type=Path, help="Operator-approved map version and pickup/delivery locations JSON")
    args = parser.parse_args()
    try:
        repository = PostgresRepository(os.environ["DATABASE_URL"])
        if args.command == "migrate":
            repository.migrate()
        else:
            if args.locations is None:
                raise ValueError("Locations file required")
            config = json.loads(args.locations.read_text(encoding="utf-8"))
            DeliveryService(repository).provision(config["operatorId"], os.environ["OPERATOR_TOKEN"],
                config["robotId"], os.environ["ROBOT_GATEWAY_TOKEN"], config["mapId"], config["locations"], config["frameId"])
    except Exception:
        raise SystemExit("Operational setup failed; check database, migration and provisioned configuration privately.") from None
    print("Operational setup completed; no credentials printed.")


if __name__ == "__main__":
    main()
