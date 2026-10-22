"""IdeasCache: keys follow exactly what the ideas prompt sees; least recently used is evicted."""

from decimal import Decimal

from app.core.budget import allocate
from app.core.models import Idea
from app.services.ideas_cache import IdeasCache
from tests.factories import make_context

IDEA = Idea(category="theme", title="Retro", description="Seventies music.")


def test_key_changes_with_context_or_budget_only() -> None:
    context = make_context()
    budget = allocate(context)
    key = IdeasCache.key(context, budget)
    assert key == IdeasCache.key(make_context(), allocate(make_context()))
    assert key != IdeasCache.key(make_context(preferences=["kid-friendly"]), budget)
    locked = [budget[0].model_copy(update={"amount": Decimal("1.00")}), *budget[1:]]
    assert key != IdeasCache.key(context, locked)


def test_get_put_and_lru_eviction() -> None:
    cache = IdeasCache(max_entries=2)
    assert cache.get("a") is None
    cache.put("a", [IDEA])
    cache.put("b", [IDEA])
    assert cache.get("a") == [IDEA]  # "a" is now the most recent
    cache.put("c", [IDEA])
    assert cache.get("b") is None
    assert cache.get("a") == [IDEA] and cache.get("c") == [IDEA]


def test_returned_lists_are_copies() -> None:
    cache = IdeasCache()
    cache.put("a", [IDEA])
    cache.get("a").clear()  # type: ignore[union-attr]
    assert cache.get("a") == [IDEA]
