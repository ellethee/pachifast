# Authentication and permissions

pachifast ships a complete user system: password login with JWT access and refresh tokens, brute-force protection, role-based access control (users, groups, permissions), optional multi-factor authentication and declarative per-user settings. The routes live in the built-in `core` module, so every project generated with `pachifast new project` has them.

This page covers:

- [How authentication works](#how-authentication-works)
- [Token endpoints](#token-endpoints)
- [Passwords, lockout and token revocation](#passwords-lockout-and-token-revocation)
- [Managing users](#managing-users)
- [Permissions and groups](#permissions-and-groups)
- [Multi-factor authentication](#multi-factor-authentication-mfa)
- [User settings](#user-settings)
- [The `pachifast users` CLI](#the-pachifast-users-cli)

All paths below are relative to the `/api` mount prefix, so `/auth/token` is served at `/api/auth/token`. Successful JSON responses are wrapped in the standard envelope unless stated otherwise (see [Routing and responses](routing-and-responses.md#the-response-envelope)); errors always use the uniform format described in [Errors and i18n](errors-and-i18n.md).

## How authentication works

| Piece | Implementation |
| --- | --- |
| Password hashing | Argon2id (`pwdlib`, PHC format, OWASP parameters m=19 MiB, t=2, p=1). A hash with outdated parameters is upgraded transparently on the next successful login. |
| Tokens | JWT signed with `PACHIFAST_SECRET_KEY` (algorithm `ALGORITHM`, default `HS256`). Claims: `sub` (user UUID), `type` (`access` or `refresh`), `iat`, `exp`; a refresh token also carries `jti` (its identity) and `fam` (the login it belongs to). |
| Identity | Users have a UUID `id`. Usernames are trimmed, lower-cased and must be 3 to 150 characters long. |
| Authorization | Permissions are atomic codenames. Groups are sets of permissions. A user has any number of groups plus optional direct permissions. |

> **Warning**: `PACHIFAST_SECRET_KEY` is mandatory and has no default; the application refuses to start without it. Keep it in `.env` or the environment, never in the YAML file or in version control. Changing it invalidates every issued token.

Token lifetimes are configurable (full list in the [settings reference](../reference/settings.md)):

| Setting (environment variable) | Default | Meaning |
| --- | --- | --- |
| `ACCESS_TOKEN_EXPIRE_MINUTES` (`PACHIFAST_ACCESS_TOKEN_EXPIRE_MINUTES`) | `30` | Lifetime of an access token, in minutes. |
| `REFRESH_TOKEN_EXPIRE_DAYS` (`PACHIFAST_REFRESH_TOKEN_EXPIRE_DAYS`) | `7` | Lifetime of a refresh token, in days. |
| `ALGORITHM` | `HS256` | JWT signing algorithm. |

By default every router created with `get_router()` requires an authenticated, **active** user: a valid access token in the `Authorization: Bearer <token>` header. See [Routing and responses](routing-and-responses.md) for how to opt out per router (`deps=["noauth"]`) or per route (`skip_auth=True`).

## Token endpoints

### Log in: `POST /auth/token`

The login endpoint follows the OAuth2 password flow: send an `application/x-www-form-urlencoded` body with `username` and `password`.

```bash
curl -X POST http://127.0.0.1:8000/api/auth/token \
  -d "username=admin" -d "password=<password>"
```

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "refresh_token": "<jwt>"
}
```

The response is **not** wrapped in the envelope, so that standard OAuth2 clients (including the Swagger UI "Authorize" button) work unchanged. Set `PACHIFAST_WRAP_AUTH_TOKEN_RESPONSE=true` if you want `/auth/token` and `/auth/refresh-token` to use the envelope like every other route.

A failed login always returns the same `401` with code `auth.invalid_credentials` and `loc: ["body", "username"]`, whether the user does not exist, the password is wrong, the account is inactive or the account is temporarily locked. The error never reveals which case occurred, and the response time is kept uniform for unknown usernames.

When the user has MFA enabled the response is different, see [Multi-factor authentication](#multi-factor-authentication-mfa).

### Refresh: `POST /auth/refresh-token`

Exchange a valid refresh token for a new access token **and a new refresh token**. The body is JSON:

```bash
curl -X POST http://127.0.0.1:8000/api/auth/refresh-token \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "<jwt>"}'
```

```json
{ "access_token": "<jwt>", "token_type": "bearer", "refresh_token": "<jwt>" }
```

Refresh tokens are **single use** (`refresh_token_rotation`, on by default). Every refresh spends the presented token and returns the next one of the same login, so the client must store the new refresh token each time and should not refresh twice in parallel with the same token (the second call fails with `401`). Presenting a token that was already spent more than `refresh_reuse_grace_seconds` (default 10) after its rotation means it leaked: the **whole login is revoked** and nobody holding a token of that login can refresh any more. Inside the grace window (two tabs refreshing together) the second call is only refused, so the login survives; set it to `0` to revoke at the first reuse. The tokens are recorded in the `refresh_token` table (a record holds only the identity of the token, never a secret), which is what makes logout effective.

With `refresh_token_rotation=false` the response has no `refresh_token`, the presented token stays valid until it expires and it can still be revoked.

A refresh token that has no record is refused. The endpoint answers `401` with one of these codes:

| Code | Cause |
| --- | --- |
| `auth.refresh_token_invalid` | Malformed, expired, wrong type or bad signature. |
| `auth.refresh_user_invalid` | The user no longer exists or is inactive. |
| `auth.refresh_token_revoked` | Logged out, password changed, user deactivated, token already used, or not issued by this server. |

### Log out: `POST /auth/logout`

Requires a valid access token and answers `204 No Content`. With a body `{"refresh_token": "<jwt>"}` it revokes the login of that token; **without a body it revokes every login of the user**. A token that is not the caller's own is ignored (and the call falls back to ending all the caller's logins). The access token is stateless and cannot be revoked: it stays valid until it expires (`ACCESS_TOKEN_EXPIRE_MINUTES`, short), so the client must still discard both tokens. The endpoint also **notifies** the exit: it emits the `logout` WebSocket event (same payload as `login`). See [Events](events.md) for who receives it and how to make `login` and `logout` public.

### Register: `POST /auth/register`

 public endpoint that creates an account and returns it with status `201`. The server ignores any privilege in the request: new accounts are always active, non-staff and non-superuser.

```bash
curl -X POST http://127.0.0.1:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "mario", "password": "<password>", "email": "mario@example.org"}'
```

Request fields use camelCase (`firstName`, `lastName`, `email`). Errors: `400 auth.username_taken`, `400 user.username_or_email_exists`, `422 auth.password_too_short`.

Self-registration is **closed by default**: the route answers `403 auth.registration_disabled` until you set `PACHIFAST_REGISTRATION_ENABLED=true`. Without it, users are created with `pachifast users create` or the admin API. When you open it, remember that the route tells whether a username exists (`auth.username_taken`), as any open registration does: put it behind your own gate on a public deployment.

> **Note**: To remove the route altogether (not even a `403`), use `PACHIFAST_DISABLE_CORE_ROUTES=POST /auth/register`. See [Modules](modules.md) for the router and route switches.

### Current user: `GET /auth/user`

Returns the authenticated user, including the list of effective permission codenames (`permissions`) used by the frontend to show or hide features.

```json
{
  "success": true,
  "message": null,
  "data": {
    "id": "0b5c...",
    "username": "mario",
    "email": "mario@example.org",
    "firstName": "Mario",
    "lastName": "Rossi",
    "isActive": true,
    "isStaff": false,
    "isSuperuser": false,
    "emailVerifiedAt": null,
    "lastLoginAt": "2026-10-05T08:30:00Z",
    "createdAt": "2026-09-01T10:00:00Z",
    "permissions": ["billing.invoice_view"]
  }
}
```

### Edit your own profile: `PATCH /auth/user`

A user changes their own `firstName`, `lastName` and `email`; only the fields that are sent change and an empty `email` removes it. Privileges, the username and the password cannot be changed here (the password has its own route below).

```bash
curl -X PATCH http://127.0.0.1:8000/api/auth/user \
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" \
  -d '{"firstName": "Mario", "email": "mario@example.org", "currentPassword": "<password>"}'
```

Changing the email asks for `currentPassword` (`400 auth.password_incorrect`; the same counted check and lockout as the other password prompts), because the email receives the codes of the email MFA method and a stolen access token must not be able to move it. An email that another account has is `400 user.email_exists`. The answer is the updated user, like `GET /auth/user`.

### How a request is authenticated

For every protected HTTP request pachifast decodes the access token and then loads the user from the database. Failures map to these responses:

| Situation | Status | Code |
| --- | --- | --- |
| No `Authorization` header | 401 | `auth.not_authenticated` |
| Invalid or expired token | 401 | `auth.token_invalid` |
| A refresh token used as access token (or vice versa) | 401 | `auth.token_wrong_type` |
| Token without `sub` | 401 | `auth.token_no_subject` |
| User deleted | 401 | `user.not_found` |
| Token issued before the last password change | 401 | `auth.token_revoked` |
| User exists but is inactive | 400 | `user.disabled` |

Every 401 carries a `WWW-Authenticate: Bearer` header.

## Passwords, lockout and token revocation

### Password policy

The policy is a minimum length, set by `password_min_length` (default `12`, as OWASP ASVS asks). It is enforced on registration, on admin password changes and when creating users from the CLI. A shorter password is rejected with `400` and code `auth.password_too_short`; the response carries `params.min_length` so a client can render its own text:

```json
{
  "detail": [
    {
      "loc": ["body", "username"],
      "msg": "Password too short (minimum 8 characters).",
      "type": "value_error",
      "code": "auth.password_too_short",
      "params": { "min_length": 12 }
    }
  ]
}
```

### Login lockout

Repeated failed logins lock the account temporarily:

| Setting | Default | Meaning |
| --- | --- | --- |
| `login_max_attempts` | `5` | Failed attempts before the account is locked. |
| `login_lockout_minutes` | `15` | How long the account stays locked. |

Each attempt is **counted before the password is checked**, in one atomic database update, so a burst of parallel requests cannot try more passwords than the limit allows. When the limit is reached the account is locked for `login_lockout_minutes` and the counter resets. While locked, **even the correct password** is answered with the uniform `401 auth.invalid_credentials`, so an attacker learns nothing. A successful login resets the counter; an administrator who changes the user's password (through the API) also removes the lock. A lock is logged at warning level on the server.

The same counter protects every place that asks for the current password, so a stolen access token cannot be used to guess it: `POST /auth/user/password`, `POST /mfa/disable` and `POST /mfa/backup-codes/regenerate`. While the account is locked these three routes answer `429` with `Retry-After` (code `auth.too_many_attempts`, or `mfa.too_many_attempts` for the MFA ones): the caller is already authenticated, so nothing is revealed. Wrong codes of the second factor count too (see [Multi-factor authentication](#multi-factor-authentication-mfa)). A locked account answers as slowly as an unknown one, so the timing does not tell them apart.

### Changing your own password

`POST /auth/user/password` with `{"currentPassword": "...", "newPassword": "..."}` (camelCase) answers `204` without a body. A wrong current password is `400 auth.password_incorrect`; a new password that breaks the policy is `422 auth.password_too_short`, and so is the same password as the current one (`422 auth.password_unchanged`). Registration and the admin routes answer `422` too for a password that breaks the policy. The change revokes the tokens issued before it (see below): the client must log in again.

### Token revocation on password change

When a user's password changes, `password_changed_at` is updated and every token issued **before** that moment stops working:

- access tokens are rejected with `401 auth.token_revoked`;
- refresh tokens are deleted, so they are rejected with `401 auth.refresh_token_revoked` (deactivating the user does the same);
- open event WebSockets of that user are closed with code `4401`.

The comparison uses the token `iat` claim, which has one-second resolution: a token issued in the very same second as the change stays valid. Deactivating or deleting a user has the same practical effect, since the user is re-checked on every request.

> **Note**: `pachifast users update --password` behaves like `PATCH /admin/user/{id}`: it updates `password_changed_at` (so tokens issued before stop working) and lifts a login lockout.

## Managing users

The `/admin/user` router is restricted to superusers (`require_superuser=True`).

| Method and path | Description |
| --- | --- |
| `GET /admin/user` | Paginated list of users. Newest first by default (`orderBy=-created_at`). `search` matches username, email, first name and last name. |
| `GET /admin/user/{user_id}` | A single user. `404 user.not_found` if missing. |
| `PATCH /admin/user/{user_id}` | Update `email`, `firstName`, `lastName`, `password`, `isActive`, `isStaff`, `isSuperuser`. Only the fields you send change. An empty email clears it. |
| `DELETE /admin/user/{user_id}` | Delete the user together with groups, direct permissions, settings and MFA data. Answers `204`. |

The list uses the standard pagination contract (`page`, `pageSize`, `orderBy`, `search`; response `{items, total, page, pageSize, pages}`), described in [Routing and responses](routing-and-responses.md#pagination).

```bash
curl -X PATCH http://127.0.0.1:8000/api/admin/user/<user-id> \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"isStaff": true}'
```

Safety rules:

- A superuser cannot delete their own account (`400 user.cannot_delete_self`).
- A superuser cannot deactivate themselves or remove their own superuser flag (`400 user.cannot_demote_self`).
- Changing the email to one already in use returns `400 user.email_exists`.

### Effects on open WebSockets

Changing a user through the API acts immediately on the event WebSocket connections of that user (see [Events](events.md)):

| Change | WebSocket close code |
| --- | --- |
| User deleted, deactivated, or password changed | `4401` (the client must log in again) |
| Superuser status gained or lost | `4403` (the client must reconnect with the same token to receive the events of its new role) |

Changes made outside the API, for example with the `pachifast users` command, are picked up by a periodic check (every 30 seconds).

## Permissions and groups

The access-control model is deliberately small:

- a **permission** is a row with a unique `codename` (for example `billing.invoice_view`);
- a **group** is a named set of permissions;
- a **user** has any number of groups and any number of direct permissions;
- the user's **effective permissions** are the union of the permissions of all their groups and their direct permissions;
- **superusers bypass every check**. **Staff** is a separate flag (`is_staff`) used by `require_staff`.

Permissions and groups are ordinary database rows created at run time through the admin API, so the framework does not ship a fixed list. Choose codenames that start with your application name to avoid collisions.

### Protecting routes

`get_router()` and the route decorators accept `permissions`, `require_staff` and `require_superuser`. They can be set on a whole router or on a single route, and the two levels add up (both must pass).

```python
from pachifast.router_factory import get_router

router = get_router(name="billing/invoices")  # any active user


@router.get("")  # inherits: any active user
async def list_invoices(): ...


@router.get("/report", permissions=["billing.report_view"])
async def report(): ...


@router.delete("/{invoice_id}", require_superuser=True)
async def delete_invoice(invoice_id: int): ...


admin_router = get_router(name="billing/admin", require_staff=True)
```

The checks run in this order:

1. superuser: always allowed;
2. `require_superuser`: otherwise `403 access.superuser_required`;
3. `require_staff`: a non-staff user gets `403 access.staff_required`;
4. `permissions`: the user must have **all** listed codenames, otherwise `403 access.permissions_missing` with `params.permissions` listing the missing ones.

```json
{
  "detail": [
    {
      "loc": ["header", "Authorization"],
      "msg": "Missing permissions: billing.report_view",
      "type": "value_error",
      "code": "access.permissions_missing",
      "params": { "permissions": "billing.report_view" }
    }
  ]
}
```

> **Note**: `skip_auth=True` on a route removes only the router's authentication dependency. A `permissions`, `require_staff` or `require_superuser` set on the *router* still applies and still needs a user. Put the check on individual routes if some routes of the same router must stay public.

`PermissionedRouter` is the router class used by `get_router()`; see [Routing and responses](routing-and-responses.md) for all its options, including the authenticated WebSocket example.

### Admin API for permissions and groups

All routes are under `/admin/permissions` and require a superuser.

| Method and path | Description |
| --- | --- |
| `GET /admin/permissions/permissions` | List all permissions. |
| `POST /admin/permissions/permissions` | Create a permission (`codename`, `description`). |
| `PUT /admin/permissions/permissions/{perm_id}` | Update a permission. |
| `DELETE /admin/permissions/permissions/{perm_id}` | Delete a permission. Answers `204`. |
| `GET /admin/permissions/groups` | List groups with their permissions. |
| `POST /admin/permissions/groups` | Create a group (`name`, `description`, `permissionIds`). |
| `PUT /admin/permissions/groups/{group_id}` | Update a group; `permissionIds` replaces the group's permissions. |
| `DELETE /admin/permissions/groups/{group_id}` | Delete a group. Answers `204`. |
| `GET /admin/permissions/users/{user_id}/groups` | Groups of a user. |
| `PUT /admin/permissions/users/{user_id}/groups` | Replace the groups of a user (`groupIds`). |
| `GET /admin/permissions/users/{user_id}/permissions` | Direct permissions of a user. |
| `PUT /admin/permissions/users/{user_id}/permissions` | Replace the direct permissions of a user (`permissionIds`). |
| `GET /admin/permissions/users/{user_id}/effective` | Effective permissions (groups plus direct): `{"permissions": ["..."]}`. |
| `GET /admin/permissions/applications` | List the application routes (path, name, methods, summary, tags), to help building permission lists. Core routes (`/api/auth`, `/api/admin`, `/api/health`, `/api/version`) are excluded. |

Example: create a permission and a group, then grant the group to a user.

```bash
BASE=http://127.0.0.1:8000/api/admin/permissions
AUTH="Authorization: Bearer <token>"

curl -X POST $BASE/permissions -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"codename": "billing.invoice_view", "description": "View invoices"}'

curl -X POST $BASE/groups -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"name": "billing_editors", "description": "Invoice editors", "permissionIds": [1]}'

curl -X PUT $BASE/users/<user-id>/groups -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"groupIds": [1]}'
```

Every change to a user's groups or direct permissions, and every update or deletion of a group, emits a `user_permissions_changed` event to the users concerned, so a frontend can reload `GET /auth/user` and refresh its menus. See [Events](events.md).

Responses add `label`, `localizedDescription` and, when the identifier is a valid key, `labelKey` and `descriptionKey` next to the stored fields, so permission and group names can be translated. See [Errors and i18n](errors-and-i18n.md#translating-permission-and-group-names).

## Multi-factor authentication (MFA)

MFA is optional and supports three methods: TOTP authenticator apps, one-time codes sent by email, and one-time codes sent by SMS. Every method comes with eight single-use backup codes. Every method comes with eight single-use backup codes.

### Enabling MFA

Two things are needed:

1. Set `PACHIFAST_MFA_ENABLED=true`. This makes `/auth/token` check whether the user has MFA configured.
2. Register the MFA module in `project_modules.py` so the `/mfa` routes are mounted:

```python
from pachifast.mfa import MfaModule
from pachifast.presets import core_module

MODULES = (
    core_module,
    MfaModule,
)
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `MFA_ENABLED` | `false` | Require the second step at login for users who enabled MFA. |
| `MFA_TOTP_ISSUER` | `""` | Issuer shown in the authenticator app. Falls back to `app_name`, then `PachiFast`. |
| `MFA_ENCRYPTION_KEY` | `""` | Key of the TOTP secrets, which are stored **encrypted** (Fernet, key derived with HKDF). Empty: derived from `SECRET_KEY`, so rotating `SECRET_KEY` makes the secrets unreadable; set a dedicated key once and keep it. Environment variable or `.env` only. |
| `MFA_OTP_EXPIRE_MINUTES` | `10` | Validity of the pending-login token and of email or SMS codes. |
| `MFA_MAX_ATTEMPTS` | `5` | Wrong codes allowed per login attempt before `429 mfa.too_many_attempts`. Every wrong code also counts on the account (`login_max_attempts`), so logging in again does not give unlimited guesses. |

The MFA tables are part of the core `auth` migration target; `MfaModule` also creates them at startup if they do not exist. See [Database and migrations](database-and-migrations.md).

> **Note**: With `MFA_ENABLED` left at `false`, users with MFA configured log in with the password alone. The `/mfa` routes themselves exist as soon as `MfaModule` is registered, regardless of this flag.

The TOTP secret (`mfa_config.totp_secret`) is encrypted at rest. Do not lose the key: with another `SECRET_KEY` (or another `MFA_ENCRYPTION_KEY`) the secrets cannot be read and the users must enrol again.

### Guarantees of the second step

- A TOTP code works **once**: the 30-second step is recorded in the cache, so an observed code cannot be replayed (with the default in-process cache this holds per worker; set `cache_url` to share it). The code used to confirm the enrolment counts as used.
- A backup code works once, even when two requests present it at the same time.
- Wrong codes are counted on the challenge and on the account, **before** they are checked, so parallel guesses cannot exceed the limits. After `login_max_attempts` wrong codes in total the account is locked like after wrong passwords.
- The one-time code of an email or SMS login is stored before the response is sent, so the client can verify at once; delivery runs in the background and a failure is only logged.

### Login flow

1. The client posts credentials to `/auth/token`. If the user has MFA enabled, the response is `200` with:

   ```json
   { "mfa_required": true, "mfa_token": "<short-lived jwt>" }
   ```

   For the email and SMS methods, a six-digit code is generated at this moment and passed to the sender (see below).

2. The client asks the user for the code and posts it to `/mfa/verify`:

   ```bash
   curl -X POST http://127.0.0.1:8000/api/mfa/verify \
     -H "Content-Type: application/json" \
     -d '{"mfa_token": "<short-lived jwt>", "code": "123456"}'
   ```

3. On success the response contains the usual `access_token`, `token_type` and `refresh_token` (**not** wrapped in the envelope, exactly like `/auth/token`: both login endpoints answer the OAuth2 token shape; `wrap_auth_token_response=true` wraps both). The code can be the TOTP or OTP value or one of the backup codes (a backup code is consumed when used).

Errors of the second step: `401 mfa.token_invalid`, `401 mfa.challenge_not_found`, `401 mfa.code_invalid`, `401 mfa.not_configured`, `429 mfa.too_many_attempts`.

A `429` caused by a locked account carries a `Retry-After` header (seconds until it is unlocked, readable from other origins: the CORS middleware exposes it), so a client can show a countdown. A `429` for a challenge whose attempts are used up has no header: the user must log in again.

### Setup and management routes

All of these require an authenticated, active user except `/mfa/verify`.

| Method and path | Description |
| --- | --- |
| `GET /mfa/status` | `{enabled, method}` for the current user. |
| `POST /mfa/totp/setup` | Start TOTP enrollment. Returns `secret`, `otpauth_uri` (render it as a QR code) and `backup_codes`. MFA is not active yet. `409 mfa.already_enabled` if MFA is already active: disable it first. |
| `POST /mfa/totp/confirm` | Body `{"code": "..."}`. Verifies a code from the app and activates TOTP; `400 mfa.totp_invalid` for a wrong or already used code (the login step answers `401 mfa.code_invalid`). |
| `POST /mfa/email/setup` | Start email OTP enrollment: sends a code to the account's address (`400 mfa.no_email` if it has none) and returns `backup_codes`. The method is not active yet. `409 mfa.already_enabled` if MFA is already active. |
| `POST /mfa/email/confirm` | Body `{"code": "..."}`. Verifies the code that arrived by email and activates the method; `400 mfa.email_code_invalid` for a wrong or expired code, `400 mfa.no_email_setup` if no setup is waiting. |
| `POST /mfa/sms/setup` | Body `{"phone_number": "..."}`. Start SMS OTP enrollment: sends a code to the number and returns `backup_codes`. The method is not active yet. `409 mfa.already_enabled` if MFA is already active. |
| `POST /mfa/sms/confirm` | Body `{"code": "..."}`. Verifies the code that arrived by SMS and activates the method; `400 mfa.sms_code_invalid` for a wrong or expired code, `400 mfa.no_sms_setup` if no setup is waiting. |
| `POST /mfa/disable` | Body `{"password": "..."}`. Remove the MFA configuration. The only way to replace an active second factor: disable it, then enrol again. |
| `POST /mfa/backup-codes/regenerate` | Body `{"password": "..."}`. Issue a new set of backup codes. |

Backup codes are shown only once, at setup or regeneration; the server stores only their hashes. Show them to the user and ask them to keep them safe.

### Sending email and SMS codes

pachifast does not send messages by itself. Subclass `MfaSenderAdapter` and register it before creating the app; the default adapter only logs a warning.

```python
import uuid

from pachifast.mfa import MfaSenderAdapter, configure_sender


class MySender(MfaSenderAdapter):
    async def send_email_otp(
        self, *, user_id: uuid.UUID, email: str, otp_code: str, expires_in_minutes: int
    ) -> None: ...  # send the email with your provider

    async def send_sms_otp(
        self, *, user_id: uuid.UUID, phone_number: str, otp_code: str, expires_in_minutes: int
    ) -> None: ...  # send the SMS with your provider


configure_sender(MySender)
```

## User settings

Every user has a small JSON document of preferences, stored per user and validated against a model that **applications extend**. The model is divided into **sections** (one per app); each field can carry UI metadata so that a generic frontend can render the settings form from a schema, without hard-coding it.

### Endpoints

| Method and path | Description |
| --- | --- |
| `GET /auth/user/settings/schema` | The UI schema: sections, titles, fields and component hints. Each field that is not required carries its `default` (as JSON, `null` included), so a client can offer "reset to defaults" without copying them. |
| `GET /auth/user/settings` | The current values, nested by section, keys in camelCase. |
| `PUT /auth/user/settings/{category}/{key}` | Set one value. Body: `{"value": <string, number, boolean or null>}`. `{category}` is the section name and `{key}` the field name, as `snake_case` or as the camelCase name used by the schema (`disableActivityLog` or `disable_activity_log`; camelCase is accepted from 0.1.7). An unknown section returns `422 user.settings_category_invalid`, an unknown key `422 user.settings_key_invalid`, a value the field does not accept `422 user.settings_value_invalid` with `loc: ["body", "value"]`. |
| `PATCH /auth/user/settings` | Set several values at once. Body: `{"<section>": {"<key>": <value>, ...}, ...}` (for example to reset a form). All values are validated first: if one is refused nothing is saved. Same errors as the `PUT`. |

Both write routes answer with the full, current values. The value is **validated against the field**: its type, a `Literal` list of allowed values and the limits `ge`/`le`/`gt`/`lt` given to `ui_field`. `null` is accepted by the fields that allow it (`str | None`). A stored value that no longer passes (a limit that changed, an option that was removed) reads as the field's default instead of failing the whole document. Values written by `0.2.x` for a field that no longer exists stay in the JSON untouched.

### Built-in sections

| Section | Field | Type and default | `apply` |
| --- | --- | --- | --- |
| `appearance` | `theme` | `dark`, `light` or `auto` (`auto`) | `theme` |
| | `accent` | `teal`, `indigo`, `amber`, `violet` or `coral` (`teal`) | `accent` |
| | `density` | `comf` or `compact` (`comf`) | `density` |
| | `fontScale` | `sm`, `md` or `lg` (`md`) | `fontScale` |
| | `reduceMotion` | boolean (`false`) | `reduceMotion` |
| `preferences` | `locale` | BCP 47 tag such as `it` or `en-GB`, or `null` for automatic (`null`) | `locale` |
| | `timezone` | IANA name such as `Europe/Rome`, or `null` for the browser's (`null`) | |
| | `dateFormat` | `auto`, `dmy`, `mdy` or `ymd` (`auto`) | |
| | `tablePageSize` | integer from 10 to 100 (`25`) | |
| `audit` | `disableActivityLog` | boolean (`false`) | |

The theme lives in `appearance.theme`. A project adds its own sections.

### Defining a section

Create a `user_settings.py` module inside an app or common module (`apps/<name>/user_settings.py` or `common/<name>/user_settings.py`); pachifast discovers it automatically. The section is named by `__section_name__`, or by the folder of the module when omitted.

```python
# apps/billing/user_settings.py
from typing import ClassVar, Literal

from pachifast.auth.user_settings_discover import BaseUserSettings, ui_field


class BillingSettings(BaseUserSettings):
    __section_name__ = "billing"
    _title: ClassVar[str] = "Billing"
    _icon: ClassVar[str] = "receipt_long"
    _route: ClassVar[str] = "/billing"

    default_limit: int = ui_field(50, component="number", label="Default page size")
    compact_view: bool = ui_field(False, component="toggle", label="Compact view")
    invoice_layout: Literal["classic", "compact"] = ui_field("classic", label="Invoice layout")
    rows_per_page: int = ui_field(25, ge=10, le=200, label="Rows per page")
```

A `Literal` field is rendered as a `select` without more code, and `ge`/`le` become the `min`/`max` of the `number`.

`ui_field(default, *, component, label, apply, properties, events, watch, **field_kwargs)`:

| Argument | Meaning |
| --- | --- |
| `component` | The frontend component to render (`input`, `select`, `toggle`, `number`, ...). When omitted it is inferred from the type: `Literal[...]` gives `select`, `bool` gives `toggle`, `int` or `float` give `number`, anything else gives `input`. |
| `label` | The text shown next to the field. |
| `apply` | The name of an **applier** the frontend runs with the field's value whenever it changes and at login (`theme`, `accent`, `density`, `fontScale`, `reduceMotion` and `locale` are standard; a frontend can register more). A plain name, never code. Exposed in the schema as `apply`. |
| `properties` | Props passed to the component, for example `options` or `emitValue`. A `select` over a `Literal` gets `options` (`value` and a readable `label`), `emitValue` and `mapOptions` unless you list them; `ge`/`le` give `min`/`max` to a `number`. |
| `events` | Declarative actions run on a component event. |
| `watch` | Declarative actions run when another field changes. |

The UI metadata only appears in the schema; it is never saved with the user's values.

### Declarative actions: never JavaScript

The schema is delivered to every client, so it must not carry anything a client would have to execute. Instead of code, `events` and `watch` name an action the **frontend** already knows, plus JSON arguments:

```python
events = {"update:modelValue": "toggleDark"}
watch = {"theme": ui_action("applyTheme", mode="dark")}
```

Each trigger is exposed in the schema as `{"action": "toggleDark", "args": {}}`. The frontend keeps a registry of named actions and runs the one the schema names. A string that is not a plain action name, for example `"() => $q.dark.toggle()"`, is rejected with `ValueError` when the field is defined. Action names may contain letters, digits, `_`, `.` and `-` and must start with a letter.

### Translatable schema texts

Every user-visible text in the schema has a stable translation key, derived from the section and field names in snake_case, and the schema adds the key next to the text:

| Text | Key | Schema field |
| --- | --- | --- |
| Section title | `settings.<section>.title` | `titleKey` |
| Field label | `settings.<section>.<field>.label` | `labelKey` |
| Texts inside `properties` (`label`, `hint`, `placeholder`, `title`, `caption`, `description`, `prefix`, `suffix`, `errorMessage`, `message`) | `settings.<section>.<field>.properties.<name>` | `propertyKeys` (`{name: key}`) |
| Label of an option in `properties.options` | `settings.<section>.<field>.options.<value>` | `optionKeys` (`{value: key}`) |

The backend also translates `title`, `label` and the texts in `properties` using the language of the request, and falls back to the text written in the code when a key is missing. Clients that translate from the keys do not depend on that. Writing the catalogs, exporting them and checking what is untranslated (`pachifast locale settings`) is covered in [Errors and i18n](errors-and-i18n.md#translating-user-settings).

## The `pachifast users` CLI

User management from the terminal works directly on the project's authentication database. It reads `.env` and `project_modules.py` from the current directory (or from `--base-dir`), so run it from the project root. The full option list is in the [CLI reference](../reference/cli.md).

```bash
# create a superuser (the password is prompted, hidden, with confirmation)
pachifast users create admin --superuser --email admin@example.org

# update flags and password
pachifast users update mario --staff --active
pachifast users update mario --prompt-password

# list and search (substring of username or email)
pachifast users ls
pachifast users ls rossi

# delete a user and everything linked to it
pachifast users delete mario --yes
```

| Command | Notes |
| --- | --- |
| `users create <username>` | Options: `--password`, `--email`, `--first-name`, `--last-name`, `--staff`, `--superuser`, `--inactive`, `--force`. Prompts for the password when `--password` is missing. |
| `users update <username>` | Options: `--password`, `--prompt-password` / `-P`, `--email`, `--first-name`, `--last-name`, `--staff/--no-staff`, `--superuser/--no-superuser`, `--active/--no-active`, `--force`. |
| `users delete <username>` | Removes groups, permissions, settings and MFA data too. Asks for confirmation unless `--yes` / `-y` is given. |
| `users ls [filter]` | Table of users with active, staff and superuser flags. |

Notes:

- The CLI checks the password against a fixed minimum of 8 characters; `--force` bypasses that check on `update`. On `create`, the server-side `password_min_length` policy still applies even with `--force`.
- `pachifast init` creates the first superuser for you; see [Getting started](../getting-started.md).
- `users update --password` revokes existing tokens, see [Token revocation on password change](#token-revocation-on-password-change).
