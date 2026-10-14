"""Rewrite ``tests/contracts/error_codes.json`` from the catalogs: run it when a code is added on purpose.

Removing a code or one of its parameters is a breaking change for clients and should be done only in a major
release; the contract test fails on it, and this script refuses to drop them unless ``--allow-breaking`` is given.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pachifast.i18n.locale import en

LOCK = Path(__file__).resolve().parents[1] / "tests" / "contracts" / "error_codes.json"
PLACEHOLDER = re.compile(r"{(\w+)}")


def current() -> dict[str, list[str]]:
    """``{code: sorted placeholders}`` of the English catalog."""
    return {code: sorted(set(PLACEHOLDER.findall(text))) for code, text in sorted(en.MESSAGES.items())}


def main() -> None:
    """Write the lock file, keeping removals out unless explicitly allowed."""
    new = current()
    old = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    broken = sorted(code for code in old if code not in new) + sorted(
        code for code in old if code in new and not set(old[code]) <= set(new[code])
    )
    if broken and "--allow-breaking" not in sys.argv:
        raise SystemExit(f"breaking change to codes {broken}: pass --allow-breaking to accept it")
    LOCK.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {LOCK.relative_to(Path.cwd()) if LOCK.is_relative_to(Path.cwd()) else LOCK}: {len(new)} codes")


if __name__ == "__main__":
    main()
