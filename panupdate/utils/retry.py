"""Async retry decorator with exponential backoff."""

import asyncio
from functools import wraps
from typing import Type, Callable


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
):
    """Decorator: retry an async function on exception with exponential backoff."""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        wait = delay * (backoff ** attempt)
                        await asyncio.sleep(wait)
            raise last_exc  # type: ignore
        return wrapper
    return decorator
