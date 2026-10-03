"""Executor entrypoint for Running's state-backed reads and weekly brief."""
import json
import sys

from running_views import answer
from running_actions import execute


if __name__ == "__main__":
    try:
        args = json.load(sys.stdin)
        result = execute(args) if args.get("action") in ("brief", "report", "recover_reports") else answer(args)
        print(json.dumps(result))
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
