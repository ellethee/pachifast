"""Locale service: loads the catalogs of a project and checks/exports them."""

from pathlib import Path

from pachifast.cli.helpers.project import project_context
from pachifast.cli.services import users as users_service
from pachifast.i18n import exporter, loader
from pachifast.i18n.loader import LocaleLayer, Problem


def load_project_layers(base_dir: str, *, include_core: bool = True) -> list[LocaleLayer]:
    """The catalog layers of the project in ``base_dir`` (core, modules with a ``locale_package``, ``locale/``).

    Raises:
        FileNotFoundError: If ``base_dir`` is not a generated project.
        pachifast.i18n.loader.LocaleError: If a locale package or file cannot be loaded.
    """
    from pachifast.migrations.runtime import _load_project_modules

    root = Path(base_dir).resolve()
    if not (root / "project_modules.py").exists():
        raise FileNotFoundError(
            f"project_modules.py not found in {root}. Run the command from a generated project "
            "(see `pachifast new project <name>`), or use --base-dir."
        )
    with project_context(str(root)):
        modules = _load_project_modules(root)
        layers = loader.collect_layers(modules, root)
    return layers if include_core else [layer for layer in layers if layer.kind != "core"]


def settings_texts(base_dir: str) -> list[tuple[str, str, dict[str, bool]]]:
    """The translatable texts of the project's user settings: ``(key, default text, {language: has an entry})``."""
    from pachifast.auth.user_settings_discover import _discover_sections, iter_text_keys
    from pachifast.migrations.runtime import _project_sys_path

    layers = load_project_layers(base_dir)
    catalogs = loader.merge_layers(layers)
    root = Path(base_dir).resolve()
    with project_context(str(root)), _project_sys_path(root):
        rows = iter_text_keys(_discover_sections(root))
    return [
        (key, default, {lang: key in messages for lang, messages in sorted(catalogs.items())}) for key, default in rows
    ]


def admin_texts(base_dir: str) -> list[tuple[str, str, dict[str, bool]]]:
    """The translatable texts of the project's model admin: ``(key, default text, {language: has an entry})``."""
    from pachifast.admin.registry import discover_admin_modules
    from pachifast.admin.schema import iter_text_keys
    from pachifast.migrations.runtime import _project_sys_path

    layers = load_project_layers(base_dir)
    catalogs = loader.merge_layers(layers)
    root = Path(base_dir).resolve()
    with project_context(str(root)), _project_sys_path(root):
        discover_admin_modules(root)
        rows = iter_text_keys()
    return [
        (key, default, {lang: key in messages for lang, messages in sorted(catalogs.items())}) for key, default in rows
    ]


@users_service.in_project
def auth_entity_texts(base_dir: str) -> list[tuple[str, str, str | None, dict[str, bool]]]:
    """Permissions and groups of the project's auth database with their message keys.

    Returns ``(kind, identifier, key or None, {language: has a label})`` for each permission (identifier: codename)
    and group (identifier: name). The key is ``None`` when the identifier cannot be part of a message code.
    """
    import asyncio

    from pachifast.auth import cruds
    from pachifast.auth.labels import group_key, permission_key

    catalogs = loader.merge_layers(load_project_layers(base_dir))

    async def action(session):  # type: ignore[no-untyped-def]
        return await cruds.list_permissions(session), await cruds.list_groups(session)

    permissions, groups = asyncio.run(users_service._with_session(base_dir, action))
    rows: list[tuple[str, str, str | None, dict[str, bool]]] = []
    entities = [("permission", p.codename, permission_key(p.codename)) for p in permissions]
    entities += [("group", g["name"], group_key(g["name"])) for g in groups]
    for kind, identifier, base in entities:
        present = {lang: bool(base) and f"{base}.label" in messages for lang, messages in sorted(catalogs.items())}
        rows.append((kind, identifier, base, present))
    return rows


def check_project(base_dir: str) -> tuple[list[LocaleLayer], list[Problem]]:
    """Load the project's catalogs and check them."""
    layers = load_project_layers(base_dir)
    return layers, loader.check_layers(layers)


def export_project(
    base_dir: str,
    destination: str,
    *,
    namespace: str = "",
    top_level: tuple[str, ...] = exporter.DEFAULT_TOP_LEVEL,
    include_core: bool = True,
    check: bool = False,
) -> tuple[exporter.ExportReport, list[Problem]]:
    """Export the project's catalogs as one JSON file per language into ``destination``.

    Nothing is written when the catalogs have problems (they are returned instead). With ``check`` nothing is
    written either: the report says which files are out of date.
    """
    layers = load_project_layers(base_dir)
    problems = loader.check_layers(layers)
    if problems:
        return exporter.ExportReport(), problems
    exported = layers if include_core else [layer for layer in layers if layer.kind != "core"]
    files = exporter.render_all(loader.merge_layers(exported), namespace, top_level)
    return exporter.write_files(Path(destination), files, check=check), []
