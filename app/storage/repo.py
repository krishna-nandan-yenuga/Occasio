"""SQLite plan repository: each plan is one JSON document keyed by its id.

Every query is parameterised. A short-lived connection per operation keeps the repository safe
to use from FastAPI's worker threads without sharing a connection.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from app.core.models import Plan

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class PlanExistsError(ValueError):
    """A plan with this id is already stored."""


class PlanNotFoundError(LookupError):
    """No plan with this id is stored."""


class PlanRepository:
    """Stores and loads plans in the SQLite database at `path`."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)

    def init_schema(self) -> None:
        """Create the tables if they do not exist yet; safe to call on every startup."""
        with self._connect() as connection:
            connection.execute(SCHEMA)

    def create(self, plan: Plan) -> None:
        """Store a new plan; raises PlanExistsError if the id is taken."""
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO plans (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (
                        plan.id,
                        plan.model_dump_json(),
                        plan.created_at.isoformat(),
                        plan.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise PlanExistsError(plan.id) from error

    def get(self, plan_id: str) -> Plan | None:
        """The plan with `plan_id`, or None."""
        with self._connect() as connection:
            row = connection.execute("SELECT data FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return None if row is None else Plan.model_validate_json(row[0])

    def update(self, plan: Plan) -> None:
        """Replace a stored plan; raises PlanNotFoundError if it does not exist."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE plans SET data = ?, updated_at = ? WHERE id = ?",
                (plan.model_dump_json(), plan.updated_at.isoformat(), plan.id),
            )
        if cursor.rowcount == 0:
            raise PlanNotFoundError(plan.id)

    def list(self, limit: int = 50) -> Sequence[Plan]:
        """Up to `limit` plans, most recently updated first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM plans ORDER BY updated_at DESC, id LIMIT ?", (limit,)
            ).fetchall()
        return [Plan.model_validate_json(row[0]) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits on success, rolls back on error, and is always closed."""
        connection = sqlite3.connect(self._path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()
