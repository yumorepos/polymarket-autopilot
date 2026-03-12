"""Local fallback for python-dotenv when dependency installation is unavailable."""

from __future__ import annotations


def load_dotenv(*args, **kwargs) -> bool:
    return False
