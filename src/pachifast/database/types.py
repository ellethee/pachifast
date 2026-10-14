from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import Select

from .base import Base
from .pagination import PageParams

M = TypeVar("M", bound=Base)

ExtraFilter = Callable[[PageParams, dict, Select, Any], tuple[dict, Select]]
