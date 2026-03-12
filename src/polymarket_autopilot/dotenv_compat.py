"""Local fallback for python-dotenv when dependency installation is unavailable."""

from __future__ import annotations

from typing import Any


def load_dotenv(*args: Any, **kwargs: Any) -> bool:
    return False
