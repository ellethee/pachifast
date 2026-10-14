# Publishing

This page explains how pachifast itself is built and released on PyPI, how to publish to a private registry instead, and how to publish a project generated with pachifast.

## Build

```bash
uv build            # sdist and wheel in dist/
```

## Release on PyPI

pachifast is published on public PyPI by `.github/workflows/publish-pypi.yml`, using [Trusted Publishing](https://docs.pypi.org/trusted-publishers/): no API token is stored in the repository, PyPI trusts the workflow of the GitHub repository directly.

| Trigger | Behaviour |
| --- | --- |
| Push of a tag `vX.Y.Z` | Builds and publishes to PyPI. The tag must equal `v` + `version` in `pyproject.toml`, otherwise the job stops. |
| Manual run from the Actions tab | Builds, checks and uploads to TestPyPI (environment `testpypi`), never touching PyPI. |

One-time setup on PyPI (and on TestPyPI): add a trusted publisher for the project with the owner `ellethee`, the repository `pachifast`, the workflow `publish-pypi.yml` and the environment name (`pypi` on PyPI, `testpypi` on TestPyPI; create both in the repository settings, with a required reviewer on `pypi`). A version that has been uploaded cannot be overwritten or reused: bump the version instead.

To release:

1. Bump `version` in `pyproject.toml`, refresh `uv.lock`, update the changelog.
2. **Dry run on TestPyPI**: run the workflow manually and install the result from TestPyPI in an empty project (`uv pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ pachifast`).
3. **Check** locally: `uv run ruff check .`, `uv run pytest -q`, `uv build`, `uvx twine check dist/*`.
4. **Release**:

   ```bash
   git commit -am "chore: release X.Y.Z"
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

5. Verify with `uv tool install pachifast==X.Y.Z` in a clean environment.

## Publishing to a private registry

The same build can go to a private registry instead, any registry that speaks the PyPI upload protocol (one URL for upload, a `simple` index for reading).

Always give the full upload URL: `uv publish` without `--publish-url` targets public PyPI. Keep credentials in `UV_PUBLISH_USERNAME` / `UV_PUBLISH_PASSWORD`, never in a file of the repository.

```bash
uv build
env -u UV_PUBLISH_INDEX uv publish \
  --publish-url https://<host>/<upload-path> \
  --username <user> dist/*
```

If the registry uses a private certificate authority, add `--system-certs` (otherwise `UnknownIssuer`). To keep a package off PyPI by accident, add `"Private :: Do Not Upload"` to `classifiers`: PyPI rejects any upload, a private registry does not enforce it.

Consumers declare the index without credentials in `pyproject.toml`:

```toml
[[tool.uv.index]]
name = "private"
url = "https://<host>/<path>/simple"
```

and export the read credentials as `UV_INDEX_PRIVATE_USERNAME` / `UV_INDEX_PRIVATE_PASSWORD`. For container builds and CI see [Deployment](deployment.md#private-package-index).

## Publish a project generated with pachifast

A project created with `pachifast new project` has a ready `pyproject.toml` (name, version, authors, license, dependencies) and is a normal Python package. For a service, the usual deliverable is a **container image**, not a wheel: generate the pipeline with `pachifast new pipeline` and tag a release (`v*`) to build and push the image. See [Deployment](deployment.md).

To publish a project as a package instead (for example a shared module or a library of common modules):

1. Set the metadata in `pyproject.toml` (`--project-version`, `--description`, `--license`, `--repository`... can be passed to `new project`).
2. Decide where it goes. If it must stay private, add `"Private :: Do Not Upload"` to `classifiers`: PyPI then rejects any upload.
3. Build and upload with the commands on this page (public PyPI, or your registry with an explicit `--publish-url`).
4. To automate it, copy `.github/workflows/publish-pypi.yml` from the pachifast repository and adapt it.

## Related pages

- [Deployment](deployment.md)
- [Contributing](../contributing.md)
