# Operations

This page covers day-to-day operation of a pachifast project: starting the server, health and version endpoints, logging, UDP listeners, first-time bootstrap, and a security checklist to run through before going to production.

## Running the server

pachifast has two server commands, both thin wrappers around uvicorn. They run from the project directory (the one that contains `main.py` and `project_modules.py`) or accept `--base-dir`. In both cases the process moves into the project directory, so `.env` and the YAML configuration file are found wherever you launch it from.

### `pachifast dev`

For development: automatic reload.

```bash
pachifast dev
pachifast dev --port 9000
```

| Option | Default | Description |
| --- | --- | --- |
| `--base-dir PATH` | `.` | Project root containing `main.py`. |
| `--app TEXT` | `main:app` | ASGI application import string. |
| `--host TEXT` | configuration, else `127.0.0.1` | Bind address. |
| `--port INTEGER` | configuration, else `8000` | Bind port. |

The server restarts when `*.py`, `*.yaml`, `*.yml` or `.env` files change in the project (including `apps/` and `common/`). The directories `.git`, `.venv`, `node_modules` and the pytest, ruff and mypy caches are ignored.

### `pachifast serve`

For production: no reload, several workers, proxy headers.

```bash
pachifast serve
pachifast serve --workers 4 --port 8080
```

| Option | Default | Description |
| --- | --- | --- |
| `--base-dir PATH` | `.` | Project root containing `main.py`. |
| `--app TEXT` | `main:app` | ASGI application import string. |
| `--host TEXT`, `--port INTEGER` | configuration, else `127.0.0.1` and `8000` | Bind address. |
| `--workers INTEGER` | `1` | Number of worker processes (minimum 1). |
| `--proxy-headers` / `--no-proxy-headers` | enabled | Trust `X-Forwarded-For` and `X-Forwarded-Proto`, but only from the addresses allowed below. |
| `--forwarded-allow-ips TEXT` | uvicorn's default | Comma-separated IPs allowed to send proxy headers. Default: `127.0.0.1` only, or the `FORWARDED_ALLOW_IPS` environment variable. |
| `--log-level TEXT` | `info` | Uvicorn log level: `critical`, `error`, `warning`, `info`, `debug`, `trace`. |

For host and port the priority is: command-line option, environment variables (`PACHIFAST_HOST`, `PACHIFAST_PORT`), `.env`, the YAML file, then the defaults. The container image, reverse proxy and pipeline side are described in [Deployment](deployment.md); every option is also listed in the [CLI reference](../reference/cli.md).

> **Note**: with several workers, anything kept in process memory is per worker. This affects the in-memory [cache](cache.md) and WebSocket tickets stored in it; see [Events](events.md#where-tickets-are-stored).

## Health and version endpoints

Both endpoints are public: no authentication is required.

### `GET /api/health/`

Reports whether the service is up and whether the auth database answers a `SELECT 1`.

```bash
curl http://localhost:8000/api/health/
```

```json
{
  "success": true,
  "message": null,
  "data": {
    "name": "My Project",
    "version": "1.0.0",
    "description": "Billing API",
    "database": true
  }
}
```

`name`, `version` and `description` come from the `project_name`, `version` and `description` settings. `database` is `false` when `AUTH_DATABASE_URL` is missing or the database is unreachable; the endpoint itself still answers `200`, so an orchestrator that must react to a failing database should check the `database` field. Use `GET /api/health/` as the container health check or load-balancer probe.

### `GET /api/health/live` and `GET /api/health/ready`

Two probes for an orchestrator (Kubernetes, Docker, a load balancer):

- `live` answers `200` `{"status": "ok"}` while the process serves requests and never touches the database: use it as the *liveness* probe, so a database outage does not restart the container.
- `ready` answers `200` with the same payload as `GET /api/health/` when the database answers `SELECT 1` within 3 seconds, and `503` with the code `health.not_ready` otherwise: use it as the *readiness* probe.

### `GET /api/version/`

Returns the version of the installed **pachifast package** (not of your project):

```json
{ "success": true, "message": null, "data": { "version": "0.1.6" } }
```

> **Note**: the examples show the default response envelope. With `envelope_enabled=false` the bodies are the bare objects. See [Routing and responses](routing-and-responses.md).

Both routers belong to the core module, so they can be switched off with `disable_core_routers` or `disable_core_routes` (see [Modules](modules.md)).

## Logging

The application logger is configured when the app is created: coloured output on stderr, one line per record, each with the id of the request being handled (`[-]` outside a request).

| Setting | Environment variable | Default | Effect |
| --- | --- | --- | --- |
| `log_level` | `PACHIFAST_LOG_LEVEL` | empty | `CRITICAL`, `ERROR`, `WARNING`, `INFO` or `DEBUG` (case-insensitive). Empty means `INFO`, or `DEBUG` when `debug` is true. Any other value is rejected at startup. |
| `log_format` | `PACHIFAST_LOG_FORMAT` | `text` | `text` (coloured) or `json`: one object per line with `time`, `level`, `logger`, `message`, `request_id` and, for an exception, `exc_info`. |
| `request_id_enabled` | `PACHIFAST_REQUEST_ID_ENABLED` | `true` | Every response carries an `X-Request-ID` header and the log lines of the request carry the same id. An id sent by the client (or a proxy) is kept when it is made of letters, digits, `-`, `_`, `.` and at most 64 characters; otherwise a new one is made. |
| `debug` | `PACHIFAST_DEBUG` | `false` | Development mode: an unhandled error returns the traceback to the client, and the default log level becomes `DEBUG`. |
| `dbdebug` | `PACHIFAST_DBDEBUG` | `false` | SQLAlchemy echoes every SQL statement. |

`serve --log-level` only affects the uvicorn logs (startup, access); `log_level` controls the logs of the application and of pachifast itself.

```bash
PACHIFAST_LOG_LEVEL=WARNING pachifast serve
```

> **Warning**: never enable `debug` in production. Tracebacks reveal server paths and internals to clients. Because environment variables take precedence over the YAML file, a `debug: true` left in `pachifast.yaml` is overridden by `PACHIFAST_DEBUG=false`. `dbdebug` can write sensitive query parameters to the log.

## Connection pool

A server database (PostgreSQL) keeps a pool of connections **per process**: with four workers and the defaults the server may see up to 4 × (5 + 10) connections. SQLite has no pool.

| Setting | Default | Effect |
| --- | --- | --- |
| `db_pool_size` | `5` | Connections kept open. |
| `db_max_overflow` | `10` | Extra connections allowed under load. |
| `db_pool_timeout` | `30` | Seconds a request waits for a free connection before it fails. |
| `db_pool_recycle` | `1800` | Seconds after which a connection is replaced (`-1` never). |

## Activity log retention

The activity log only grows. `pachifast audit purge --days 90` deletes the rows older than 90 days (`--dry-run` only counts them); without `--days` it uses `activity_log_retention_days`. Run it from cron or a systemd timer:

```bash
0 3 * * *  cd /srv/myproject && pachifast audit purge --days 90
```

The table has an index on `(user_id, timestamp)` for the activity of one user.

## UDP listeners

pachifast includes a small asyncio helper for receiving UDP datagrams in the same process as the API (for example from devices or log shippers). It does not go through FastAPI routing: you attach it to the application lifecycle with module hooks.

```python
from fastapi import FastAPI

from pachifast import AppModule
from pachifast.udp import UdpListenerConfig, udp_registry


async def echo_handler(data: bytes, addr: tuple[str, int], transport) -> None:
    transport.sendto(data, addr)


async def start_udp(app: FastAPI) -> None:
    await udp_registry.start(UdpListenerConfig(name="echo", host="0.0.0.0", port=9999, handler=echo_handler))


async def stop_udp(app: FastAPI) -> None:
    await udp_registry.stop("echo")


module = AppModule(
    name="udp_echo",
    kind="app",
    startup_hooks=(start_udp,),
    shutdown_hooks=(stop_udp,),
)
```

Elements of `pachifast.udp`:

| Name | Description |
| --- | --- |
| `UdpListenerConfig(name, host, port, handler, protocol_factory=None)` | Describes a listener. `handler(data, addr, transport)` may be sync or async. `protocol_factory` lets you supply your own `asyncio.DatagramProtocol`. |
| `udp_registry` | The process-wide registry (also available as `app.state.udp_registry`). |
| `await udp_registry.start(config)` | Start a listener; starting an existing name returns the running one. |
| `await udp_registry.stop(name)` / `stop_all()` | Close one or all listeners. |
| `udp_registry.get(name)`, `list()` | Inspect running listeners (`list()` returns name, host, port, address). |
| `udp_registry.sendto(name, data, addr)` | Send a datagram from a listener's socket. Raises `KeyError` for an unknown name. |
| `BaseUdpProtocol` | The default protocol: calls the handler, runs async handlers as tasks and logs their errors. |

If the listener must live as long as some other resource, use `lifespan_hooks` instead of `startup_hooks` and `shutdown_hooks` (see [Modules](modules.md)).

> **Note**: with `--workers N` every worker process starts its own listener. Only one process can bind a given UDP port, so the other workers fail to start their listener. Run UDP listeners in a single-worker deployment (or in a dedicated service).

## Bootstrapping a project

`pachifast init` prepares a freshly generated project (`pachifast new project <name>`) in one step:

```bash
pachifast init
pachifast init --username admin --email admin@example.com
pachifast init --database-url "postgresql+asyncpg://user:password@localhost:5432/app" --no-user
```

It performs three idempotent steps:

1. **`.env`**: creates it, or fills in missing values, with a random `PACHIFAST_SECRET_KEY` and `PACHIFAST_AUTH_DATABASE_URL` (default: a SQLite file `db.sqlite3` in the project). Existing values are never overwritten; new files get mode `0600`. Values already present in the YAML file are not copied.
2. **Migrations**: creates the `auth` target layout and its first autogenerated revision if they do not exist, then applies `upgrade head`.
3. **First superuser**: creates it unless the username already exists.

| Option | Default | Description |
| --- | --- | --- |
| `--base-dir PATH` | `.` | Project root containing `project_modules.py`. |
| `--username`, `-u` | `admin` | Superuser username. |
| `--password` | prompted | Superuser password. Prefer the prompt: passwords given on the command line stay in your shell history. |
| `--email` | none | Superuser email. |
| `--database-url` | SQLite `db.sqlite3` in the project | Auth database URL. |
| `--target-name` | `auth` | Migration target of the auth tables. |
| `--no-user` | off | Do not create a superuser. |

`init` refuses to continue if a `PACHIFAST_SECRET_KEY` or `PACHIFAST_AUTH_DATABASE_URL` exported in your shell differs from the value that would go into `.env`, because the environment would silently override the file. Unset the variable and run it again.

Users can also be managed later with `pachifast users create|update|delete|ls`. See the [CLI reference](../reference/cli.md) and [Getting started](../getting-started.md).

## Production security checklist

Go through this list before exposing a project.

| Check | Why |
| --- | --- |
| `PACHIFAST_SECRET_KEY` is random, at least 32 characters, unique per environment, and stored in a secret manager or the environment (never in Git or the YAML file) | It signs every JWT. The application refuses to start without it, or with a shorter one. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`. Changing it invalidates all issued tokens. |
| `PACHIFAST_DEBUG=false` (the default) | Tracebacks and server paths must not reach clients. |
| `global_noauth` is `false` | It turns off authentication on **every** router. Development only. |
| `swagger_noauth` is `false` | It lets requests coming from the Swagger UI act as the first superuser without a token. Development only; it only answers the loopback, so it does nothing useful behind a reverse proxy. |
| `PACHIFAST_EVENTS_ALLOW_QUERY_TOKEN=false` | Forces one-time tickets, so access tokens never appear in WebSocket URLs and proxy logs. See [Events](events.md#legacy-token). |
| `dbdebug` is `false` | Avoids SQL and parameters in the logs. |
| The service is served over HTTPS (TLS terminated at a reverse proxy or load balancer) | Tokens and passwords travel in requests. |
| Proxy headers are trusted only from the proxy: `--forwarded-allow-ips` is the proxy address or network, and `*` is used only if the port is reachable through the proxy alone | Otherwise clients can forge `X-Forwarded-*` headers (client IP, scheme). |
| CORS origins are an explicit list | Only the front-end origins you control. See [Frontend integration](frontend-integration.md). |
| Credentials (database passwords, Redis URL) come from the environment or `.env` with restricted permissions | Keep them out of the YAML file and version control. |
| Migrations are applied with `pachifast migrations upgrade` as a release step, and `auto_create_auth_schema` is `false` | Reproducible schema changes. See [Database and migrations](database-and-migrations.md). |
| `events_public_types` does not include `activity` unless intended | It would show every user's requests to every connected client. |
| Unneeded core routers are disabled (`disable_core_routers`, `disable_core_routes`) | Smaller attack surface; can be changed by configuration only. |
| `/api/health/` is used for probes | Public by design and exposes only the project name, version and database status. |

A minimal production `.env`:

```env
PACHIFAST_SECRET_KEY=<64 random hex characters>
PACHIFAST_AUTH_DATABASE_URL=postgresql+asyncpg://app:<password>@db:5432/app
PACHIFAST_DEBUG=false
PACHIFAST_EVENTS_ALLOW_QUERY_TOKEN=false
```

## See also

- [Deployment](deployment.md): container image, reverse proxy, pipelines.
- [Configuration](configuration.md) and the [settings reference](../reference/settings.md).
- [Authentication](authentication.md): tokens, MFA and users.
