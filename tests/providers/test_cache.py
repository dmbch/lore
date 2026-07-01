"""Tests for CachedEmbedder: request-scoped embedding cache.

The cache must single-flight concurrent calls with identical keys so
duplicate ``contributes`` strings in a consult batch trigger one inner
``embed`` call, not N. Audit S3.10 surfaced the original
check-then-set race that allowed the second waiter to also fire the
inner provider before the first ever populated the cache.
"""

import asyncio
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel

from lore.providers import Providers, TaskTypeKey
from lore.providers._cache import CachedEmbedder
from lore.providers.protocols import Completer, Embedder

T = TypeVar("T", bound=BaseModel)


class _CountingInner:
    """Minimal Embedder, no ``resolve_task_type``, deterministic outputs.

    Sleeps briefly inside ``embed`` so concurrent ``gather`` calls
    interleave at the await point, which is where the task-map
    contract matters.
    """

    def __init__(
        self,
        *,
        result: list[float] | None = None,
        raises: type[BaseException] | None = None,
    ) -> None:
        self.calls = 0
        self._result = result if result is not None else [0.1, 0.2, 0.3]
        self._raises = raises

    async def embed(self, text: str, *, task_type_key: TaskTypeKey | None = None) -> list[float]:
        del text, task_type_key
        self.calls += 1
        # Yield once so concurrent waiters land on the already-installed
        # task before it resolves.
        await asyncio.sleep(0)
        if self._raises is not None:
            raise self._raises("inner blew up")
        return list(self._result)


class TestCachedEmbedderSingleFlight:
    """Concurrent calls with the same key share one inner embedding."""

    async def test_concurrent_embed_with_duplicate_keys_calls_inner_once(self) -> None:
        inner = _CountingInner()
        cached = CachedEmbedder(cast(Embedder, inner))
        # Five identical keys racing through asyncio.gather. Pre-fix this
        # would call ``inner.embed`` five times; post-fix once.
        results = await asyncio.gather(*(cached.embed("same text") for _ in range(5)))
        assert all(r == [0.1, 0.2, 0.3] for r in results)
        assert inner.calls == 1

    async def test_distinct_keys_each_get_their_own_call(self) -> None:
        inner = _CountingInner()
        cached = CachedEmbedder(cast(Embedder, inner))
        results = await asyncio.gather(
            cached.embed("alpha"),
            cached.embed("beta"),
            cached.embed("gamma"),
        )
        assert all(r == [0.1, 0.2, 0.3] for r in results)
        assert inner.calls == 3

    async def test_repeated_call_after_completion_returns_cached_value(self) -> None:
        inner = _CountingInner()
        cached = CachedEmbedder(cast(Embedder, inner))
        first = await cached.embed("same text")
        second = await cached.embed("same text")
        assert first == second
        assert inner.calls == 1

    async def test_inner_failure_propagates_and_clears_failed_entry(self) -> None:
        """When the inner embed raises, the failed entry is dropped so retries can run.

        Single-flight on the failure path: every concurrent waiter sees
        the same exception. The task-map deletes the failed key after
        propagating, so a subsequent successful retry isn't blocked by a
        stale failed task.
        """

        class _FlakyInner:
            def __init__(self) -> None:
                self.calls = 0

            async def embed(
                self, text: str, *, task_type_key: TaskTypeKey | None = None
            ) -> list[float]:
                del text, task_type_key
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("first call fails")
                return [0.7]

        inner = _FlakyInner()
        cached = CachedEmbedder(cast(Embedder, inner))
        with pytest.raises(RuntimeError, match="first call fails"):
            await cached.embed("same text")
        # Failed entry dropped → second call exercises the inner again.
        assert await cached.embed("same text") == [0.7]
        assert inner.calls == 2


class TestCachedEmbedderTaskTypeResolution:
    async def test_resolved_task_type_disambiguates_cache_entries(self) -> None:
        """Same text with different task_type_keys → distinct cache entries.

        The inner exposes ``resolve_task_type``; different semantic keys
        map to different vendor strings, so the cache key tuple differs
        and the inner is called once per distinct vendor string.
        """

        class _ResolvingInner:
            def __init__(self) -> None:
                self.calls = 0

            async def embed(
                self, text: str, *, task_type_key: TaskTypeKey | None = None
            ) -> list[float]:
                del text, task_type_key
                self.calls += 1
                return [0.1]

            def resolve_task_type(self, key: TaskTypeKey | None) -> str | None:
                return f"vendor::{key}"

        inner = _ResolvingInner()
        cached = CachedEmbedder(cast(Embedder, inner))

        a = await cached.embed("text", task_type_key=cast(TaskTypeKey, "document"))
        b = await cached.embed("text", task_type_key=cast(TaskTypeKey, "question"))
        # Same data shape, distinct cache entries because the resolved
        # vendor strings differ.
        assert a == b == [0.1]
        assert inner.calls == 2

    async def test_inner_without_resolve_caches_per_key(self) -> None:
        """Inner without ``resolve_task_type``: semantic key is the cache key directly."""

        class _BareInner:
            """Minimal Embedder, no ``resolve_task_type`` attribute."""

            def __init__(self) -> None:
                self.calls: list[TaskTypeKey | None] = []

            async def embed(
                self, text: str, *, task_type_key: TaskTypeKey | None = None
            ) -> list[float]:
                del text
                self.calls.append(task_type_key)
                return [0.1]

        inner = _BareInner()
        cached = CachedEmbedder(cast(Embedder, inner))

        # Two distinct semantic keys → two cache entries.
        await cached.embed("text", task_type_key=cast(TaskTypeKey, "document"))
        await cached.embed("text", task_type_key=cast(TaskTypeKey, "question"))
        assert len(inner.calls) == 2

        # Same key again → cached, no extra call.
        await cached.embed("text", task_type_key=cast(TaskTypeKey, "document"))
        assert len(inner.calls) == 2


class _NoopCompleter:
    """Minimal Completer: never called; only satisfies Protocol shape."""

    async def complete(self, *, response_model: type[T], system: str, user: str) -> T:
        del response_model, system, user
        raise NotImplementedError("_NoopCompleter.complete is not exercised")


class TestProvidersSessionLifetime:
    """Each session() entry yields a fresh CachedEmbedder."""

    async def test_providers_session_yields_fresh_cached_embedder(self) -> None:
        """Two sequential sessions must hand out distinct wrapper instances.

        Construction-time contract: ``Providers.session`` builds a new
        ``CachedEmbedder(self.embedder)`` on every entry, so per-request
        cache state never leaks across requests. Regression guard: a
        refactor that memoizes the wrapper would silently turn the cache
        into a process-wide singleton and this assertion would catch it.
        """
        providers = Providers(
            embedder=cast(Embedder, _CountingInner()),
            interpreter=cast(Completer, _NoopCompleter()),
            archivist=cast(Completer, _NoopCompleter()),
        )
        async with providers.session() as s1:
            first = s1.embedder
        async with providers.session() as s2:
            second = s2.embedder
        assert first is not second
