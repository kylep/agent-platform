"""CLI entrypoint for the TCMS state-App adapter."""
from __future__ import annotations

import json
import sys

from legacy import ToolError
from state import run


def main() -> int:
    try:
        args = json.load(sys.stdin)
        if not isinstance(args, dict):
            raise ToolError("TCMS arguments must be an object")
        out = run(args)
    except (ToolError, ValueError, KeyError, TypeError) as exc:
        print(str(exc)[:500], file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"TCMS unavailable: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
