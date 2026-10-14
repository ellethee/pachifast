"""Config helpers."""

from pathlib import Path


def get_env_file(
    env_name: str | None = None,
    use_fallback: bool = True,
    base_dir: str | Path | None = None,
) -> str | None:
    """Get the path to the .env file."""
    root_dir = Path(base_dir or Path.cwd()).resolve()
    env_name = f".env.{env_name}" if env_name else ".env"
    env_path = Path(root_dir, env_name)
    if env_path.exists():
        return str(env_path)

    if use_fallback:
        fallback_path = Path(root_dir, ".env")
        if fallback_path.exists():
            return str(fallback_path)

    return None


default_env_file = get_env_file()
