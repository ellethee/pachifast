"""User settings schema: stable text keys, texts from the catalogs, defaults from the code."""

import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from pydantic import create_model

from pachifast.auth.user_settings_discover import BaseUserSettings, build_ui_schema, get_user_settings_model, ui_field
from pachifast.database.aliased_models import AliasedBaseModel
from pachifast.i18n import configure_catalogs
from pachifast.i18n.catalogs import CATALOGS
from pachifast.i18n.translator import reset_locale, set_locale


class ShopSettings(BaseUserSettings):
    __section_name__ = "shop"
    _title = "Shop"

    view: str = ui_field(
        "grid",
        component="select",
        label="View",
        properties={
            "color": "primary",
            "hint": "How the products are listed",
            "emitValue": True,
            "options": [{"label": "Grid", "value": "grid"}, {"label": "Dark mode", "value": "dark-mode"}],
        },
    )
    limit: int = 10  # a plain field: no label, no properties


def _schema() -> dict:
    model = create_model("UserSettings", __base__=AliasedBaseModel, shop=(ShopSettings, ShopSettings()))
    return build_ui_schema(model)["shop"]


@pytest.fixture(autouse=True)
def _core_catalogs():
    configure_catalogs()
    yield
    configure_catalogs()


def test_every_section_and_labelled_field_has_a_stable_key() -> None:
    schema = _schema()
    assert schema["title"] == "Shop" and schema["titleKey"] == "settings.shop.title"
    view = schema["fields"]["view"]
    assert view["label"] == "View" and view["labelKey"] == "settings.shop.view.label"
    assert view["propertyKeys"] == {"hint": "settings.shop.view.properties.hint"}  # color is not a text
    assert view["optionKeys"] == {"grid": "settings.shop.view.options.grid"}  # "dark-mode" is not a valid key part
    assert view["properties"]["color"] == "primary" and view["properties"]["emitValue"] is True
    limit = schema["fields"]["limit"]
    assert limit == {"component": "number", "default": 10}  # no label, no text: nothing to translate


def test_every_field_with_a_default_exposes_it_in_the_schema() -> None:
    fields = _schema()["fields"]
    assert fields["view"]["default"] == "grid" and fields["limit"]["default"] == 10
    appearance = build_ui_schema(get_user_settings_model())["appearance"]["fields"]
    assert appearance["theme"]["default"] == "auto" and appearance["reduceMotion"]["default"] is False
    preferences = build_ui_schema(get_user_settings_model())["preferences"]["fields"]
    assert preferences["locale"]["default"] is None  # null is a default too


def test_catalog_texts_replace_the_defaults_in_the_current_language() -> None:
    CATALOGS["it"]["settings.shop.title"] = "Negozio"
    CATALOGS["it"]["settings.shop.view.label"] = "Vista"
    CATALOGS["it"]["settings.shop.view.properties.hint"] = "Come sono elencati i prodotti"
    CATALOGS["it"]["settings.shop.view.options.grid"] = "Griglia"
    token = set_locale("it")
    try:
        schema = _schema()
    finally:
        reset_locale(token)
    view = schema["fields"]["view"]
    assert schema["title"] == "Negozio" and view["label"] == "Vista"
    assert view["properties"]["hint"] == "Come sono elencati i prodotti"
    assert view["properties"]["options"][0] == {"label": "Griglia", "value": "grid"}
    assert view["properties"]["options"][1]["label"] == "Dark mode"  # no key, no entry: the default stays
    # the keys do not depend on the language, and the class metadata is never modified
    assert view["labelKey"] == "settings.shop.view.label"
    english = _schema()["fields"]["view"]
    assert english["label"] == "View" and english["properties"]["options"][0]["label"] == "Grid"


def test_a_language_without_the_key_keeps_the_text_written_in_the_code() -> None:
    CATALOGS["it"]["settings.shop.title"] = "Negozio"  # only in it
    assert _schema()["title"] == "Shop"  # the default language is English: no entry, the code's text


def test_builtin_sections_have_their_texts_in_every_core_language() -> None:
    """The keys the schema names for the built-in sections exist in the core catalogs of every language."""
    schema = build_ui_schema(get_user_settings_model())
    keys: set[str] = set()
    for section in schema.values():
        keys.add(section["titleKey"])
        for field in section["fields"].values():
            keys.update(
                [field.get("labelKey"), *field.get("propertyKeys", {}).values(), *field.get("optionKeys", {}).values()]
            )
    keys.discard(None)
    assert {"settings.preferences.title", "settings.audit.disable_activity_log.label"} <= keys
    for language, catalog in CATALOGS.items():
        assert keys <= set(catalog), (language, keys - set(catalog))


def test_builtin_schema_follows_the_language() -> None:
    token = set_locale("it")
    try:
        schema = build_ui_schema(get_user_settings_model())
    finally:
        reset_locale(token)
    assert schema["preferences"]["title"] == "Preferenze"
    assert schema["appearance"]["fields"]["theme"]["label"] == "Tema"
    assert schema["appearance"]["fields"]["theme"]["properties"]["options"][0] == {"label": "Scuro", "value": "dark"}
    assert schema["audit"]["fields"]["disableActivityLog"]["label"].startswith("Disabilita log attività")
