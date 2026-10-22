"""In-memory LRU cache of generated ideas, keyed by event context and budget split.

Ideas depend only on what the LLM is shown, the context and the budget, so an identical plan
can reuse them instead of paying for another LLM call. Single-process, like the rate limiter.
"""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Sequence

from app.core.models import BudgetLine, EventContext, Idea


class IdeasCache:
    """Keeps the ideas for the `max_entries` most recently used contexts."""

    def __init__(self, max_entries: int = 256) -> None:
        self._entries: OrderedDict[str, list[Idea]] = OrderedDict()
        self._max_entries = max_entries

    @staticmethod
    def key(context: EventContext, budget: Sequence[BudgetLine]) -> str:
        """A stable hash of exactly what the ideas prompt is built from."""
        payload = {
            "context": context.model_dump(mode="json"),
            "budget": [[line.category, str(line.amount), str(line.percent)] for line in budget],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def get(self, key: str) -> list[Idea] | None:
        """Cached ideas for `key`, marking them recently used, or None."""
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return list(self._entries[key])

    def put(self, key: str, ideas: Sequence[Idea]) -> None:
        """Store ideas for `key`, evicting the least recently used entry when full."""
        self._entries[key] = list(ideas)
        self._entries.move_to_end(key)
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
