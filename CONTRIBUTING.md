# Contributing

Thanks for helping. The full guide is in [docs/contributing.md](docs/contributing.md); the short version:

```bash
git clone https://github.com/ellethee/pachifast.git && cd pachifast
uv sync
uv run pytest -q                  # tests
uv run ruff check .               # lint, on the whole repository
uv run ruff format --check .      # formatting, on the whole repository
```

- Python 3.11 or later, [uv](https://docs.astral.sh/uv/) for everything.
- To run the migration and security tests on PostgreSQL too, set `PACHIFAST_TEST_POSTGRES_URL` to an administrative URL (for example `postgresql://user:password@localhost:5432/postgres`); each test creates and drops its own databases.
- Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`), keep the tests green and add a line to `CHANGELOG.md` under `[Unreleased]` for anything a user can see.
- Generated files (reference pages, READMEs version) are refreshed with `uv run python scripts/gen_reference.py`.
- Security problems are reported privately, see [SECURITY.md](SECURITY.md). Everyone follows the [code of conduct](CODE_OF_CONDUCT.md).
