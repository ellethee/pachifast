# Model admin

The model admin turns a SQLAlchemy model into a set of REST routes plus a **schema** that a generic frontend uses to draw the table and the form, without writing code per model. It is optional and covers the common case: list, search, filter, sort, create, edit and delete rows, each action behind its own permission.

It does not render HTML. The frontend (for example the Quasar pages of your project) reads the schema and builds the screens.

## Enable it

Add `AdminModule` to the project's modules:

```python
# project_modules.py
from pachifast.admin import AdminModule
from pachifast.presets import core_module

MODULES = (core_module, AdminModule, billing_module)
```

At startup the module imports every `apps/**/admin.py` and `common/**/admin.py` and creates the missing permissions (see below). The models must live in the **authentication database** (the one behind `AUTH_DATABASE_URL`); the model admin is asynchronous only.

## Register a model

```python
# apps/billing/admin.py
from pachifast.admin import ModelAdmin, register_admin

from .models import Invoice


@register_admin(Invoice)
class InvoiceAdmin(ModelAdmin):
    list_display = ["number", "customer_id", "status", "total"]
    search_fields = ["number"]
    list_filter = ["status", "customer_id"]
    ordering = ["-id"]
    readonly_fields = ["created_at"]
    exclude = ["internal_notes"]
    fields_meta = {"status": {"label": "State"}}
```

Every attribute is optional. With none of them the admin lists every column, searches nothing, filters nothing and orders by the primary key, newest first.

| Attribute | Meaning |
| --- | --- |
| `app` | Prefix of the routes and of the permissions. Default: the package that holds `admin.py` (`apps.billing.admin` gives `billing`). |
| `name` | Name of the model in routes and permissions. Default: the table name. |
| `title` | Human title. Default: the class name spaced out. |
| `list_display` | Columns of the table, in order. |
| `search_fields` | Columns the `search` text looks into (case insensitive, substring). |
| `list_filter` | Columns that can be filtered by exact value (`?status=paid`). Others are ignored. |
| `ordering` | Default order (`"-id"` style). |
| `order_fields` | Columns a client may sort by. Default: `list_display` plus the primary key. |
| `readonly_fields` | Columns shown but never written. The primary key is always read-only. |
| `exclude` | Columns that never leave the server: not listed, not read, not written. Binary columns are always excluded. |
| `fields_meta` | Per column overrides of the schema, for example `{"status": {"label": "State"}}`. |

`app` and `name` must be lowercase identifiers (they appear in URLs and permission codenames) and two models cannot use the same pair. The model needs a single primary key column; unknown column names are refused when the class is defined.

## Routes

All routes are under `/api/admin/models` and answer inside the standard envelope.

| Route | Does | Permission |
| --- | --- | --- |
| `GET /` | The models the user can list (the menu of the admin), with the user's permissions on each. | `view` |
| `GET /{app}/{model}/_schema` | The schema of the model. | `view` |
| `GET /{app}/{model}/_options` | Rows as `{value, label}` pairs for a select that points at the model: `search`, `ids=1,2`, `page`, `pageSize`; answers `{items, total, page, pageSize, pages}`. The label is `label_field` or the first text column of the table. | `view` |
| `GET /{app}/{model}` | Paginated rows: `page`, `pageSize`, `orderBy`, `search` and the `list_filter` columns. | `view` |
| `GET /{app}/{model}/{pk}` | One row. | `view` |
| `POST /{app}/{model}` | Create a row. Answers `201` with the row. | `add` |
| `PATCH /{app}/{model}/{pk}` | Update only the columns sent. | `change` |
| `DELETE /{app}/{model}/{pk}` | Delete a row. Answers `204`. | `delete` |
| `GET /{app}/{model}/{pk}/history` | The changes made to the row through the admin, newest first, paginated (works for a deleted row too). | `view` |
| `POST /{app}/{model}/_actions/{name}` | Run a bulk action on `{"ids": [...]}`: `{affected, message}`. | `delete` for `delete_selected`, `change` or the one the action declares |

Details worth knowing:

- `orderBy` takes one or more columns separated by commas, `-` for descending: `orderBy=-status,number` (camelCase or snake_case names, only `order_fields`).
- Filters of `list_filter` match exactly (`?status=paid`; a foreign key takes the primary key of the other row). Columns of a numeric, date, datetime or time type can also be filtered by a range with `<column>__gte` and `<column>__lte` (inclusive; `?dueDate__gte=2026-01-01T00:00:00Z`); the schema marks these filters with `"range": true`.
- Datetimes are ISO 8601. Send an offset or `Z` (`2026-03-01T10:00:00+02:00`): for a column without time zone the value is converted to UTC and stored naive, so what is read back is UTC. A datetime without offset is taken as it is.
- A row has no title in the data; to show "delete *invoice 12*?" ask `_options?ids=12,13` (it answers the labels of those rows).
- Without `page` and `pageSize` the list answers page 1 of 25; `pageSize` is capped at 200. The admin never returns an unbounded list.
- The body of `POST` and `PATCH` is validated against the model's types, `Literal` options, `String(n)` lengths and nullability. Errors are the usual `422` with `loc: ["body", "<field>"]` (and `"query"` for parameters); the field is always the camelCase name of the schema, even if the client sent the snake_case one. Keys are camelCase (snake_case is accepted too). Keys of read-only, excluded or unknown columns are **ignored**, never written.
- `delete_selected` answers a localized `message` (`3 deleted`).
- A write refused by the database (duplicate, foreign key, not null) answers `409 admin.integrity_error`. Unknown models and rows answer `404 admin.model_not_found` and `admin.object_not_found`.
- Writes are recorded by the activity log when it is enabled (`{username} changed shop.invoice #3`).

## History

Every write made through the admin (create, update, delete and bulk actions) adds a row to the history table, **in the same transaction** as the change: either both are saved or neither. A row records who (`userId`, and the `username` copied so it survives the account), when, the `action` (`add`, `change`, `delete`, `action:<name>`) and `changes` (always an object, empty when nothing differs), a map from each changed column to `{"old": ..., "new": ...}` (an update that changes nothing records nothing). Excluded columns never appear.

```json
{"id": 12, "timestamp": "2026-10-07T10:30:00+00:00", "userId": "...", "username": "boss",
 "action": "change", "changes": {"total": {"old": 5.0, "new": 7.0}, "status": {"old": "open", "new": "paid"}}}
```

The history only sees changes made **through the admin**; writes from your own routes or scripts are not recorded (the [activity log](events.md) records HTTP requests in general). The table is `pachifast_admin_change` (name follows `table_prefix`). `AdminModule` creates it at startup if it does not exist, and declares the migration target `admin`, so a project that manages its schema with migrations can generate a revision with `pachifast migrations revision --target-name admin` before the first start in production.

## Bulk actions

`delete_selected` exists for every model. To add your own, mark a method with `@admin_action` and list its name in `actions`:

```python
from pachifast.admin import ModelAdmin, admin_action, register_admin


@register_admin(Invoice)
class InvoiceAdmin(ModelAdmin):
    actions = ["mark_paid"]

    @admin_action(label="Mark as paid")  # permission="change" and confirm=True by default
    async def mark_paid(self, session, rows) -> str:
        for row in rows:
            row.status = "paid"
        return f"{len(rows)} paid"
```

The method receives the selected ORM rows, changes them and may return a text for the user (`message`). It **must not commit**: the admin commits once, with the history rows. The action runs on 1 to 500 ids, all or nothing: an id that does not exist answers `404` and nothing changes. An action needs the model permission it declares (`permission="delete"`, for example); a name that is not in `actions` answers `404 admin.action_not_found`. Declaring a name that is not marked with `@admin_action` is refused when the class is defined.

## Permissions

Each model has four permissions: `<app>.<model>.view`, `.add`, `.change` and `.delete`. A superuser has them all; other users get them through groups or directly, as usual.

At startup `AdminModule` **creates the missing ones** in the permissions table so they can be assigned from the groups page. It only adds: existing permissions are left alone and none is removed. If the authentication tables do not exist yet (migrations not applied) it logs a warning and the next start creates them.

## The schema

`GET /{app}/{model}/_schema` describes the model for the user who asks:

```json
{
  "app": "billing", "model": "invoice", "title": "Invoice", "titleKey": "admin.billing.invoice.title",
  "primaryKey": "id", "primaryKeyType": "int",
  "permissions": {"view": true, "add": true, "change": true, "delete": false},
  "list": {
    "display": ["number", "status", "total"], "search": true, "ordering": ["-id"],
    "orderFields": ["number", "status", "total", "id"],
    "filters": [{"field": "status", "component": "select", "label": "State", "properties": {"options": [...]}}]
  },
  "fields": {
    "number": {"component": "input", "label": "Number", "labelKey": "admin.billing.invoice.number.label",
               "type": "string", "default": null, "required": true, "readonly": false, "nullable": false,
               "primaryKey": false, "maxlength": 20},
    "status": {"component": "select", "required": false, "properties": {"options": [{"label": "Open", "value": "open"}], "emitValue": true},
               "optionKeys": {"open": "admin.billing.invoice.status.options.open"}}
  }
}
```

The component comes from the Python type of the column:

| Type | Component |
| --- | --- |
| `Literal[...]`, `enum.Enum` | `select` with `options` (and `optionKeys`) |
| `bool` | `toggle` |
| `int`, `float`, `Decimal` | `number` |
| `str` | `input` (with `maxlength` for `String(n)`), `textarea` for text without a length |
| `datetime`, `date`, `time` | `datetime`, `date`, `time` |
| `dict`, `list` (JSON) | `json` |
| foreign key to a registered model | `lookup`, with `"lookup": {"app", "model"}`: fill it from that model's `_options` |

`type` is the JSON data type (`string`, `integer`, `number`, `boolean`, `date`, `datetime`, `time` or `json`), `default` the plain default a new row gets (`null` for generated or computed ones), and A foreign key column whose table is administered by another registered model gets `component: "lookup"` and `lookup: {"app": ..., "model": ...}` (also in `list.filters`); a client reads the choices from `GET /{app}/{model}/_options` of that model and resolves a stored value with `?ids=`. A foreign key to a table nobody administers stays a plain number. `required` is true when a row cannot be created without the value (not nullable, no default). Number fields carry `properties.step` (`1` for integers, `"any"` otherwise); `min`, `max` and other component props can be added per column with `fields_meta = {"price": {"properties": {"min": 0}}}`. `primaryKeyType` is `int`, `uuid` or `str`, to build the URLs. `list.display` names columns that are always in `fields`: take the label and type from there.

Data in responses is JSON: dates and times are ISO 8601 strings, UUIDs and decimals (`Decimal`) are strings, floats are numbers. Binary columns are not handled: they are always left out. `actions` lists the bulk actions (`name`, `label`, `labelKey`, `permission`, `confirm`, and `allowed` for the asking user) and `history: true` tells the client that the history route exists. Labels and option texts follow the same translation rule as the [user settings](authentication.md#translatable-schema-texts): the keys `admin.<app>.<model>.title`, `admin.<app>.<model>.<column>.label`, `admin.<app>.<model>.<column>.options.<value>` and `admin.<app>.<model>.actions.<name>.label` can be added to the catalogs of the project, and the schema falls back to a readable text made from the names.

## Security notes

- Every route checks a permission; a model that is not registered has no route.
- The columns that may be written are exactly the model's columns minus `readonly_fields`, `exclude` and the primary key: a body can never reach other attributes. A model with a sensitive column (a role, a flag such as `is_superuser`) must list it in `readonly_fields` or `exclude`, otherwise whoever has the `add` or `change` permission can set it.
- Sorting is limited to `order_fields`, searching to `search_fields`, filtering to `list_filter`.

`pachifast locale admin` lists every text of the registered models with its default and the languages that translate it (`--missing` shows what is untranslated), like `pachifast locale settings` does for the user settings.

## Not included (yet)

Inline related rows (child rows inside the parent's form), CSV export and live updates of open lists are not part of this version.
