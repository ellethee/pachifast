import copy
import importlib
import inspect
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal, get_args, get_origin
from zoneinfo import available_timezones

from pydantic import ConfigDict, Field, computed_field, create_model, field_validator
from pydantic.alias_generators import to_camel
from pydantic_core import to_jsonable_python

from pachifast.database.aliased_models import AliasedBaseModel
from pachifast.i18n import lookup

log = logging.getLogger(__name__)

# Properties of a component whose value is a text for the user (translatable); the others (color, icon...) are not.
TEXT_PROPERTIES = frozenset(
    {"label", "hint", "placeholder", "title", "caption", "description", "prefix", "suffix", "errorMessage", "message"}
)
_KEY_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def settings_key(*parts: str) -> str:
    """Message code of a text of the settings schema: ``settings.<section>.<field>.<part>``."""
    return ".".join(("settings", *parts))


def _text(key: str, default: str) -> str:
    """The text of ``key`` in the current language, or ``default`` (the text written in the code) when it has none."""
    return lookup(key) or default


# ---------------------------------------------------------------------------
# Helper per definire campi con metadati UI (componente frontend, label, ecc.)
# ---------------------------------------------------------------------------


_ACTION_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def ui_action(name: str, **args: Any) -> dict[str, Any]:
    """Declarative UI action: the name of an action the frontend knows plus JSON arguments, never code.

    The schema reaches every client, so it must not carry anything the client would have to execute: the frontend
    keeps a registry of named actions (``toggleDark``, ``setLocale``...) and runs the one the schema names.
    """
    if not _ACTION_NAME.match(name):
        raise ValueError(f"invalid action name {name!r}: use a plain name such as 'toggleDark', not code")
    return {"action": name, "args": args}


def _declarative(handlers: Any, what: str) -> dict[str, dict[str, Any]]:
    """Normalise ``events``/``watch`` to ``{trigger: {"action": name, "args": {...}}}``, refusing JavaScript.

    A value is an action name (``"toggleDark"``), a ``ui_action(...)`` dict or any ``{"action", "args"}`` dict.
    """
    if not isinstance(handlers, dict):
        raise ValueError(f"{what} must be a dict of trigger -> action")
    result: dict[str, dict[str, Any]] = {}
    for trigger, value in handlers.items():
        if isinstance(value, str):
            value = ui_action(value)
        elif not (isinstance(value, dict) and set(value) <= {"action", "args"} and "action" in value):
            raise ValueError(f"{what}[{trigger!r}] must be an action name or ui_action(...), got {value!r}")
        else:
            value = ui_action(value["action"], **(value.get("args") or {}))
        result[str(trigger)] = value
    return result


def ui_field(
    default: Any,
    *,
    component: str | None = None,
    label: str | None = None,
    apply: str | None = None,
    properties: dict[str, Any] | None = None,
    events: dict[str, str | dict[str, Any]] | None = None,
    watch: dict[str, str | dict[str, Any]] | None = None,
    **field_kwargs: Any,
) -> Any:
    """Field Pydantic arricchito con metadati per il rendering frontend.

    Args:
        default:    valore di default del campo
        component:  tipo di componente Quasar ('input', 'select', 'toggle', 'number', ...); se omesso lo
                    deduce il frontend dallo schema: Literal -> 'select', bool -> 'toggle', int/float -> 'number'
        label:      etichetta da mostrare nel form (opzionale)
        apply:      nome di un "applier" del frontend che riceve il valore del campo ogni volta che cambia (e al
                    login), es. "theme" o "locale"; solo un nome semplice, MAI codice (opzionale)
        properties: props del componente, es. {"options": [...], "emitValue": True}
        events:     azioni da eseguire a un evento Vue, es. {"update:modelValue": "toggleDark"} oppure
                    {"update:modelValue": ui_action("setLocale", from_value=True)}: nome di un'azione nota al
                    frontend + argomenti JSON, MAI codice (il codice JavaScript viene rifiutato con ValueError)
        watch:      azioni da eseguire quando cambia un campo, es. {"theme": "applyTheme"} (opzionale)
        **field_kwargs: eventuali argomenti aggiuntivi passati a Field()

    I metadati finiscono in json_schema_extra e vengono esposti da
    GET /user/settings/schema, ma NON vengono serializzati nei valori utente.

    Esempio:
        theme: str = ui_field(
            "light",
            component="select",
            label="Tema",
            properties={
                "options":    [{"label": "Tema chiaro", "value": "light"}, ...],
                "emitValue":  True,
                "mapOptions": True,
            },
            events={"update:modelValue": "toggleDark"},
        )
    """
    extra: dict[str, Any] = {}
    if component is not None:
        extra["x_component"] = component
    if apply is not None:
        if not _ACTION_NAME.match(apply):
            raise ValueError(f"invalid apply name {apply!r}: use a plain name such as 'theme', not code")
        extra["x_apply"] = apply
    if label is not None:
        extra["x_label"] = label
    if properties is not None:
        extra["x_properties"] = properties
    if events is not None:
        extra["x_events"] = _declarative(events, "events")
    if watch is not None:
        extra["x_watch"] = _declarative(watch, "watch")
    return Field(default, json_schema_extra=extra, **field_kwargs)


# ---------------------------------------------------------------------------
# Modelli base
# ---------------------------------------------------------------------------


class BaseUserSettings(AliasedBaseModel):
    """Classe base per i settings utente di ogni app.

    Per personalizzare ogni sezione nelle sottoclassi:
        __section_name__ = "myapp"       # nome sezione nel JSON
        _route: ClassVar[str] = "/myapp"  # route frontend dell'app
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True)
    __section_name__: str | None = None  # attributo di classe, non field pydantic
    _route: ClassVar[str | None] = None
    _title: ClassVar[str | None] = None
    _icon: ClassVar[str | None] = None

    @computed_field  # type: ignore[misc]
    @property
    def route(self) -> str | None:
        """Route frontend dell'app. Renderizzato nella response, non salvato nel DB."""
        return self.__class__._route

    @computed_field  # type: ignore[misc]
    @property
    def title(self) -> str | None:
        """Titolo dell'app. Renderizzato nella response, non salvato nel DB."""
        return self.__class__._title

    @computed_field  # type: ignore[misc]
    @property
    def icon(self) -> str | None:
        """Icona dell'app. Renderizzata nella response, non salvata nel DB."""
        return self.__class__._icon


_LOCALE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


class UserSettingsAppearance(BaseUserSettings):
    """Sezione 'appearance' sempre presente: come l'app appare a questo utente."""

    __section_name__ = "appearance"
    _title = "Appearance"
    _icon = "palette"

    theme: Literal["dark", "light", "auto"] = ui_field("auto", label="Theme", apply="theme")
    accent: Literal["teal", "indigo", "amber", "violet", "coral"] = ui_field(
        "teal", label="Accent color", apply="accent"
    )
    density: Literal["comf", "compact"] = ui_field("comf", label="Density", apply="density")
    font_scale: Literal["sm", "md", "lg"] = ui_field("md", label="Font size", apply="fontScale")
    reduce_motion: bool = ui_field(False, label="Reduce motion", apply="reduceMotion")


class UserSettingsPreferences(BaseUserSettings):
    """Sezione 'preferences' sempre presente."""

    __section_name__ = "preferences"
    _title = "Preferences"
    _icon = "tune"

    locale: str | None = ui_field(None, component="input", label="Language", apply="locale")
    timezone: str | None = ui_field(None, component="input", label="Time zone")
    date_format: Literal["auto", "dmy", "mdy", "ymd"] = ui_field("auto", label="Date format")
    table_page_size: int = ui_field(25, ge=10, le=100, label="Rows per page")

    @field_validator("locale")
    @classmethod
    def _check_locale(cls, value: str | None) -> str | None:
        """A BCP 47 language tag (``it``, ``en-GB``) or ``None`` for automatic."""
        if value is not None and not _LOCALE.match(value):
            raise ValueError("not a BCP 47 language tag")
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str | None) -> str | None:
        """An IANA time zone name (``Europe/Rome``) or ``None`` for the browser's."""
        zones = available_timezones()
        if value is not None and zones and value not in zones:
            raise ValueError("not an IANA time zone")
        return value


class UserSettingsAudit(BaseUserSettings):
    """Sezione 'audit' — per il controllo del logging (solo superuser)."""

    __section_name__ = "audit"
    _title: ClassVar[str] = "Audit & Debug"
    _icon: ClassVar[str] = "bug_report"

    disable_activity_log: bool = ui_field(
        False,
        component="toggle",
        label="Disable activity log (development/debug only)",
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _get_settings_classes(module: Any) -> list[type[BaseUserSettings]]:
    """Restituisce le classi BaseUserSettings definite nel modulo (esclusa la base)."""
    return [
        obj
        for _, obj in vars(module).items()
        if inspect.isclass(obj) and issubclass(obj, BaseUserSettings) and obj is not BaseUserSettings
    ]


def _discover_sections(root: Path | None = None) -> dict[str, type[BaseUserSettings]]:
    """Scansiona apps/* e common/* per classi BaseUserSettings."""
    rootpath = Path(root or Path.cwd())
    sections: dict[str, type[BaseUserSettings]] = {
        "appearance": UserSettingsAppearance,
        "preferences": UserSettingsPreferences,
        "audit": UserSettingsAudit,
    }
    for folder in ["apps", "common"]:
        folder_path = rootpath / folder
        if not folder_path.exists():
            continue
        for path in folder_path.rglob("user_settings.py"):
            module_path = path.relative_to(rootpath).with_suffix("").as_posix().replace("/", ".")
            try:
                mod = importlib.import_module(module_path)
                default_section = module_path.split(".")[-2]
                for cls in _get_settings_classes(mod):
                    name = getattr(cls, "__section_name__", None) or default_section
                    sections[name] = cls
                    log.info(f"✔️  {cls.__name__} → sezione '{name}'")
            except Exception as e:
                log.exception(f"❌ Errore in {module_path}: {e}")
    return sections


@lru_cache
def get_user_settings_model() -> type[AliasedBaseModel]:
    """Costruisce e casha il modello Pydantic dinamico nidificato per i settings utente."""
    sections = _discover_sections()
    fields: dict[str, tuple[type[BaseUserSettings], BaseUserSettings]] = {
        name: (cls, cls()) for name, cls in sections.items()
    }
    model = create_model("UserSettings", __base__=AliasedBaseModel, **fields)  # type: ignore[call-overload]
    log.info(f"UserSettings model costruito: sezioni={list(sections.keys())}")
    return model


# ---------------------------------------------------------------------------
# Schema UI (per GET /user/settings/schema)
# ---------------------------------------------------------------------------


def _infer_component(annotation: Any) -> str:
    """Inferisce il componente Quasar dal tipo Python del field.

    Literal[...] → 'select'
    bool        → 'toggle'
    int | float → 'number'
    str | altro → 'input'
    Optional[X] → inferisce da X
    """
    import types
    import typing

    origin = typing.get_origin(annotation)
    # Unwrap Optional[X] / Union[X, None]
    if origin is typing.Union or (hasattr(types, "UnionType") and isinstance(annotation, types.UnionType)):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if args:
            return _infer_component(args[0])

    if origin is Literal:
        return "select"
    if annotation is bool:
        return "toggle"
    if annotation in (int, float):
        return "number"
    return "input"


def _unwrap_optional(annotation: Any) -> Any:
    """``X`` for ``X | None``, otherwise the annotation itself."""
    import types
    import typing

    origin = get_origin(annotation)
    if origin is typing.Union or (hasattr(types, "UnionType") and isinstance(annotation, types.UnionType)):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _effective_properties(fi: Any, component: str, extra: dict[str, Any]) -> dict[str, Any] | None:
    """The ``properties`` of a field: the ones written with ``ui_field`` plus what the type and the limits say.

    A ``Literal`` field rendered as a ``select`` gets its ``options`` (value and a readable label, translatable by
    key) unless the field lists them; ``ge``/``le``/``gt``/``lt`` become ``min``/``max`` of a ``number``.
    """
    properties = copy.deepcopy(extra.get("x_properties")) if extra.get("x_properties") is not None else None
    annotation = _unwrap_optional(fi.annotation)
    if component == "select" and get_origin(annotation) is Literal:
        properties = properties or {}
        if "options" not in properties:
            properties["options"] = [
                {"label": str(v).replace("_", " ").capitalize(), "value": v} for v in get_args(annotation)
            ]
            properties.setdefault("emitValue", True)
            properties.setdefault("mapOptions", True)
    if component == "number":
        limits = {"min": ("ge", "gt"), "max": ("le", "lt")}
        for target, names in limits.items():
            for meta in fi.metadata:
                for name in names:
                    value = getattr(meta, name, None)
                    if value is not None:
                        properties = properties or {}
                        properties.setdefault(target, value)
    return properties


def _field_component(fi: Any, extra: dict[str, Any]) -> str:
    """The component of a field: the explicit one or the one inferred from its type."""
    return extra.get("x_component") or _infer_component(fi.annotation)


def _localize_properties(
    section: str, field: str, properties: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Translate the texts inside a component's ``properties`` and name the keys a client can translate itself.

    Returns ``(properties, property_keys, option_keys)``: ``properties`` is a copy with the texts of
    :data:`TEXT_PROPERTIES` and the ``label`` of the ``options`` replaced by the catalog text (when there is one);
    ``property_keys`` maps those property names to their key and ``option_keys`` maps an option ``value`` to the key of
    its label (only for values that are valid key parts).
    """
    result = copy.deepcopy(properties)
    property_keys: dict[str, str] = {}
    option_keys: dict[str, str] = {}
    for name, value in properties.items():
        if name in TEXT_PROPERTIES and isinstance(value, str):
            key = settings_key(section, field, "properties", name)
            property_keys[name] = key
            result[name] = _text(key, value)
    options = result.get("options")
    if isinstance(options, list):
        for option in options:
            if not (isinstance(option, dict) and isinstance(option.get("label"), str)):
                continue
            value = option.get("value")
            if isinstance(value, str) and _KEY_PART.match(value):
                key = settings_key(section, field, "options", value)
                option_keys[value] = key
                option["label"] = _text(key, option["label"])
    return result, property_keys, option_keys


def iter_text_keys(sections: dict[str, type[BaseUserSettings]]) -> list[tuple[str, str]]:
    """Every translatable text of the settings sections as ``(key, default text written in the code)``.

    Same rules as the schema: the section title, the label of each field, the texts in ``properties`` and the
    option labels. Used by ``pachifast locale settings`` to show what a catalog can translate.
    """
    rows: list[tuple[str, str]] = []
    for section_name, section_cls in sections.items():
        rows.append((settings_key(section_name, "title"), section_cls._title or section_name))
        for fname, fi in section_cls.model_fields.items():
            extra = fi.json_schema_extra if isinstance(fi.json_schema_extra, dict) else {}
            if isinstance(extra.get("x_label"), str):
                rows.append((settings_key(section_name, fname, "label"), extra["x_label"]))
            properties = _effective_properties(fi, _field_component(fi, extra), extra)
            if not isinstance(properties, dict):
                continue
            for name, value in properties.items():
                if name in TEXT_PROPERTIES and isinstance(value, str):
                    rows.append((settings_key(section_name, fname, "properties", name), value))
            for option in properties.get("options") or []:
                if isinstance(option, dict) and isinstance(option.get("label"), str):
                    value = option.get("value")
                    if isinstance(value, str) and _KEY_PART.match(value):
                        rows.append((settings_key(section_name, fname, "options", value), option["label"]))
    return rows


def build_ui_schema(model: type[AliasedBaseModel]) -> dict[str, Any]:
    """Estrae i metadati UI da ogni sezione del modello UserSettings.

    Per i field con ui_field() usa i metadati espliciti (x_component, x_label, x_apply, x_properties).
    Un campo Literal diventa un 'select' con le sue options, ge/le diventano min/max di un 'number'.
    Per i field ordinari inferisce il componente dal tipo Python:
        bool        → 'toggle'
        int | float → 'number'
        str         → 'input'

    Ritorna un dict camelCase pronto per il frontend:
        {
            "preferences": {
                "theme": {
                    "component": "select",
                    "label": "Tema",
                    "properties": {"options": [...]}
                },
                "notificationsEnabled": { "component": "toggle", "label": "Notifiche" }
            },
            "exampleApp": {
                "useCompactView": { "component": "toggle" },
                "defaultLimit":   { "component": "number" },
            }
        }
    """
    schema: dict[str, Any] = {}
    for section_name, field_info in model.model_fields.items():
        section_cls = field_info.annotation
        if not (inspect.isclass(section_cls) and issubclass(section_cls, BaseUserSettings)):
            continue
        fields: dict[str, Any] = {}
        for fname, fi in section_cls.model_fields.items():
            extra = fi.json_schema_extra if isinstance(fi.json_schema_extra, dict) else {}
            component = _field_component(fi, extra)
            entry: dict[str, Any] = {"component": component}
            if not fi.is_required():
                entry["default"] = to_jsonable_python(fi.get_default(call_default_factory=True))
            if "x_apply" in extra:
                entry["apply"] = extra["x_apply"]
            if "x_label" in extra:
                label_key = settings_key(section_name, fname, "label")
                entry["label"] = _text(label_key, extra["x_label"])
                entry["labelKey"] = label_key
            effective = _effective_properties(fi, component, extra)
            if effective is not None:
                properties, property_keys, option_keys = _localize_properties(section_name, fname, effective)
                entry["properties"] = properties
                if property_keys:
                    entry["propertyKeys"] = property_keys
                if option_keys:
                    entry["optionKeys"] = option_keys
            if "x_events" in extra:
                entry["events"] = extra["x_events"]
            if "x_watch" in extra:
                entry["watch"] = extra["x_watch"]
            fields[to_camel(fname)] = entry
        title_key = settings_key(section_name, "title")
        schema[section_name] = {
            "title": _text(title_key, section_cls._title or section_name),
            "titleKey": title_key,
            "icon": section_cls._icon,
            "route": section_cls._route,
            "fields": fields,
        }
    return schema
