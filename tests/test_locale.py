"""``pachifast locale``: app catalogs, project overrides, check and export for the frontend."""

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pachifast.cli import app
from pachifast.i18n import configure_catalogs, translate
from pachifast.i18n.exporter import nest, to_vue_i18n
from pachifast.i18n.loader import LocaleError, LocaleLayer, check_layers, collect_layers, load_locale_directory

runner = CliRunner()
PROJECT_PACKAGES = ("apps", "common", "project_modules", "locale")


@pytest.fixture(autouse=True)
def _isolated_catalogs():
    """Projects of different tests share the names ``apps``/``common``: forget them, and restore the core catalogs."""

    def forget() -> None:
        for name in [n for n in sys.modules if n.split(".")[0] in PROJECT_PACKAGES]:
            del sys.modules[name]

    forget()
    yield
    forget()
    configure_catalogs()


def _write(path: Path, messages: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"MESSAGES = {messages!r}\n", encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A generated project with the ``billing`` and ``shop`` apps."""
    assert (
        runner.invoke(
            app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "--no-git"]
        ).exit_code
        == 0
    )
    root = tmp_path / "demo"
    for name in ("billing", "shop"):
        assert runner.invoke(app, ["new", "app", name, "--base-dir", str(root)]).exit_code == 0
    return root


def _locale(root: Path, *args: str):
    return runner.invoke(app, ["locale", *args, "--base-dir", str(root)])


def _filled(root: Path) -> None:
    _write(root / "apps/billing/locale/en.py", {"billing.invoice_not_found": "Invoice {number} not found"})
    _write(root / "apps/billing/locale/it.py", {"billing.invoice_not_found": "Fattura {number} non trovata"})
    _write(root / "apps/shop/locale/en.py", {"shop.cart.empty": "Your cart is empty", "shop.cart.full": "Cart full"})
    _write(root / "apps/shop/locale/it.py", {"shop.cart.empty": "Il carrello è vuoto", "shop.cart.full": "Pieno"})


# --- scaffold ---------------------------------------------------------------


def test_new_app_creates_a_locale_package_declared_in_its_module(project: Path) -> None:
    locale_dir = project / "apps/billing/locale"
    assert (locale_dir / "__init__.py").is_file() and (locale_dir / "en.py").is_file()
    assert 'locale_package="apps.billing.locale"' in (project / "apps/billing/module.py").read_text(encoding="utf-8")
    result = _locale(project, "check")
    assert result.exit_code == 0, result.output
    assert "apps.billing.locale" in result.output and "apps.shop.locale" in result.output


def test_new_common_creates_a_locale_package(tmp_path: Path, project: Path) -> None:
    assert runner.invoke(app, ["new", "common", "shared", "--base-dir", str(project)]).exit_code == 0
    assert (project / "common/shared/locale/en.py").is_file()
    assert 'locale_package="common.shared.locale"' in (project / "common/shared/module.py").read_text(encoding="utf-8")


# --- loading and checking ---------------------------------------------------


def test_any_file_name_that_is_a_language_tag_is_a_language(tmp_path: Path) -> None:
    _write(tmp_path / "pt-BR.py", {"a.b": "x"})
    _write(tmp_path / "_private.py", {"ignored.x": "x"})
    assert load_locale_directory(tmp_path) == {"pt-BR": {"a.b": "x"}}
    _write(tmp_path / "x.py", {"a.b": "x"})
    with pytest.raises(LocaleError, match="Invalid language"):
        load_locale_directory(tmp_path)


def test_broken_locale_files_are_reported_with_their_path(tmp_path: Path) -> None:
    (tmp_path / "en.py").write_text("NOT_MESSAGES = {}\n", encoding="utf-8")
    with pytest.raises(LocaleError, match="must define MESSAGES"):
        load_locale_directory(tmp_path)
    (tmp_path / "en.py").write_text("MESSAGES = {'a.b': 3}\n", encoding="utf-8")
    with pytest.raises(LocaleError, match="str codes to str texts"):
        load_locale_directory(tmp_path)
    (tmp_path / "en.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    with pytest.raises(LocaleError, match="boom"):
        load_locale_directory(tmp_path)


def _problems(*layers: LocaleLayer) -> list[str]:
    return [problem.message for problem in check_layers(list(layers))]


def test_check_finds_what_would_break_a_client() -> None:
    mod = lambda name, messages: LocaleLayer(name, "module", messages)  # noqa: E731
    assert _problems(mod("a", {"en": {"a.x": "X"}, "it": {"a.x": "X"}})) == []

    assert any(
        "missing 1 message(s): a.y" in p
        for p in _problems(mod("a", {"en": {"a.x": "X", "a.y": "Y"}, "it": {"a.x": "X"}}))
    )
    assert any("different fields" in p for p in _problems(mod("a", {"en": {"a.x": "{n}"}, "it": {"a.x": "{m}"}})))
    assert any("invalid code" in p for p in _problems(mod("a", {"en": {"a b": "X"}})))
    assert any("unsupported field" in p for p in _problems(mod("a", {"en": {"a.x": "{n.attr}"}})))
    assert any("prefix of other codes" in p for p in _problems(mod("a", {"en": {"a": "X", "a.b": "Y"}})))
    clash = _problems(mod("one", {"en": {"a.x": "1"}}), mod("two", {"en": {"a.x": "2"}}))
    assert any("defined by one and by two" in p for p in clash)
    # only the project's locale/ directory may redefine a code of another layer
    override = LocaleLayer("locale/", "override", {"en": {"a.x": "mine"}})
    assert _problems(mod("one", {"en": {"a.x": "1"}}), override) == []


def test_check_command_fails_on_a_missing_translation(project: Path) -> None:
    _filled(project)
    _write(project / "apps/billing/locale/it.py", {})  # the Italian billing message is gone
    result = _locale(project, "check")
    assert result.exit_code == 1
    assert "[it] missing 1 message(s): billing.invoice_not_found" in " ".join(result.output.split())


# --- override and runtime ---------------------------------------------------


def test_project_locale_directory_overrides_core_and_modules(project: Path) -> None:
    _filled(project)
    _write(project / "locale/en.py", {"auth.invalid_credentials": "Wrong user or password"})
    _write(project / "locale/it.py", {"billing.invoice_not_found": "Nessuna fattura {number}"})
    layers = collect_layers(_modules(project), project)
    assert [layer.kind for layer in layers] == ["core", "module", "module", "override"]
    catalogs = configure_catalogs(_modules(project), project)
    assert translate("auth.invalid_credentials", locale="en") == "Wrong user or password"
    assert translate("auth.invalid_credentials", locale="it") == "Credenziali non valide"  # not overridden in it
    assert translate("billing.invoice_not_found", {"number": 7}, "it") == "Nessuna fattura 7"
    assert translate("shop.cart.empty", locale="it") == "Il carrello è vuoto"
    assert "billing.invoice_not_found" in catalogs["en"]


def _modules(root: Path):
    from pachifast.migrations.runtime import _load_project_modules

    return _load_project_modules(root)


# --- export -----------------------------------------------------------------


def test_vue_i18n_conversion_escapes_the_special_characters() -> None:
    assert to_vue_i18n("Hello {name}") == "Hello {name}"
    assert to_vue_i18n("mail a@b | 5$ {{x}}") == "mail a{'@'}b {'|'} 5$ {'{'}x{'}'}"
    assert nest({"a.b": "x", "a.c.d": "y", "e": "z"}) == {"a": {"b": "x", "c": {"d": "y"}}, "e": "z"}
    with pytest.raises(ValueError, match="already a text"):
        nest({"a": "x", "a.b": "y"})


def test_export_writes_one_nested_json_per_language(project: Path, tmp_path: Path) -> None:
    _filled(project)
    (tmp_path / "frontend").mkdir()
    dest = tmp_path / "frontend" / "locales"  # the last folder is created, its parents must exist
    result = _locale(project, "export", "--dest-path", str(dest))
    assert result.exit_code == 0, result.output
    assert sorted(path.name for path in dest.iterdir()) == ["en.json", "it.json"]
    english = json.loads((dest / "en.json").read_text(encoding="utf-8"))
    assert english["billing"] == {"invoice_not_found": "Invoice {number} not found"}
    assert english["shop"]["cart"] == {"empty": "Your cart is empty", "full": "Cart full"}
    assert english["auth"]["invalid_credentials"] == "Invalid credentials"  # the core is included
    italian = json.loads((dest / "it.json").read_text(encoding="utf-8"))
    assert italian["shop"]["cart"]["empty"] == "Il carrello è vuoto"  # UTF-8, not \u escapes
    assert "è" in (dest / "it.json").read_text(encoding="utf-8")


def test_export_namespace_core_and_unsafe_destination(project: Path, tmp_path: Path) -> None:
    _filled(project)
    dest = tmp_path / "out"
    result = _locale(project, "export", "--dest-path", str(dest), "--namespace", "errors.codes", "--no-core")
    assert result.exit_code == 0, result.output
    english = json.loads((dest / "en.json").read_text(encoding="utf-8"))
    assert list(english) == ["errors"] and list(english["errors"]["codes"]) == ["billing", "shop"]
    # the parents of the destination are not created behind your back
    missing = _locale(project, "export", "--dest-path", str(tmp_path / "no" / "such" / "dir"))
    assert missing.exit_code == 1 and not (tmp_path / "no").exists()


def test_export_check_and_regeneration(project: Path, tmp_path: Path) -> None:
    _filled(project)
    dest = tmp_path / "out"
    dest.mkdir()
    assert _locale(project, "export", "--dest-path", str(dest), "--check").exit_code == 1  # nothing there yet
    assert list(dest.iterdir()) == []  # --check never writes
    assert _locale(project, "export", "--dest-path", str(dest)).exit_code == 0
    assert _locale(project, "export", "--dest-path", str(dest), "--check").exit_code == 0

    first = (dest / "en.json").read_bytes()
    assert "Nothing to update" in _locale(project, "export", "--dest-path", str(dest)).output
    assert (dest / "en.json").read_bytes() == first  # stable output

    (dest / "en.json").write_text(
        "{}\n", encoding="utf-8"
    )  # edited by hand: the export does not read it, it replaces it
    stale = _locale(project, "export", "--dest-path", str(dest), "--check")
    assert stale.exit_code == 1
    assert "Regenerate the files with the same command without --check" in " ".join(stale.output.split())
    assert _locale(project, "export", "--dest-path", str(dest)).exit_code == 0
    assert (dest / "en.json").read_bytes() == first

    (dest / "fr.json").write_text("{}\n", encoding="utf-8")  # a language that is not generated any more
    result = _locale(project, "export", "--dest-path", str(dest))
    assert "Not generated any more" in result.output and (dest / "fr.json").exists()  # reported, never deleted


def test_export_refuses_to_write_when_the_catalogs_have_problems(project: Path, tmp_path: Path) -> None:
    _filled(project)
    _write(project / "apps/shop/locale/it.py", {"shop.cart.empty": "Il carrello è vuoto"})  # shop.cart.full is missing
    dest = tmp_path / "out"
    dest.mkdir()
    result = _locale(project, "export", "--dest-path", str(dest))
    assert result.exit_code == 1 and "shop.cart.full" in " ".join(result.output.split())
    assert list(dest.iterdir()) == []


def test_locale_commands_need_a_project(tmp_path: Path) -> None:
    result = runner.invoke(app, ["locale", "check", "--base-dir", str(tmp_path)])
    assert result.exit_code == 1 and "project_modules.py not found" in " ".join(result.output.split())


def test_create_app_loads_the_catalogs_of_the_modules_and_the_project(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from pachifast import create_app
    from pachifast.config import get_settings
    from pachifast.i18n import api_error

    _filled(project)
    _write(project / "locale/en.py", {"auth.not_authenticated": "Please sign in"})
    monkeypatch.chdir(project)  # the project's locale/ is looked up in the current directory
    monkeypatch.setenv("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}")
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    get_settings.cache_clear()
    try:
        fastapi_app = create_app(_modules(project))

        @fastapi_app.get("/invoice")
        async def invoice():
            raise api_error(404, "billing.invoice_not_found", number=42)

        client = TestClient(fastapi_app)
        item = client.get("/invoice").json()["detail"][0]
        assert item["msg"] == "Invoice 42 not found" and item["code"] == "billing.invoice_not_found"
        assert item["params"] == {"number": 42}
        anonymous = client.get("/api/auth/user")
        assert anonymous.json()["detail"][0]["msg"] == "Please sign in"  # the project's override of a core message
    finally:
        get_settings.cache_clear()


def test_settings_command_lists_the_translatable_texts(project: Path) -> None:
    (project / "apps/shop/user_settings.py").write_text(
        "from pachifast.auth.user_settings_discover import BaseUserSettings, ui_field\n\n\n"
        "class ShopSettings(BaseUserSettings):\n"
        '    __section_name__ = "shop"\n'
        '    _title = "Shop"\n\n'
        '    view: str = ui_field("grid", component="select", label="View",\n'
        '                         properties={"hint": "Listing", "options": [{"label": "Grid", "value": "grid"}]})\n',
        encoding="utf-8",
    )
    _write(project / "apps/shop/locale/en.py", {"settings.shop.title": "Shop"})
    _write(project / "apps/shop/locale/it.py", {"settings.shop.title": "Negozio"})
    result = _locale(project, "settings")
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    for key in (
        "settings.shop.title",
        "settings.shop.view.label",
        "settings.shop.view.properties.hint",
        "settings.shop.view.options.grid",
        "settings.preferences.locale.label",  # the built-in sections too
    ):
        assert key in text, key
    only_missing = " ".join(_locale(project, "settings", "--missing").output.split())
    assert "settings.shop.view.label" in only_missing and "settings.shop.title" not in only_missing
    assert "settings.preferences.locale.label" not in only_missing  # present in every language of the core


def test_namespace_keeps_the_settings_texts_at_the_top_of_the_file(project: Path, tmp_path: Path) -> None:
    """One file per language with several roots: errors.codes.* for the codes, settings.* for the settings texts."""
    _filled(project)
    dest = tmp_path / "out"
    dest.mkdir()
    result = _locale(project, "export", "--dest-path", str(dest), "--namespace", "errors.codes")
    assert result.exit_code == 0, result.output
    english = json.loads((dest / "en.json").read_text(encoding="utf-8"))
    assert sorted(english) == ["errors", "settings"]
    assert {"auth", "billing", "shop"} <= set(english["errors"]["codes"])
    assert "settings" not in english["errors"]["codes"]
    assert english["settings"]["preferences"]["title"] == "Preferences"  # t("settings.preferences.title")
    assert english["settings"]["audit"]["disable_activity_log"]["label"].startswith("Disable activity log")

    # --top-level is repeatable; an empty value wraps everything
    _write(project / "locale/en.py", {"menu.home": "Home"})
    _write(project / "locale/it.py", {"menu.home": "Inizio"})
    extra = _locale(
        project,
        "export",
        "--dest-path",
        str(dest),
        "--namespace",
        "errors.codes",
        "--top-level",
        "settings",
        "--top-level",
        "menu",
    )
    assert extra.exit_code == 0, extra.output
    assert sorted(json.loads((dest / "it.json").read_text(encoding="utf-8"))) == ["errors", "menu", "settings"]
    wrapped = _locale(project, "export", "--dest-path", str(dest), "--namespace", "errors.codes", "--top-level", "")
    assert wrapped.exit_code == 0, wrapped.output
    assert list(json.loads((dest / "en.json").read_text(encoding="utf-8"))) == ["errors"]

    clash = _locale(project, "export", "--dest-path", str(dest), "--namespace", "settings.x")
    assert clash.exit_code == 1 and "collides" in " ".join(clash.output.split())


def test_permissions_command_lists_the_database_entities_and_their_labels(project: Path, monkeypatch) -> None:
    import asyncio

    from pachifast.cli.services import users as users_service

    monkeypatch.setenv("PACHIFAST_SECRET_KEY", "test-secret-key-for-cli-tests-0123456789")
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", f"sqlite+aiosqlite:///{project / 'db.sqlite3'}")
    for args in (
        ["migrations", "init"],
        ["migrations", "revision", "init", "--target-name", "auth", "--autogenerate"],
        ["migrations", "upgrade", "--target-name", "auth"],
    ):
        assert runner.invoke(app, [*args, "--base-dir", str(project)]).exit_code == 0

    async def seed(session):
        from pachifast.auth import cruds

        await cruds.create_permission(session, "billing.invoice_view", "See invoices")
        await cruds.create_permission(session, "billing:edit", "Edit")
        await cruds.create_group(session, "billing_editors", "Editors", [])
        await cruds.create_group(session, "Billing admins", "Admins", [])

    asyncio.run(users_service._with_session(str(project), seed))
    _write(project / "locale/en.py", {"permissions.billing.invoice_view.label": "View invoices"})
    _write(project / "locale/it.py", {"groups.billing_editors.label": "Redattori"})

    result = _locale(project, "permissions")
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "permissions.billing.invoice_view.label" in text and "groups.billing_editors.label" in text
    assert "billing:edit" in text and "Billing admins" in text  # listed, without a key
    missing = " ".join(_locale(project, "permissions", "--missing").output.split())
    assert "billing.invoice_view" in missing and "billing_editors" in missing  # each has one language only
    assert "billing:edit" not in missing and "Billing admins" not in missing  # no key: nothing to translate


def test_export_keeps_permissions_and_groups_at_the_top_level(project: Path, tmp_path: Path) -> None:
    _filled(project)
    _write(
        project / "locale/en.py", {"permissions.billing.invoice_view.label": "View", "groups.editors.label": "Editors"}
    )
    _write(
        project / "locale/it.py",
        {"permissions.billing.invoice_view.label": "Vedere", "groups.editors.label": "Redattori"},
    )
    dest = tmp_path / "out"
    dest.mkdir()
    result = _locale(project, "export", "--dest-path", str(dest), "--namespace", "errors.codes")
    assert result.exit_code == 0, result.output
    english = json.loads((dest / "en.json").read_text(encoding="utf-8"))
    assert sorted(english) == ["errors", "groups", "permissions", "settings"]
    assert (
        english["permissions"]["billing"]["invoice_view"]["label"] == "View"
    )  # t("permissions.billing.invoice_view.label")
    assert english["groups"]["editors"]["label"] == "Editors"
    assert "permissions" not in english["errors"]["codes"]


def test_check_says_what_is_out_of_date(project: Path, tmp_path: Path) -> None:
    _filled(project)
    dest = tmp_path / "out"
    dest.mkdir()
    assert _locale(project, "export", "--dest-path", str(dest)).exit_code == 0

    english = json.loads((dest / "en.json").read_text(encoding="utf-8"))
    english["billing"]["invoice_not_found"] = "Old text"  # a changed text
    del english["shop"]["cart"]["full"]  # a message the backend has and the file lacks
    english["gone"] = {"old": "x"}  # a message the backend no longer has
    (dest / "en.json").write_text(json.dumps(english), encoding="utf-8")
    (dest / "it.json").unlink()  # a missing file

    result = _locale(project, "export", "--dest-path", str(dest), "--check")
    text = " ".join(result.output.split())
    assert result.exit_code == 1
    assert "it.json" in text and "file is missing" in text
    assert "1 missing: shop.cart.full" in text and "1 not generated any more: gone.old" in text
    assert "1 different text: billing.invoice_not_found" in text
    assert (dest / "en.json").read_text(encoding="utf-8") == json.dumps(english)  # --check never writes
