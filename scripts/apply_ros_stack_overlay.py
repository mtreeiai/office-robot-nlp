"""Apply the reviewed mission/nav overlay to the approved robot repository.

Every existing target must match the inspected base or the already-applied
overlay. Refuse unknown edits; never force/overwrite another developer's work.
"""

import argparse
import hashlib
import json
from pathlib import Path


def apply_overlay(repository, overlay=None):
    repository = Path(repository).resolve()
    overlay = overlay or Path(__file__).resolve().parent.parent / "ros2/stack_overlay"
    manifest = json.loads((overlay / "manifest.json").read_text(encoding="utf-8"))
    pending = []
    for relative, original_hash in manifest["files"].items():
        target = (repository / relative).resolve()
        if not target.is_relative_to(repository) or not relative.startswith("src/"):
            raise ValueError("Overlay target escaped the robot repository")
        desired = (overlay / relative).read_text(encoding="utf-8")
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == desired:
            continue
        if current is None:
            if original_hash is not None:
                raise ValueError(f"Expected ROS base file is missing: {relative}")
        elif hashlib.sha256(current.encode()).hexdigest() != original_hash:
            raise ValueError(f"ROS file has unreviewed edits or a different base: {relative}")
        pending.append((target, desired))
    for target, desired in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".office_robot_tmp")
        temporary.write_text(desired, encoding="utf-8")
        temporary.replace(target)
    return len(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    args = parser.parse_args()
    try:
        count = apply_overlay(args.repository)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
    print(f"Applied {count} reviewed ROS stack files. Rebuild robot_interfaces and both stack packages.")


if __name__ == "__main__":
    main()
