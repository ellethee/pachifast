# Events and Activity Log

pachifast can push events to browsers over a WebSocket (who logged in, what changed, which requests were made) and can record every write request in an activity log. Both features are off by default, and both are aimed mainly at administrators.

This page covers:

- enabling the event stream and the shape of its messages;
- authenticating a WebSocket with one-time tickets;
- who receives which event, and the close codes you must handle;
- the activity log and its endpoints;
- a complete browser client.

## Enable the event stream

```env
PACHIFAST_EVENTS_BROADCAST_ENABLED=true
```

This mounts two routes under `/api/core/events`:

| Route | Purpose |
| --- | --- |
| `POST /api/core/events/ticket` | Authenticated with `Authorization: Bearer <access token>`. Returns a one-time ticket. |
| `GET /api/core/events/ws` | The WebSocket endpoint. |

When the setting is off, neither route exists and a WebSocket handshake receives HTTP `403` whatever credentials it carries.

### Message format

Every event is a JSON text frame with the same envelope:

```json
{
  "type": "login",
  "timestamp": "2026-10-05T09:30:12.123456+00:00",
  "data": { "userId": "3f1c...", "username": "alice" }
}
```

| Field | Description |
| --- | --- |
| `type` | The event type (see [Event types](#event-types)). |
| `timestamp` | UTC time at which the server emitted the event, in ISO 8601. |
| `data` | The payload. Keys of live events are camelCase. |

Two frames are sent by the connection itself and do not carry a `timestamp`:

- **`snapshot`**: sent once, right after the connection is accepted: `{"type": "snapshot", "data": [...]}`. For superusers `data` holds the last 20 activity log entries, oldest first. For everyone else it is an empty list. Entries in the snapshot keep the database field names in snake_case (`user_id`, `status_code`, `duration_ms`, ...), unlike live events.
- **`ping`**: `{"type": "ping"}`, sent every 30 seconds. Clients do not need to answer it; see [Connection checks](#connection-checks).

> **Note**: the snapshot reads the activity log table. If that table does not exist yet (older project, migrations not applied), the snapshot is empty, a warning is logged once per process, and the connection stays open. See [Database and migrations](database-and-migrations.md#upgrading-an-existing-project).

## Authentication

A WebSocket URL ends up in proxy and access logs, so it must not contain a long-lived credential. pachifast uses one-time tickets for this.

### One-time tickets

1. The client asks for a ticket with an authenticated `POST`:

   ```bash
   curl -X POST http://localhost:8000/api/core/events/ticket \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```

   ```json
   { "ticket": "kP3x...", "expires_in": 30 }
   ```

   This response is not wrapped in the standard envelope.

2. The client opens `GET /api/core/events/ws?ticket=<ticket>` within 30 seconds.

A ticket is valid **once**: ask for a new one at every (re)connection. The user is checked again when the socket opens. Only the SHA-256 digest of a ticket is ever stored, so a copy of the storage cannot be used to open sockets.

### Where tickets are stored

| `events_ticket_store` | Storage | Use it when |
| --- | --- | --- |
| `database` (default) | The `ws_ticket` table of the auth database (name follows `table_prefix`). Expired rows are purged whenever a new ticket is issued. | You run several workers or hosts and have no Redis. Needs the `ws_ticket` table (see below). |
| `cache` | The configured [cache](cache.md): process memory by default, Redis when `cache_url` is set. | You already run Redis, or a single worker is enough. |

> **Warning**: with `events_ticket_store=cache` and the default in-memory cache, a ticket issued by one worker cannot be redeemed by another. Set `cache_url` to a Redis server as soon as you run more than one worker. If the cache is unreachable, issuing or redeeming a ticket fails with a server error.

Projects that use Alembic need a revision for the `ws_ticket` table; see [Database and migrations](database-and-migrations.md#upgrading-an-existing-project).

### Legacy `?token=`

For backward compatibility the socket still accepts the access token directly: `/ws?token=<access token>`. The token is then part of the URL and may be written to logs. Disable this in production:

```env
PACHIFAST_EVENTS_ALLOW_QUERY_TOKEN=false
```

With the setting off, a connection that only sends `?token=` is closed with `4401`. If both `ticket` and `token` are present, the ticket is used.

## Close codes

The server always accepts the handshake first and then closes the socket, so that browsers see the close code (closing before accepting would turn into an opaque HTTP 403).

| Code | Meaning | What the client should do |
| --- | --- | --- |
| `4401` | Not authorised: missing, invalid, expired or already used ticket; wrong token type (a refresh token is rejected); token issued before the last password change; unknown or deactivated user; `?token=` refused by `events_allow_query_token=false`. It is also sent on an **open** connection when the user is deleted, deactivated or changes password. | Do not retry with the same credential. Request a new ticket (or send the user to the login page if the access token cannot be refreshed). |
| `4403` | The user's superuser status changed (promoted or demoted). | Reconnect with a new ticket to receive the events of the new role. |
| `1011` | Unexpected server error, for example the database is unreachable during authentication. The detail is in the server log. | Retry later, with a back-off. |

### Connection checks

Every 30 seconds the server re-reads the user, sends the `ping`, and closes the connection with `4401` or `4403` if something changed. This also catches changes made outside the API, for example with the `pachifast users` command. The deadline is absolute, so a client that keeps sending messages cannot postpone the check.

- The check ignores token expiry: clients refresh their access token without reopening the socket.
- If the check itself fails (for example the database is down), the connection stays open and the error is logged.
- `PATCH` and `DELETE /api/admin/user/{id}` close the user's sockets immediately instead of waiting for the next check.

## Who receives what

No event is broadcast to everybody by default. An event reaches the superusers plus, for events that concern one user, that user. Superuser membership is the one the user had when the connection was opened (a change closes the connection with `4403`, as above).

### Event types

| Type | Payload (`data`) | Default audience |
| --- | --- | --- |
| `login` | `{userId, username}` | Superusers |
| `logout` | `{userId, username}` | Superusers |
| `activity` | `{userId, username, method, path, statusCode, description}`; only with the activity log enabled | Superusers |
| `user_permissions_changed` | `{userId}` | Superusers and the affected user |

`logout` is emitted by `POST /api/auth/logout` (access token in the header, response `204` without body). JWTs are stateless: the server does not invalidate them, so the client must discard both tokens itself. The endpoint exists to notify the others. It disappears if you disable the `auth` core router (see [Modules](modules.md)).

### Public event types

To tell every connected user that, for example, somebody has logged in or out, list the types in `events_public_types` (comma separated in the environment, a list in YAML):

```env
PACHIFAST_EVENTS_PUBLIC_TYPES=login,logout
```

```yaml
events_public_types: [login, logout]
```

Listed types go to **all** connected clients.

> **Warning**: `activity` contains method and path of every user's requests. Make it public only if that is really what you want.

## Activity log

The activity log records write requests in the auth database and, optionally, streams them as `activity` events.

```env
PACHIFAST_ACTIVITY_LOG_ENABLED=true
```

### What is recorded

- Requests with method `POST`, `PUT`, `PATCH` or `DELETE` whose path starts with `/api`. `GET` requests are never logged.
- Responses with a status below 500. Server errors are not logged.
- The user is read from the bearer token without a database query. Requests without a valid token are logged with no user.
- Each entry stores timestamp, user id, username (denormalised, so the log outlives the user; there is no foreign key), method, path, status code, duration in milliseconds and an optional description.

The description comes from the `log_as` template of the route, for example:

```python
@router.patch("/{id}", log_as="{username} updated item {id}")
async def update_item(id: int): ...
```

Path parameters and `username` can be used as placeholders; unknown placeholders are left untouched. See [Routing and responses](routing-and-responses.md).

### Endpoints

Enabling the log mounts two superuser-only routes. Query parameters are camelCase.

| Route | Description |
| --- | --- |
| `GET /api/admin/audit/logs` | Paginated list (`page`, `pageSize`), newest first by default. |
| `GET /api/admin/audit/activity` | The most recent entries as a plain list (`limit`, 1 to 500, default 10). Ignores pagination. |

Filters accepted by both routes:

| Parameter | Description |
| --- | --- |
| `orderBy` | Sort field; `-timestamp` (default) means newest first. |
| `search` | Case-insensitive text search in `username` and `path`. |
| `userId` | Exact user id (UUID). |
| `method` | Exact HTTP method. |
| `statusCode` | Exact status code. |
| `dateFrom`, `dateTo` | Inclusive timestamp range. |
| `pathContains` | Keep paths containing the string. |
| `excludePaths` | Comma-separated substrings to drop from the result. |

```bash
curl -G http://localhost:8000/api/admin/audit/logs \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  --data-urlencode "method=DELETE" \
  --data-urlencode "pageSize=20"
```

`excludePaths` only filters what the endpoints **return**; the entries are still stored. When the parameter is absent, the `activity_log_exclude_paths` setting is applied (default `refresh-token`, which hides token refresh noise). Pass an empty string (`excludePaths=`) to disable the default.

### Let a superuser opt out

Every superuser has a user setting, `audit.disable_activity_log` ("Disable activity log", meant for development and debugging only). When it is true, that user's requests are not logged and no `activity` event is emitted for them. The choice is remembered for 30 seconds in the [cache](cache.md), so a change can take up to 30 seconds to apply. If the cache is unavailable, the setting is read from the database and a warning is logged.

### Untrusted data

`path`, `description` and `username` contain what a user typed (for example path parameters), so they are untrusted. Before storing them, and before sending the `activity` event, the server:

- replaces control characters (C0, C1, line and paragraph separators) with spaces, which prevents log forging;
- truncates `path` and `description` to 500 characters and `username` to 150, ending a truncated value with an ellipsis.

> **Warning**: sanitising is not escaping. Clients must **always** render these fields as plain text, never as HTML.

## Browser client

The example below requests a ticket, opens the socket and applies the rules above: it never retries on `4401` with a stale credential, and it reconnects with a fresh ticket on `4403`.

```javascript
const API = "https://api.example.com";

/** Ask the API for a one-time ticket (valid 30 seconds, single use). */
async function fetchTicket(accessToken) {
  const res = await fetch(`${API}/api/core/events/ticket`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error(`ticket request failed: ${res.status}`);
  return (await res.json()).ticket;
}

/**
 * Open the event stream and keep it open.
 * getAccessToken must return a valid (refreshed if needed) access token.
 */
function connectEvents(getAccessToken, onEvent, onLogout) {
  let delay = 1000;

  async function open() {
    let ticket;
    try {
      ticket = await fetchTicket(await getAccessToken());
    } catch {
      return setTimeout(open, delay = Math.min(delay * 2, 30000));
    }

    const ws = new WebSocket(
      `${API.replace(/^http/, "ws")}/api/core/events/ws?ticket=${encodeURIComponent(ticket)}`,
    );

    ws.onopen = () => { delay = 1000; };

    ws.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === "ping") return;
      onEvent(event);
    };

    ws.onclose = (close) => {
      if (close.code === 4401) {
        // Not authorised: do not loop. Let the app refresh the session or show the login page.
        onLogout();
      } else if (close.code === 4403) {
        // Role changed: reconnect right away with a new ticket.
        open();
      } else {
        // 1011, network error, server restart: retry with exponential back-off.
        setTimeout(open, delay = Math.min(delay * 2, 30000));
      }
    };
  }

  open();
}
```

When rendering an `activity` event, use `textContent` (or your framework's text binding), never `innerHTML`:

```javascript
connectEvents(getToken, (event) => {
  if (event.type === "activity") {
    const li = document.createElement("li");
    li.textContent = `${event.data.username} ${event.data.method} ${event.data.path}`;
    list.append(li);
  }
}, () => location.assign("/login"));
```

## Settings summary

| Setting | Default | Description |
| --- | --- | --- |
| `events_broadcast_enabled` | `false` | Mounts the WebSocket and ticket routes. |
| `events_ticket_store` | `database` | `database` or `cache`. |
| `events_allow_query_token` | `false` | Accept the legacy `?token=` (the token would end up in access logs). Off by default. |
| `events_public_types` | empty | Event types sent to every connected client. |
| `activity_log_enabled` | `false` | Records write requests and mounts the audit routes. |
| `activity_log_exclude_paths` | `refresh-token` | Default `excludePaths` of the audit routes. |

All options are listed in the [settings reference](../reference/settings.md).

## See also

- [Authentication](authentication.md): access tokens, refresh tokens and logout.
- [Cache](cache.md): the store behind tickets and the activity-log opt-out.
- [Database and migrations](database-and-migrations.md): the `activity_log` and `ws_ticket` tables.
- [Operations](operations.md): production security checklist.
