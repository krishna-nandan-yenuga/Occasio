from datetime import timedelta
from pathlib import Path

import pytest

from app.storage.repo import PlanExistsError, PlanNotFoundError, PlanRepository
from tests.factories import NOW, make_context, make_plan


@pytest.fixture
def repo(tmp_path: Path) -> PlanRepository:
    repository = PlanRepository(tmp_path / "plans.db")
    repository.init_schema()
    return repository


def test_create_then_get_round_trips(repo: PlanRepository) -> None:
    plan = make_plan(make_context(dietary=["jain"]))
    repo.create(plan)
    assert repo.get(plan.id) == plan


def test_get_missing_returns_none(repo: PlanRepository) -> None:
    assert repo.get("nope") is None


def test_ids_are_parameters_not_sql(repo: PlanRepository) -> None:
    repo.create(make_plan(make_context()))
    assert repo.get("x' OR '1'='1") is None
    assert len(repo.list()) == 1


def test_create_rejects_duplicate_id(repo: PlanRepository) -> None:
    plan = make_plan(make_context())
    repo.create(plan)
    with pytest.raises(PlanExistsError):
        repo.create(plan)


def test_update_replaces_stored_plan(repo: PlanRepository) -> None:
    plan = make_plan(make_context())
    repo.create(plan)
    changed = plan.model_copy(update={"ideas": [], "updated_at": NOW + timedelta(hours=1)})
    repo.update(changed)
    assert repo.get(plan.id) == changed


def test_update_missing_plan_raises(repo: PlanRepository) -> None:
    with pytest.raises(PlanNotFoundError):
        repo.update(make_plan(make_context()))


def test_list_is_most_recently_updated_first(repo: PlanRepository) -> None:
    base = make_plan(make_context())
    for n in range(3):
        repo.create(base.model_copy(update={"id": f"p{n}", "updated_at": NOW + timedelta(n)}))
    assert [plan.id for plan in repo.list()] == ["p2", "p1", "p0"]
    assert [plan.id for plan in repo.list(limit=2)] == ["p2", "p1"]


def test_schema_init_is_idempotent_and_keeps_data(tmp_path: Path) -> None:
    first = PlanRepository(tmp_path / "plans.db")
    first.init_schema()
    first.create(make_plan(make_context()))
    second = PlanRepository(tmp_path / "plans.db")
    second.init_schema()
    assert len(second.list()) == 1
