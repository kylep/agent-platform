"""Kai's Judgment tool over its database-owned state App.

The executor owns the one-call credential and exposes only the loopback
App-data proxy to this process. The former SQL adapter remains in legacy.py
for a measured rollback, but is never selected by model-controlled input.
"""
from __future__ import annotations

import json
import sys

from state import JudgmentError, run


def main() -> int:
    try:
        args = json.load(sys.stdin)
        if not isinstance(args, dict):
            raise JudgmentError("Judgment arguments must be an object")
        result = run(args)
    except (JudgmentError, ValueError, KeyError, TypeError) as exc:
        print(str(exc)[:500], file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Judgment unavailable: {type(exc).__name__}: {str(exc)[:300]}",
              file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
