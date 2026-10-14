# Contributing

## Development setup

pachifast uses [uv](https://docs.astral.sh/uv/) for everything.

```bash
git clone https://github.com/ellethee/pachifast.git pachifast && cd pachifast
uv sync
uv run pytest            # the test suite
uv run ruff check .      # lint
uv run ruff format --check .   # formatting (drop --check to apply it)
```

Python 3.11 or later is required. Use type hints and docstrings on every function. CI runs the suite on Python 3.11 to 3.14.

The migration lifecycle tests (`tests/test_migrations_lifecycle.py`) and the security tests (`tests/test_security_hardening.py`) run on SQLite. Set `PACHIFAST_TEST_POSTGRES_URL` to an administrative URL (for example `postgresql://user:password@localhost:5432/postgres`) to run them on PostgreSQL too: each test creates throwaway databases and drops them at the end. The atomic counters of the lockout and the MFA limits rely on `UPDATE ... RETURNING` and on date comparisons, which behave differently per database: run the PostgreSQL pass before a release.

SQLAlchemy is locked to 2.0 in `uv.lock`, but users may install 2.1: before a release run the suite against it too, with `uv run --with "sqlalchemy==2.1.3" pytest`.

## Keeping the generated files in sync

Some files are generated from the code, and a test fails when they are stale:

| File | Regenerate with |
| --- | --- |
| `docs/reference/settings.md`, `cli.md`, `error-codes.md`, `pachifast.schema.json`, `docs/changelog.md`, and the version in `README.md`, `README.it.md` and `docs/index.md` | `uv run python scripts/gen_reference.py` |
| `tests/contracts/error_codes.json` | `uv run python scripts/update_contracts.py` |

When you add a setting, give it a description in `src/pachifast/config/schema.py` (a test enforces it). When you add a
message code, add it to **every** language catalog in `src/pachifast/i18n/locale/`, run `uv run pachifast locale check` and record it in the contract file.

## Contracts

Two tests protect what other people build on:

- **Message codes** (`tests/test_error_code_contract.py`): clients translate from the `code` and its `params`, so a code, or a placeholder of a code, is never removed or renamed outside a major release. New codes are recorded on purpose with the script above.
- **Package layering** (`tests/test_architecture.py`): foundation < infrastructure < features < entry points; a package may import its own layer and the ones below. The imports that go up today are listed in `KNOWN_VIOLATIONS` with the reason. The list only shrinks: a new violation fails, and so does an entry that is no longer needed.

## Documentation site

The documentation is markdown in `docs/` and also builds into a static site with [MkDocs](https://www.mkdocs.org/)
(Material theme, configured in `mkdocs.yml`):

```bash
uv run --group docs mkdocs serve            # live preview at http://127.0.0.1:8000
uv run --group docs mkdocs build --strict   # what CI runs: a broken link or a page missing from the nav fails
```

`docs/changelog.md` is a generated copy of `CHANGELOG.md` (see above). The workflow `.github/workflows/docs.yml` publishes the site to GitHub Pages on every push to `main` that touches the documentation, and only builds it on pull requests. To enable it in a fork: repository settings, Pages, Source: "GitHub Actions"; the site is then at `https://<owner>.github.io/<repository>/`.

The site shows the latest documentation only; versioned documentation (one site per release) can be added later with
[mike](https://github.com/jimporter/mike).

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`...), keep the test
suite green and update `CHANGELOG.md` for user-visible changes.

## Releases

Bump `version` in `pyproject.toml` (the only place where it is written: the docs read it from there), refresh `uv.lock`, run `uv run python scripts/gen_reference.py` to stamp it in the READMEs, the home page and the reference pages, commit, tag `vX.Y.Z` and push the tag: the publish workflow (`.github/workflows/publish-pypi.yml`) builds the
package and uploads it to PyPI. See [Publishing](guides/publishing.md).

## Security and conduct

Report vulnerabilities privately, following the repository's `SECURITY.md`, never in a public issue. All
contributors are expected to follow the `CODE_OF_CONDUCT.md`.
