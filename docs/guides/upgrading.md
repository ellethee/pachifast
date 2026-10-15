# Upgrading

Read the changelog section of every version you cross, from the one you run to the one you install. The complete list is in the [changelog](../changelog.md). While the major version is 0, a minor release may break.

## After every upgrade

After any upgrade of the core, check whether the database needs a revision for the `auth` target:

```bash
uv run pachifast migrations revision "check" --target-name auth --autogenerate   # an empty revision means nothing to do
uv run pachifast migrations upgrade --target-name auth
```

Delete the revision when it is empty.

## How breaking changes are announced

A breaking change is listed in the changelog (under **Changed**, marked **Breaking**, or under **Removed**) and, when it needs an action from you, described in [Upgrade notes](#upgrade-notes) below. The promises behind this, and the deprecation policy, are in [Stability and support](stability.md).

## Pinning

While the major version is 0, pin the minor in your project so that a breaking release never arrives by surprise:

```text
pachifast>=0.15,<0.16
```

Move the pin only after reading the changelog of the new minor.

## Upgrade notes

### Unreleased: `pachifast.yaml` in sections

The settings of `pachifast.yaml` are now grouped in sections (`app`, `server`, `dev`, `log`, `routes`, `database`, `cache`, `auth`, `mfa`, `tables`, `activity_log`, `events`). Flat top-level keys (the 0.15 format) are still read, but each file logs one deprecation warning that lists the new location of every key; move them when convenient. Unknown keys inside a section are now an error, and the same setting given both flat and in its section is an error.

To convert a file, put every key under its section: the key keeps its name, except in the four prefixed sections, where the section replaces the prefix. The [Settings reference](../reference/settings.md) lists the section of every setting.

| Old flat key | New location |
| --- | --- |
| `cache_url`, `cache_prefix`, `cache_max_entries` | `cache.url`, `cache.prefix`, `cache.max_entries` |
| `mfa_enabled`, `mfa_encryption_key`, `mfa_max_attempts`, `mfa_otp_expire_minutes`, `mfa_send_cooldown_seconds`, `mfa_totp_issuer` | `mfa.enabled`, `mfa.encryption_key`, `mfa.max_attempts`, `mfa.otp_expire_minutes`, `mfa.send_cooldown_seconds`, `mfa.totp_issuer` |
| `activity_log_enabled`, `activity_log_exclude_paths`, `activity_log_retention_days`, `activity_log_table` | `activity_log.enabled`, `activity_log.exclude_paths`, `activity_log.retention_days`, `activity_log.table` |
| `events_broadcast_enabled`, `events_allow_query_token`, `events_public_types`, `events_ticket_store` | `events.broadcast_enabled`, `events.allow_query_token`, `events.public_types`, `events.ticket_store` |
| any other key, e.g. `port`, `secret_key`, `disable_routers`, `user_table` | same name under its section: `server.port`, `auth.secret_key`, `routes.disable_routers`, `tables.user_table` |

Regenerate the editor schema with `pachifast config schema -o pachifast.schema.json`. Environment variables are unchanged.
