"""The message codes are a contract with the clients: they translate from the ``code`` and its ``params``.

``tests/contracts/error_codes.json`` records every code and its placeholders. Removing a code, or one of its
placeholders, breaks clients and fails here; a new code must be recorded on purpose with
``uv run python scripts/update_contracts.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pachifast.i18n.locale import en, it

LOCK = Path(__file__).parent / "contracts" / "error_codes.json"
PLACEHOLDER = re.compile(r"{(\w+)}")
HOW_TO_RECORD = "run `uv run python scripts/update_contracts.py` to record it"


def _locked() -> dict[str, list[str]]:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def _placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def test_no_code_was_removed() -> None:
    """A client may still receive any code it knew: removing one needs a major release."""
    missing = sorted(set(_locked()) - set(en.MESSAGES))
    assert not missing, f"codes removed from the catalog: {missing}"


def test_no_placeholder_was_removed_from_a_code() -> None:
    """The ``params`` of a code only grow: a client builds the message from them."""
    broken = {
        code: sorted(set(params) - _placeholders(en.MESSAGES[code]))
        for code, params in _locked().items()
        if code in en.MESSAGES and not set(params) <= _placeholders(en.MESSAGES[code])
    }
    assert not broken, f"placeholders removed: {broken}"


def test_new_codes_are_recorded_on_purpose() -> None:
    """Adding a code is fine, but it must show in the diff of the contract file."""
    unrecorded = sorted(set(en.MESSAGES) - set(_locked()))
    assert not unrecorded, f"codes not in the contract file ({HOW_TO_RECORD}): {unrecorded}"


def test_every_language_uses_the_same_placeholders() -> None:
    """A translation that drops or renames a placeholder would render a broken message."""
    mismatched = {
        code: (sorted(_placeholders(en.MESSAGES[code])), sorted(_placeholders(text)))
        for code, text in it.MESSAGES.items()
        if code in en.MESSAGES and _placeholders(text) != _placeholders(en.MESSAGES[code])
    }
    assert not mismatched, f"placeholders differ between English and Italian: {mismatched}"
