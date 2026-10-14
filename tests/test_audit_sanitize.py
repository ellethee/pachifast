import pytest

from pachifast.audit.middleware import MAX_PATH_LENGTH, sanitize_text
from pachifast.auth.user_settings_discover import ui_action, ui_field


def test_control_characters_become_spaces() -> None:
    assert sanitize_text("a\nb\r\x00c\x1b[31m d", 100) == "a b  c [31m d"


def test_long_text_is_truncated_with_an_ellipsis() -> None:
    result = sanitize_text("x" * 1000, MAX_PATH_LENGTH)
    assert result is not None and len(result) == MAX_PATH_LENGTH and result.endswith("…")


def test_none_and_short_text_pass_through() -> None:
    assert sanitize_text(None, 10) is None
    assert sanitize_text("ok", 10) == "ok"


def _extra(field) -> dict:
    return field.json_schema_extra


def test_actions_are_normalised() -> None:
    field = ui_field(
        "x",
        events={"update:modelValue": "toggleDark"},
        watch={"theme": ui_action("applyTheme", mode="dark")},
    )
    assert _extra(field)["x_events"] == {"update:modelValue": {"action": "toggleDark", "args": {}}}
    assert _extra(field)["x_watch"] == {"theme": {"action": "applyTheme", "args": {"mode": "dark"}}}


@pytest.mark.parametrize("code", ["() => $q.dark.toggle()", "alert(1)", "a b", ""])
def test_javascript_is_refused(code: str) -> None:
    with pytest.raises(ValueError):
        ui_field("x", events={"click": code})
    with pytest.raises(ValueError):
        ui_field("x", watch={"theme": code})


def test_malformed_action_is_refused() -> None:
    with pytest.raises(ValueError):
        ui_field("x", events={"click": {"code": "1"}})
    with pytest.raises(ValueError):
        ui_field("x", watch=True)  # type: ignore[arg-type]
