from pathlib import Path

from alembic import context

from pachifast.migrations.runtime import run_env

config = context.config
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TARGET_NAME = "auth"

run_env(config, TARGET_NAME, PROJECT_ROOT)
