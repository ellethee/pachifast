"""Project scaffolding utilities for pachifast."""

from pachifast.scaffold.generators import create_app, create_common, create_pipeline, create_project, register_module
from pachifast.scaffold.helpers import normalize_name
from pachifast.scaffold.metadata import ProjectMetadata, parse_keywords, parse_requirements
from pachifast.scaffold.pipelines import DEFAULT_PYTHON_VERSION, PipelineReport, Provider

__all__ = [
    "DEFAULT_PYTHON_VERSION",
    "PipelineReport",
    "ProjectMetadata",
    "Provider",
    "create_app",
    "create_common",
    "create_pipeline",
    "create_project",
    "normalize_name",
    "parse_keywords",
    "parse_requirements",
    "register_module",
]
