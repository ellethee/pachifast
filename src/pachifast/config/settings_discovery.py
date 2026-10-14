import importlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def discover_app_settings(
    settings: object,
    scan_dirs: tuple[str, ...] = ("apps", "common"),
    root: Path | None = None,
) -> None:
    """Import settings.py from `scan_dirs` and merge them into the global configuration."""
    rootpath = Path(root or Path.cwd())

    for folder in scan_dirs:
        for path in (rootpath / folder).rglob("settings.py"):
            module_path = path.relative_to(rootpath).with_suffix("").as_posix().replace("/", ".")
            app_name = path.parent.name
            try:
                mod = importlib.import_module(module_path)

                if hasattr(mod, "register_settings"):
                    mod.register_settings(settings)
                    log.info("✔️ register_settings eseguito da %s", module_path)
                else:
                    for key in dir(mod):
                        if key.endswith("_settings") or key == "settings":
                            value = getattr(mod, key)
                            if hasattr(value, "__dict__"):
                                setattr(settings, app_name, value)
                                log.info(
                                    "✔️ Settings da %s in %s raggruppati sotto settings.%s",
                                    key,
                                    module_path,
                                    app_name,
                                )

                    for key in dir(mod):
                        if key.isupper():
                            setattr(settings, key.lower(), getattr(mod, key))
                    log.info("ℹ️ Variabili aggiunte da %s", module_path)
            except Exception as e:
                log.exception("❌ Errore in %s: %s", module_path, e)
