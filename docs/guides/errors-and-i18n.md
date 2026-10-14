# Errors and internationalization

Every error pachifast returns has the same shape, and every error raised by the framework carries a **stable code** that clients can rely on. The human-readable message is a courtesy text in the configured language. This page explains the error format, how to raise your own coded errors, how the message catalogs work and how to hand them to a vue-i18n frontend.

- [The error format](#the-error-format)
- [Raising errors in your code](#raising-errors-in-your-code)
- [Message language](#message-language)
- [Message catalogs](#message-catalogs)
- [Adding a language](#adding-a-language)
- [The `pachifast locale` commands](#the-pachifast-locale-commands)
- [Exporting for vue-i18n](#exporting-for-vue-i18n)
- [Translating user settings](#translating-user-settings)
- [Translating permission and group names](#translating-permission-and-group-names)
- [Checking the generated files in CI](#checking-the-generated-files-in-ci)

The list of codes shipped with the framework, with their English and Italian texts, is in the [error code reference](../reference/error-codes.md).

## The error format

Every error response has a `detail` that is always a **list** of objects, never a string. This also holds for the errors that FastAPI and Starlette generate themselves (unknown path, wrong method, validation, unhandled exceptions).

```json
{
  "detail": [
    {
      "loc": ["body", "username"],
      "msg": "Invalid credentials",
      "type": "value_error",
      "code": "auth.invalid_credentials"
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `loc` | Where the problem is: `["body", "field"]`, `["query", "orderBy"]`, `["path", "user_id"]`, `["header", "Authorization"]`, or `[]` when there is no specific place. |
| `msg` | Human-readable text in the configured language. A courtesy text: do not parse it. |
| `type` | Error family: `value_error` for coded errors, `http_error` for generic HTTP errors, `server_error` for unhandled exceptions. |
| `code` | **Stable dotted identifier** (`auth.invalid_credentials`). Present on errors raised by pachifast and by code that uses `api_error`. This is the contract with clients. |
| `params` | Values of the variable parts of the message (`{"min_length": 8}`). Present only when the message has placeholders. |

Typical status codes:

| Status | When | Typical `loc` |
| --- | --- | --- |
| 400 | Business rule violated (username taken, password too short, MFA problems) | `["body", "field"]` |
| 401 | Missing, invalid or expired token; wrong credentials | `["header", "Authorization"]`, `["body", "username"]` |
| 403 | Authenticated but not allowed (staff, superuser, missing permissions) | `["header", "Authorization"]` |
| 404 | Resource, path or disabled route not found | `["path", "user_id"]` or `[]` |
| 405 | Method not allowed | `[]` |
| 422 | Request validation (body, query, `orderBy`) | `["body", "field"]`, `["query", "orderBy"]` |
| 429 | Too many MFA attempts | `["body", "code"]` |
| 500 | Unhandled error | `[]`, with `type: "server_error"` |

Notes:

- Errors without a specific location have `loc: []` and `type: "http_error"`.
- FastAPI's own validation errors (422) and errors such as 404 and 405 have no `code`, and their text is in English.
- A `500` never reveals the exception text: it answers with a generic message and the details go to the server log. With `PACHIFAST_DEBUG=true` (default `false`) Starlette shows its debug page with the traceback instead. Use it in development only.
- Errors are never wrapped in the success envelope, see [Routing and responses](routing-and-responses.md#the-response-envelope).

> **Note**: A client should branch on `code` (and `status`), and show `msg` only as a fallback. A frontend that translates from the code can fill the placeholders from `params`.

## Raising errors in your code

Use `api_error` to raise an HTTP error with a code and a localized message. The code must exist in a catalog (see [Message catalogs](#message-catalogs)).

```python
from pachifast.i18n import api_error


@router.get("/{number}")
async def get_invoice(number: str):
    invoice = await find_invoice(number)
    if invoice is None:
        raise api_error(404, "billing.invoice_not_found", loc=("path", "number"), number=number)
    return invoice
```

`api_error(status_code, code, *, loc=(), headers=None, **params)` returns an `HTTPException`. Keyword arguments other than the listed ones become the message placeholders and the `params` of the response:

```json
{
  "detail": [
    {
      "loc": ["path", "number"],
      "msg": "Invoice INV-7 not found",
      "type": "value_error",
      "code": "billing.invoice_not_found",
      "params": { "number": "INV-7" }
    }
  ]
}
```

In business code that should not know about HTTP (CRUD helpers, services), raise `MessageError`, a `ValueError` that carries a code. The route turns it into the uniform response with `detail_from_error`:

```python
from fastapi import HTTPException
from pachifast.i18n import MessageError, detail_from_error


def reserve(stock: int, wanted: int) -> None:
    if wanted > stock:
        raise MessageError("billing.not_enough_stock", available=stock)


@router.post("/reserve")
async def reserve_route(wanted: int):
    try:
        reserve(stock=3, wanted=wanted)
    except ValueError as exc:
        raise HTTPException(400, detail=detail_from_error(exc, loc=("body", "wanted"))) from None
```

A plain `ValueError` is also accepted by `detail_from_error`: it produces an item without `code`.

Other helpers in `pachifast.i18n`: `translate(code, params, locale)` (alias `t`) returns a text, `lookup(...)` does the same without falling back, `current_locale()` returns the language of the current request, and `supported_locales()` lists the languages that have a catalog.

## Message language

Two settings choose the language of `msg`:

| Setting (environment variable) | Default | Effect |
| --- | --- | --- |
| `default_locale` (`PACHIFAST_DEFAULT_LOCALE`) | `en` | Language of `msg`. A language tag such as `en`, `it` or `pt-BR`. |
| `use_accept_language` (`PACHIFAST_USE_ACCEPT_LANGUAGE`) | `false` | When `true`, each request picks the language from its `Accept-Language` header. |

With `use_accept_language` the header is parsed with its quality values and regional variants (`it-IT` is served by `it` when there is no `it-IT`). If the header contains no supported language, `default_locale` applies. If a language has no catalog, or the catalog lacks a code, the message falls back to English, and finally to the code itself.

The runtime cost is one dictionary lookup per message and, only with `use_accept_language`, one header read per request. See the [settings reference](../reference/settings.md).

## Message catalogs

A catalog is a Python file named after a language tag, inside a `locale/` folder. **Any file whose name is a language tag is a language** (`en.py`, `it.py`, `pt-BR.py`); files starting with `_` are ignored. Each file defines `MESSAGES`, a dictionary from code to template:

```python
# apps/billing/locale/en.py
MESSAGES: dict[str, str] = {
    "billing.invoice_not_found": "Invoice {number} not found",
    "billing.cart.empty": "The cart is empty",
}
```

```python
# apps/billing/locale/it.py
MESSAGES: dict[str, str] = {
    "billing.invoice_not_found": "Fattura {number} non trovata",
    "billing.cart.empty": "Il carrello è vuoto",
}
```

Rules:

- The **code** is the contract with clients. Start it with the module name (`billing.…`) to avoid collisions. Codes are dotted identifiers made of letters, digits and `_`.
- The text is a `str.format` template with **plain** fields: `{number}` is fine, `{a.b}`, `{0}` and `{a!r}` are not. Write a literal brace as `{{` or `}}`.
- Every language must define the **same codes** and use the **same fields**.
- A code cannot be the prefix of another one (`billing` together with `billing.x`), because the exported nested JSON could not represent both.

`pachifast new app billing` and `pachifast new common <name>` already create `locale/en.py` and set `locale_package="apps.billing.locale"` in the module descriptor. For an existing module, add the `locale_package` field to its `AppModule` and create the folder with an `__init__.py`. See [Modules](modules.md).

### Which text wins

Catalogs are layered. A later layer redefines the codes of the earlier ones:

1. the **core** (`pachifast.i18n.locale`, with English and Italian);
2. the `locale_package` of every module of the project, in registration order;
3. the **`locale/` folder at the project root**, which can change the default text of **any** code, including the framework's (for example `auth.invalid_credentials`), without touching the package.

Two modules defining the same code is a conflict, reported by `pachifast locale check`. Only the project `locale/` folder may redefine a code of another layer. The server reads that folder from the current directory; `pachifast dev` and `pachifast serve` already run from the project folder.

```python
# locale/en.py  (project root): reword a framework message
MESSAGES: dict[str, str] = {
    "auth.invalid_credentials": "Wrong username or password",
}
```

## Adding a language

1. In each module's `locale/` folder, copy the English file to `<tag>.py` (for example `pt-BR.py`) and translate the texts, keeping codes and `{fields}` unchanged.
2. The framework ships only English and Italian. For any other language, add a `<tag>.py` to the project root `locale/` folder with the core codes you need: the core codes are listed in the [error code reference](../reference/error-codes.md).
3. Run `pachifast locale check`. It lists exactly what is missing for the new language.
4. Set `default_locale` to the tag, or enable `use_accept_language`.

The check treats a language as incomplete if it lacks any code that another language has, so a new language must cover the core and all your modules to pass. Until it does, the API keeps working: missing messages fall back to English.

## The `pachifast locale` commands

All commands work on a generated project (they need `project_modules.py`) and take `--base-dir` to point to another folder (default: current directory). The full option list is in the [CLI reference](../reference/cli.md).

| Command | What it does | Exit code |
| --- | --- | --- |
| `locale check` | Loads core, module and project catalogs and verifies them. | `1` if any problem is found, else `0`. |
| `locale export` | Writes one nested JSON file per language for vue-i18n. | `1` on catalog problems, if the destination's parent folder is missing, or (with `--check`) if the files are missing or stale. |
| `locale settings` | Lists the translatable texts of the user settings schema. | `0` |
| `locale permissions` | Lists the permissions and groups of the auth database and their labels. | `0` |

### `locale check`

```bash
pachifast locale check
```

Prints the layers it found (source, languages, number of messages) and then reports:

- invalid codes and invalid templates;
- missing translations;
- different `{fields}` across languages;
- codes defined by two modules;
- codes that are also the prefix of other codes.

It exits with `1` when it finds problems, which makes it suitable for CI. Projects generated by pachifast already run it in the build stage of their Dockerfile.

### `locale export`

```bash
pachifast locale export --dest-path ../frontend/src/i18n/generated
```

| Option | Description |
| --- | --- |
| `--dest-path PATH` | **Required.** Folder that receives one `<language>.json` per language. Its last folder is created if missing, its parents are not. |
| `--base-dir PATH` | Project root (default `.`). |
| `--namespace NAME` | Wrap the codes under a dotted path, for example `errors.codes`. |
| `--top-level NAME` | With `--namespace`: codes whose first part is `NAME` stay at the top of the file instead of under the namespace. Repeatable. Default: `settings`, `permissions`, `groups`. An empty value wraps everything. |
| `--core` / `--no-core` | Include or exclude the framework's own messages (default: included). |
| `--check` | Write nothing; exit `1` if files are missing or out of date. |

### `locale settings`

```bash
pachifast locale settings            # every translatable settings text
pachifast locale settings --missing  # only texts some language does not translate
```

Shows, for each key `settings.<section>.<field>.…`, the default text written in the code and `yes` or `no` per language.

### `locale permissions`

```bash
pachifast locale permissions
pachifast locale permissions --missing
```

Reads the project's auth database and lists each permission and group with its translation key and whether each language has a label. Identifiers that cannot be used as a key are shown with `-`. This command needs the database configured in `.env`.

## Exporting for vue-i18n

`pachifast locale export` turns the catalogs into one JSON file per language, with the dotted codes nested (`billing.invoice_not_found` becomes `{"billing": {"invoice_not_found": "..."}}`), keys sorted and UTF-8 encoding. The format is the one vue-i18n reads: `{fields}` stay as they are, and the characters that are special in vue-i18n (`{`, `}`, `@`, `|`) in literal text are written as `{'@'}`. `$` is not special and is left alone.

Without a namespace the file mirrors the codes:

```json
{
  "billing": { "invoice_not_found": "Invoice {number} not found" },
  "auth": { "invalid_credentials": "Invalid credentials" }
}
```

With `--namespace errors.codes` the error codes are wrapped under that path, **except** the roots listed in `--top-level` (`settings`, `permissions`, `groups` by default). They stay at the top level because the API sends their keys as absolute keys (`settings.preferences.title`), so `t('settings.preferences.title')` works as is:

```bash
pachifast locale export --dest-path ../frontend/src/i18n/generated --namespace errors.codes
```

```json
{
  "errors": {
    "codes": {
      "auth": { "invalid_credentials": "Invalid credentials" },
      "billing": { "invoice_not_found": "Invoice {number} not found" }
    }
  },
  "settings": {
    "preferences": { "title": "Preferences" }
  }
}
```

Repeat `--top-level` to keep more roots at the top (`--top-level settings --top-level menu`). If the namespace equals one of the top-level roots, the export fails.

Things to know:

- The files are **generated**: they are always overwritten and never read back (except for the `--check` comparison). Do not edit them by hand.
- To let end users change a text in the frontend, put it in a separate **override file** that your i18n setup merges *after* the generated one. To change the backend text as well, use the project's `locale/` folder.
- A `.json` file in the destination that no language produces any more is reported as "Not generated any more" but never deleted.
- If the catalogs have problems, nothing is written and the command exits `1`.

### `--check`: verify without writing

`--check` compares what an export **would** write with the files already on disk and never changes anything:

| Situation | Output | Exit code |
| --- | --- | --- |
| All files match | `Exported files are up to date.` | `0` |
| A file is missing | `Out of date <file>` with `file is missing` | `1` |
| A file differs | `Out of date <file>` with the affected keys, for example `1 different text: settings.billing.default_limit.label` | `1` |
| The catalogs have problems | The problems, `Not exported` | `1` |

When it fails, regenerate with the same command without `--check` and commit the files.

## Translating user settings

The user settings schema (`GET /api/auth/user/settings/schema`) contains visible texts: section titles, field labels, texts inside `properties` and option labels. Each has a stable key derived from the section and field names:

| Text | Key |
| --- | --- |
| Section title | `settings.<section>.title` |
| Field label | `settings.<section>.<field>.label` |
| Text in `properties` (`label`, `hint`, `placeholder`, ...) | `settings.<section>.<field>.properties.<name>` |
| Option label | `settings.<section>.<field>.options.<value>` |

Put the texts in your catalogs like any other message:

```python
# apps/billing/locale/it.py
MESSAGES: dict[str, str] = {
    "settings.billing.title": "Fatturazione",
    "settings.billing.default_limit.label": "Righe per pagina",
}
```

They then reach the frontend through `locale export`. The backend also translates `title`, `label` and `properties` texts in the language of the request, keeping the text written in the code when a key is missing. How sections and fields are declared, and the schema fields `titleKey`, `labelKey`, `propertyKeys` and `optionKeys`, is described in [Authentication](authentication.md#translatable-schema-texts). Use `pachifast locale settings --missing` to find what is still untranslated.

## Translating permission and group names

Permissions and groups are database rows created at run time, so their texts cannot live in the code. What is stable is the identifier: the permission `codename` and the group `name`. When the identifier can be a message key (letters, digits and `_`; a permission codename may also contain dots), the catalogs translate it with these keys:

| Entity | Keys |
| --- | --- |
| Permission `billing.invoice_view` | `permissions.billing.invoice_view.label`, `permissions.billing.invoice_view.description` |
| Group `billing_editors` | `groups.billing_editors.label`, `groups.billing_editors.description` |

```python
# apps/billing/locale/it.py
MESSAGES: dict[str, str] = {
    "permissions.billing.invoice_view.label": "Vedere le fatture",
    "groups.billing_editors.label": "Redattori fatture",
}
```

The admin responses (permissions, groups, a user's assignments) keep their existing fields (`description` and `name` stay as stored, because an edit form sends them back) and add:

| Field | Content |
| --- | --- |
| `label` | The `.label` text in the language of the request; the identifier when there is none. |
| `localizedDescription` | The `.description` text; the stored `description` when there is none. |
| `labelKey`, `descriptionKey` | The keys, for clients that translate by themselves. Absent when the identifier is not a valid key. |

An identifier that is not a valid key (`billing:view`, or a group called "Billing admins") has no keys and is shown as is. To make a group translatable, give it an identifier as its name (`billing_editors`) and put the human text in the catalog. The roots `permissions` and `groups` stay at the top of the exported file (see `--top-level`). `pachifast locale permissions` shows which entries still lack a label.

## Checking the generated files in CI

Because `locale export --check` never writes, it is safe to run in CI: the pipeline turns red when the committed frontend files are stale, and a developer regenerates and commits them. Nothing is committed by the pipeline and no `git pull` is needed afterwards. The job needs access to both the backend (it reads its catalogs) and the frontend (the files to compare), so it normally lives in the frontend repository and checks the backend out as well:

```bash
cd backend
uv sync --frozen --no-dev
uv run pachifast locale export --dest-path ../src/i18n/generated --namespace errors.codes --check
```

For the consistency of catalogs across languages, which does not need the frontend, `pachifast locale check` is enough. A complete workflow example, with the CI job definition, is in [Frontend integration](frontend-integration.md).
