"""Create an allowlisted Docker context: no .env, database or model/map assets."""

import argparse
import shutil
from pathlib import Path

from scripts.apply_ros_stack_overlay import apply_overlay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("robot_repository", type=Path)
    parser.add_argument("context", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    context = args.context.resolve()
    if not context.is_relative_to(root / ".venv"):
        raise SystemExit("Use an isolated context under this repository's .venv directory")
    apply_overlay(args.robot_repository)
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".env*", "*.sqlite3*")
    for name in ("robot_interfaces", "robot_navigation_manager", "robot_mission_control"):
        shutil.copytree(args.robot_repository / "src" / name, context / "src" / name, dirs_exist_ok=True, ignore=ignored)
    shutil.copytree(root / "ros2/office_robot_edge_gateway", context / "src/office_robot_edge_gateway", dirs_exist_ok=True, ignore=ignored)
    shutil.copytree(root / "robot_edge_gateway", context / "core/robot_edge_gateway", dirs_exist_ok=True, ignore=ignored)
    for name in ("Dockerfile", "check_stack.py"):
        shutil.copyfile(root / "tests/ros2" / name, context / name)
    print("Prepared allowlisted ROS test build context; no environment secrets copied.")


if __name__ == "__main__":
    main()
