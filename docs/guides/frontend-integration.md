# Frontend integration

This page is for frontend developers who consume a pachifast backend: the response contract, error handling and translation, message catalogs, authentication flows, WebSockets, user settings and the other conventions a client has to know.

> **Note**: examples use the default mount prefix `/api`. Router names and paths are those of the built-in core modules; your own modules define their own routes. Interactive OpenAPI documentation is available at `/docs` (and `/docs/core` for the core only) when enabled.

## JSON conventions

Request and response bodies of models built on pachifast's `AliasedBaseModel` use **camelCase** keys (`pageSize`, `userId`). The backend accepts both camelCase and snake_case on input and always serializes camelCase. Two deliberate exceptions use their native names: the OAuth2 token endpoints (`access_token`, `refresh_token`, `token_type`) and the one-time WebSocket ticket (`ticket`, `expires_in`).

## The response envelope

By default every successful (2xx) JSON response is wrapped:

```json
{
  "success": true,
  "message": null,
  "data": { "...": "the endpoint's own payload" }
}
```

The contract is:

- The envelope exists **only on 2xx responses** and always has `success: true`. An error is always a non-2xx response with `{"detail": [...]}` (see below), never a 2xx with `success: false`.
- `message` is optional. `database` (a list of database names used by the request) appears only when the backend enables `expose_response_database`; do not rely on it.
- Non-JSON responses (downloads, blobs, streaming) and responses without a body are **not** wrapped.
- A route declared with `skip_envelope=True` is not wrapped either. In the core these are `POST /api/auth/token`, `POST /api/auth/refresh-token` (unless the backend sets `wrap_auth_token_response=true`), `POST /api/auth/logout` and `POST /api/core/events/ticket`. These return their native payload directly.
- A paginated list is `data: { items, total, page, pageSize, pages }` inside the envelope.
- **No content.** `204` answers have no body and therefore no envelope: do not call `.json()` on them. `POST /api/auth/logout` and the built-in admin deletes (`DELETE /api/admin/user/{user_id}`, `.../permissions/permissions/{perm_id}`, `.../groups/{group_id}`) answer `204` (before 0.1.8 the admin deletes answered `200` with an enveloped `{"detail": ...}` body). Write your own delete routes with `status_code=204` and `skip_envelope=True` so the client contract is "DELETE answers 204, no body".
- **File upload.** The framework ships no generic upload route, so this is a convention for application modules: send `multipart/form-data` with the binary in the field `file` and a JSON string in the field `metadata`. The backend route must declare them (`UploadFile` plus a `Form` string parsed as JSON). Do not set the `Content-Type` header yourself so the browser adds the multipart boundary.

```ts
const form = new FormData()
form.append('file', file)
form.append('metadata', JSON.stringify({ description: 'Q3 report' }))
await api.post('/api/billing/documents', form)
```

### Turning the envelope off

An application can set `envelope_enabled=false` (`PACHIFAST_ENVELOPE_ENABLED=false`). Then **no** response is wrapped: the client receives the endpoint payload directly. This is a decision of the application, and **the client must be configured the same way** (unwrapping `data` when the envelope is on, reading the body as is when it is off). See [Routing and responses](routing-and-responses.md).

When the envelope is off, tell the client **explicitly**. Do not rely on the client tolerating bare bodies: a client that unwraps `{success, data}` by shape (a common default) would silently discard a plain payload that happens to have both keys. Configure your HTTP client to read the body as is. An "auto-detect" mode cannot remove this ambiguity.

## Errors

Every error has the same body, whatever the status (validation 422, 401, 404, 405, 500...):

```json
{
  "detail": [
    {
      "loc": ["body", "password"],
      "msg": "Password too short (minimum 12 characters)",
      "type": "http_error",
      "code": "auth.password_too_short",
      "params": { "min_length": 12 }
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `loc` | Where the error is: `["body", "field"]`, `["query", "orderBy"]`, `["path", "user_id"]`. Empty when it is not tied to an input. |
| `msg` | Human text, already localized by the server (language from `default_locale`, or `Accept-Language` when `use_accept_language` is on). |
| `type` | Error class (`http_error`, `server_error`, `value_error`, or FastAPI's validation types). |
| `code` | Stable identifier of errors raised by pachifast (`auth.invalid_credentials`). Absent on generic HTTP errors and plain validation errors. |
| `params` | Values to fill the message, present only when the message has variable parts. |

Use `loc` to attach the error to a form field and `code` + `params` to show your own translated text. Fall back to `msg` when there is no `code`. Unexpected server errors answer `500` with `Internal Server Error`; details stay in the server log.

### Translating from `code` and `params`

The catalogs exported for the frontend (next section) are in vue-i18n format, with the codes nested and the `{params}` placeholders preserved.

```ts
import { useI18n } from 'vue-i18n'

interface ApiErrorItem {
  loc: (string | number)[]
  msg: string
  type: string
  code?: string
  params?: Record<string, unknown>
}

export function useErrorText() {
  const { t, te } = useI18n()
  return (item: ApiErrorItem): string => {
    const key = item.code ? `errors.codes.${item.code}` : ''
    return key && te(key) ? t(key, item.params ?? {}) : item.msg
  }
}
```

The prefix `errors.codes` is the `--namespace` used at export time. See [Errors and i18n](errors-and-i18n.md) and the [error code reference](../reference/error-codes.md).

## Export the message catalogs

`pachifast locale export` writes one `<language>.json` per language, in the format of vue-i18n:

```bash
pachifast locale export --dest-path src/i18n/generated --namespace errors.codes
```

| Option | Description |
| --- | --- |
| `--dest-path` (required) | Folder that receives the `<language>.json` files. Its last segment is created if missing; parent folders are not. |
| `--base-dir` | Project root containing `project_modules.py` (default `.`). |
| `--namespace` | Wrap the messages under a dotted path such as `errors.codes`. |
| `--top-level` | With `--namespace`: first parts of codes that stay at the file root. Default: `settings`, `permissions`, `groups`. Repeatable; an empty value wraps everything. |
| `--core` / `--no-core` | Include or skip the pachifast core messages (default: include). |
| `--check` | Write nothing; exit with 1 if the files are missing or out of date. |

With `--namespace errors.codes` each file has several roots, for example `{"errors": {"codes": {...}}, "settings": {...}, "permissions": {...}, "groups": {...}}`. Settings texts, permission names and group names therefore stay reachable by their absolute keys (`t('settings.preferences.title')`), while error codes live under `errors.codes`. If the namespace equals one of the `--top-level` roots the export fails.

Rules to remember:

- The files are **generated**: they are always overwritten and never read back (except for `--check`). Do not edit them by hand.
- To let end users change a text, load an **override** file merged *after* the generated one in the frontend (generated < override). To change the backend text as well, use the project's `locale/` folder.
- `{fields}` are preserved; vue-i18n special characters (`{ } @ |`) in literal text are escaped as `{'@'}`.
- A `.json` in the folder that no language produces any more is only reported ("Not generated any more"), never deleted.
- If the catalogs have problems nothing is written. Run `pachifast locale check` to see them.

### Check-only job in the frontend CI

`pachifast locale export --check` **never writes or commits anything**: it compares what it would write with the generated files already in the frontend and exits with `1` when they are missing or different (listing the keys that differ), `0` when they are up to date. The pipeline therefore just turns red; a developer regenerates the files with the same command without `--check`, commits and pushes. No `git pull` is needed after the CI run.

The job needs a checkout of the backend (it reads its catalogs) and the backend's dependencies including pachifast installed. It needs **no database and no running server**. Put it in the frontend repository and check out the backend as well. Example for GitHub Actions (adapt repository names, paths and secrets):

```yaml
jobs:
  locale-files:
    runs-on: ubuntu-latest
    container: node:22-bookworm
    steps:
      - uses: actions/checkout@v4
      - uses: actions/checkout@v4              # the backend: a pachifast project
        with:
          repository: <owner>/<backend-repository>
          path: backend
          token: ${{ secrets.BACKEND_READ_TOKEN }}
      - uses: astral-sh/setup-uv@v6
      - name: Translation files are up to date
        working-directory: backend
        run: |
          uv sync --frozen --no-dev
          uv run pachifast locale export --dest-path ../src/i18n/generated --namespace errors.codes --check
```

The backend's dependencies, pachifast included, must be installable on the runner (for a private backend repository, give the checkout step a token that can read it).

For consistency between languages, which needs no frontend, the generated Dockerfile already runs `pachifast locale check` in its build stage.

### Permission and group names

Permissions and groups are database rows created at runtime, so their texts are not in the code. The stable part is the identifier (`codename` or `name`); when it is a valid message key, the catalogs translate it with `permissions.<codename>.label` / `.description` and `groups.<name>.label` / `.description`. Admin responses keep their existing fields and add `label`, `localizedDescription`, `labelKey` and `descriptionKey`. The CLI lists them with `pachifast locale permissions`.

## Authentication flows

### Login

`POST /api/auth/token` takes an OAuth2 password form (`application/x-www-form-urlencoded`, fields `username` and `password`). It answers, without an envelope:

```json
{ "access_token": "<jwt>", "token_type": "bearer", "refresh_token": "<jwt>" }
```

When MFA is enabled for the user, the response is instead `{"mfa_required": true, "mfa_token": "<token>"}` and the login must be completed with the MFA step (see [Authentication](authentication.md)). Wrong credentials answer `401` with code `auth.invalid_credentials`.

Send the access token on every request: `Authorization: Bearer <access_token>`.

### Refresh token

Access tokens are short lived (see the token settings in the [settings reference](../reference/settings.md)); the refresh token lasts `refresh_token_expire_days` (default 7).

`POST /api/auth/refresh-token` with `{"refresh_token": "<jwt>"}` returns `{"access_token": "<jwt>", "token_type": "bearer", "refresh_token": "<jwt>"}` (no envelope unless `wrap_auth_token_response` is on). The refresh token is **single use**: store the new one at every refresh, and share one in-flight refresh among concurrent requests, because a second refresh with the already spent token fails and, being a reuse, revokes the whole login. Failures answer `401` with code `auth.refresh_token_invalid`, `auth.refresh_token_revoked` or `auth.refresh_user_invalid`; a refresh token is rejected after the user changes password or is deactivated.

Recommended client behaviour, **a single retry on 401**:

1. A request answers `401`.
2. If it is not already a retry and not a call to the auth endpoints, call `refresh-token` once (share one in-flight refresh among concurrent requests).
3. On success, store the new access token and replay the original request once.
4. If the refresh fails or the replay answers `401` again, discard both tokens and send the user to the login page.

```ts
api.interceptors.response.use(undefined, async (error) => {
  const original = error.config
  if (error.response?.status !== 401 || original._retried || original.url?.includes('/auth/')) {
    throw error
  }
  original._retried = true
  const { data } = await axios.post('/api/auth/refresh-token', { refresh_token: auth.refreshToken })
  auth.accessToken = data.access_token
  auth.refreshToken = data.refresh_token // the old one is spent
  original.headers.Authorization = `Bearer ${auth.accessToken}`
  return api(original)
})
```

(On refresh failure, clear the stored tokens and redirect to login.)

### Logout

`POST /api/auth/logout` (with the access token, answer `204`, no body) revokes the refresh tokens: send `{"refresh_token": "<jwt>"}` to end that login only, or no body to end all the user's logins. It also emits the `logout` event on the WebSocket. The access token is stateless and stays valid until it expires (short), so the client must discard both tokens itself, whether or not the call succeeds.

## WebSocket events

Open the event stream with a **one-time ticket**, never with the access token in the URL (URLs end up in proxy and access logs):

1. `POST /api/core/events/ticket` with the bearer token, answer `{"ticket": "...", "expires_in": 30}` (seconds, no envelope).
2. Open `GET /api/core/events/ws?ticket=<ticket>`. The ticket works once: request a new one at every (re)connection.
3. On close code `4401` do not retry with the same token (log in again); `4403` means the superuser status changed (reconnect); `1011` is a server error (retry later).

The route exists only when `events_broadcast_enabled` is on. Events have the shape `{"type", "timestamp", "data"}`. See [Events](events.md) for event types, who receives what and the close codes.

> **Warning**: fields such as `path` and `description` of `activity` events contain user-supplied text. Always render them as text, never as HTML.

## User settings

User settings are declared on the backend; the UI is rendered from a schema.

| Request | Purpose |
| --- | --- |
| `GET /api/auth/user/settings/schema` | Sections and fields to render (title, label, component and its properties). |
| `GET /api/auth/user/settings` | Current values of the authenticated user. |
| `PUT /api/auth/user/settings/{category}/{key}` | Update one value. Body: `{"value": <string, number, boolean or null>}`; answers the updated values. |

All three require authentication and are enveloped (unless the envelope is off). Texts in the schema come with stable keys for translating on the client: `titleKey`, `labelKey`, `propertyKeys` and `optionKeys` (for example `settings.<section>.<field>.label`), exported with the other catalogs under the `settings` root.

Behaviour (events, watchers) is expressed as **declarative actions**, never code: the schema carries `{"action": "<name>", "args": {...}}` and the client keeps a **registry of named actions** (for example `toggleDark`, `setLocale`) and runs the one the schema names. Keep the registry on the client explicit and ignore unknown names.

```ts
const actions: Record<string, (args: Record<string, unknown>, value?: unknown) => void> = {
  toggleDark: (_args, value) => Dark.set(Boolean(value)),
  setLocale: (_args, value) => (i18n.global.locale.value = String(value)),
}

function run(spec?: { action: string; args?: Record<string, unknown> }, value?: unknown) {
  const fn = spec && actions[spec.action]
  if (fn) fn(spec!.args ?? {}, value)
}
```

## Pagination for tables

List endpoints built on pachifast's pagination accept these query parameters (camelCase):

| Parameter | Meaning |
| --- | --- |
| `page` | Page number, from 1. |
| `pageSize` | Items per page (0 or more). |
| `orderBy` | Comma separated fields; prefix a field with `-` for descending (`-createdAt,name`). camelCase and snake_case names are accepted. An unknown field answers `422` with `loc: ["query", "orderBy"]`. |
| `search` | Text filter on the fields the endpoint declares searchable. |

Pagination applies only when **both** `page` and `pageSize` are set; otherwise the full list is returned (the lists of pachifast itself, the admin ones, always answer page 1 of 25 and at most 200 rows). The result is `{ items, total, page, pageSize, pages }`, so `total` drives the table's pager (`pages` is the number of pages: `ceil(total / pageSize)`, `1` without paging, `0` when empty). Endpoints may add their own filters; list filters accept repeated parameters (`?status=1&status=2`) or comma separated values (`?status=1,2`). With Quasar's `QTable` server-side mode map `pagination.page`, the rows-per-page value and `sortBy`/`descending` to `page`, `pageSize` and `orderBy`.

## CORS

CORS is **not** enabled by default. `create_app(modules, cors_origins=[...])` installs the middleware with those origins, all headers and all methods. The generated `main.py` lists `http://localhost` and `http://127.0.0.1:8000` as a starting point: add the origin of your frontend (scheme, host and port) there. A development server proxy (for example Quasar's `devServer.proxy` to the backend) avoids CORS altogether. The middleware exposes the `Retry-After` header, so a client on another origin can read the countdown of a `429` for a locked account (a same-origin proxy does not need it). There are no CORS settings in `pachifast.yaml` or in environment variables: the list is set in code.

## JSON Schema for `pachifast.yaml`

`pachifast config schema` prints the JSON Schema of the configuration file (`-o file` writes it). New projects already contain `pachifast.schema.json` and a first line in `pachifast.yaml` that points the editor to it:

```yaml
# yaml-language-server: $schema=./pachifast.schema.json
```

With an editor that supports `yaml-language-server` (VS Code with the YAML extension, JetBrains IDEs) you get completion and validation of setting names and values. Regenerate the file after upgrading pachifast:

```bash
pachifast config schema --output pachifast.schema.json
```

## Related pages

- [Routing and responses](routing-and-responses.md)
- [Authentication](authentication.md)
- [Errors and i18n](errors-and-i18n.md)
- [Events](events.md)
- [Deployment](deployment.md)
