"""Request-scoped embedding cache. Private to the providers package."""

import asyncio
from collections.abc import Callable

from lore.providers.protocols import Embedder, TaskTypeKey


class CachedEmbedder:
    """Wraps an Embedder with per-request memoization.

    Cache key is ``(text, resolved_task_type)``. If the inner embedder exposes
    a ``resolve_task_type`` method, the semantic task_type_key is resolved to
    its vendor string before keying. Vendors without task types collapse all
    keys to None — one cache entry. Satisfies ``Embedder`` structurally.
    The cache lives and dies with the instance.

    Stores the in-flight ``asyncio.Task`` per key so concurrent callers with
    the same key share a single inner ``embed`` call. Without this,
    ``asyncio.gather`` over duplicate keys would race past the lookup and
    fire the inner provider twice, wasting token spend. On failure the entry
    is dropped so retries spin up a fresh task.
    """

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner
        self._resolve: Callable[[TaskTypeKey | None], str | None] | None = getattr(
            inner, "resolve_task_type", None
        )
        self._tasks: dict[tuple[str, str | None], asyncio.Task[list[float]]] = {}

    async def embed(self, text: str, *, task_type_key: TaskTypeKey | None = None) -> list[float]:
        resolved = self._resolve(task_type_key) if self._resolve else task_type_key
        key = (text, resolved)
        if (existing := self._tasks.get(key)) is not None:
            return await existing
        task = asyncio.create_task(self._inner.embed(text, task_type_key=task_type_key))
        self._tasks[key] = task
        try:
            return await task
        except BaseException:
            del self._tasks[key]
            raise
