"""Every route through the real app (lifespan included) with httpx.AsyncClient and MockProvider."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response

from app.api.deps import get_now, get_today
from app.config import Settings
from app.llm.mock_provider import MockProvider
from app.llm.provider import LLMProvider
from app.main import create_app
from tests.factories import DEMO_TEXT, NOW, TODAY, FailingProvider

CONTEXT = {
    "event_type": "birthday",
    "event_date": "2026-12-05",
    "guest_count": 40,
    "total_budget": "150000",
    "location_tier": "city",
    "formality": "semi",
}


@asynccontextmanager
async def make_client(
    tmp_path: Path, provider: LLMProvider | None = None, per_minute: int = 1000
) -> AsyncIterator[AsyncClient]:
    settings = Settings(database_path=str(tmp_path / "test.db"), rate_limit_per_minute=per_minute)
    app = create_app(settings, provider or MockProvider())
    app.dependency_overrides[get_today] = lambda: TODAY
    app.dependency_overrides[get_now] = lambda: NOW
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with make_client(tmp_path) as client:
        yield client


async def create(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/api/plans", json=CONTEXT | overrides)
    assert response.status_code == 201, response.text
    plan: dict[str, Any] = response.json()
    return plan


def amounts(plan: dict[str, Any]) -> dict[str, str]:
    return {line["category"]: line["amount"] for line in plan["budget"]}


# health and headers


async def test_healthz_and_security_headers(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.json() == {"status": "ok"}
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" not in response.headers  # dev mode
    assert "access-control-allow-origin" not in response.headers


async def test_security_headers_on_errors_too(client: AsyncClient) -> None:
    response = await client.get("/api/plans/" + "0" * 32)
    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"


# intake


async def test_intake_extracts_fields(client: AsyncClient) -> None:
    response = await client.post("/api/plans/intake", json={"text": DEMO_TEXT})
    assert response.status_code == 200
    body = response.json()
    assert body["fields"]["guest_count"] == 40
    assert body["fields"]["event_date"] == "2026-12-02"  # six weeks after TODAY
    assert body["missing_fields"] == ["formality"]
    assert body["degraded"] is False


@pytest.mark.parametrize(
    "payload", [{"text": ""}, {"text": "x" * 2001}, {}, {"text": "hi", "admin": True}]
)
async def test_intake_validates_input(client: AsyncClient, payload: dict[str, Any]) -> None:
    assert (await client.post("/api/plans/intake", json=payload)).status_code == 422


async def test_intake_degrades_when_llm_fails(tmp_path: Path) -> None:
    async with make_client(tmp_path, FailingProvider()) as client:
        body = (await client.post("/api/plans/intake", json={"text": DEMO_TEXT})).json()
    assert body["degraded"] is True and body["fields"] == {}
    assert "guest_count" in body["missing_fields"]


# create and read


async def test_create_plan_then_get_it(client: AsyncClient) -> None:
    plan = await create(client, dietary=["jain"])
    assert len(plan["id"]) == 32
    assert sum(float(a) for a in amounts(plan).values()) == 150000
    assert plan["timeline"] and plan["vendors"] and plan["ideas"]

    fetched = await client.get(f"/api/plans/{plan['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == plan


@pytest.mark.parametrize(
    "overrides",
    [
        {"guest_count": 0},
        {"total_budget": "-5"},
        {"event_type": "conference"},
        {"event_date": "not-a-date"},
        {"currency": "rupees"},
        {"preferences": ["x" * 201]},
        {"surprise": True},
    ],
)
async def test_create_plan_validates_context(
    client: AsyncClient, overrides: dict[str, Any]
) -> None:
    response = await client.post("/api/plans", json=CONTEXT | overrides)
    assert response.status_code == 422


async def test_create_plan_rejects_past_event(client: AsyncClient) -> None:
    response = await client.post("/api/plans", json=CONTEXT | {"event_date": "2026-01-01"})
    assert response.status_code == 422
    assert response.json()["detail"] == "The event date has passed."


async def test_create_plan_without_llm_is_engine_only(tmp_path: Path) -> None:
    async with make_client(tmp_path, FailingProvider()) as client:
        plan = await create(client)
    assert plan["ideas"] == []
    assert "LLM_UNAVAILABLE" in [a["code"] for a in plan["advisories"]]


@pytest.mark.parametrize(("plan_id", "status"), [("0" * 32, 404), ("not-an-id", 422)])
async def test_get_unknown_or_malformed_id(client: AsyncClient, plan_id: str, status: int) -> None:
    assert (await client.get(f"/api/plans/{plan_id}")).status_code == status


# patch


async def patch(client: AsyncClient, plan_id: str, body: dict[str, Any]) -> Response:
    return await client.patch(f"/api/plans/{plan_id}", json=body)


async def test_patch_locks_line_rebalances_and_persists(client: AsyncClient) -> None:
    plan = await create(client)
    body = {"budget": [{"category": "venue", "is_locked": True, "amount": "45000"}]}
    response = await patch(client, plan["id"], body)
    assert response.status_code == 200
    updated = response.json()
    assert amounts(updated)["venue"] == "45000"
    assert sum(float(a) for a in amounts(updated).values()) == 150000
    assert float(amounts(updated)["catering"]) < float(amounts(plan)["catering"])
    assert (await client.get(f"/api/plans/{plan['id']}")).json() == updated


async def test_patch_marks_milestone_done_and_vendor_booked(client: AsyncClient) -> None:
    plan = await create(client)
    body = {
        "milestones": [{"title": "Book venue", "status": "done"}],
        "vendors": [{"category": "venue", "status": "booked"}],
    }
    updated = (await patch(client, plan["id"], body)).json()
    assert {m["title"]: m["status"] for m in updated["timeline"]}["Book venue"] == "done"
    assert {v["category"]: v["status"] for v in updated["vendors"]}["venue"] == "booked"


@pytest.mark.parametrize(
    "body",
    [
        {"budget": [{"category": "fireworks", "is_locked": True}]},
        {"budget": [{"category": "venue", "is_locked": True, "amount": "999999"}]},
        {"budget": [{"category": "venue", "is_locked": False, "amount": "10"}]},
        {"budget": [{"category": "venue", "is_locked": True, "amount": "-1"}]},
        {"milestones": [{"title": "Book venue", "status": "late"}]},
        {"vendors": [{"category": "dragon", "status": "booked"}]},
        {"budget": "venue"},
        {"id": "hijack"},
    ],
)
async def test_patch_rejects_invalid_edits(client: AsyncClient, body: dict[str, Any]) -> None:
    plan = await create(client)
    assert (await patch(client, plan["id"], body)).status_code == 422
    assert (await client.get(f"/api/plans/{plan['id']}")).json() == plan  # nothing saved


async def test_patch_unknown_plan(client: AsyncClient) -> None:
    assert (await patch(client, "0" * 32, {})).status_code == 404


# regenerate ideas and chat


async def test_regenerate_ideas_recovers_from_llm_failure(tmp_path: Path) -> None:
    async with make_client(tmp_path, FailingProvider()) as client:
        plan = await create(client)
    async with make_client(tmp_path) as client:  # same database, working LLM
        response = await client.post(f"/api/plans/{plan['id']}/regenerate-ideas")
    assert response.status_code == 200
    regenerated = response.json()
    assert regenerated["ideas"]
    assert "LLM_UNAVAILABLE" not in [a["code"] for a in regenerated["advisories"]]


async def test_regenerate_ideas_unknown_plan(client: AsyncClient) -> None:
    assert (await client.post(f"/api/plans/{'0' * 32}/regenerate-ideas")).status_code == 404


async def test_chat_answers_from_engine_facts(client: AsyncClient) -> None:
    plan = await create(client)
    response = await client.post(
        f"/api/plans/{plan['id']}/chat", json={"question": "Can I afford a live band?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False
    assert body["facts"]["asks"][0]["ask"] == "live band"
    assert "live band" in body["answer"]


@pytest.mark.parametrize("payload", [{"question": ""}, {"question": "x" * 2001}, {}])
async def test_chat_validates_input(client: AsyncClient, payload: dict[str, Any]) -> None:
    plan = await create(client)
    assert (await client.post(f"/api/plans/{plan['id']}/chat", json=payload)).status_code == 422


async def test_chat_unknown_plan(client: AsyncClient) -> None:
    response = await client.post(f"/api/plans/{'0' * 32}/chat", json={"question": "hi"})
    assert response.status_code == 404


async def test_chat_degrades_when_llm_fails(tmp_path: Path) -> None:
    async with make_client(tmp_path, FailingProvider("answer_question")) as client:
        plan = await create(client)
        body = (await client.post(f"/api/plans/{plan['id']}/chat", json={"question": "?"})).json()
    assert body["degraded"] is True
    assert body["answer"].startswith("The assistant is unavailable")


# rate limiting


async def test_llm_routes_are_rate_limited(tmp_path: Path) -> None:
    async with make_client(tmp_path, per_minute=2) as client:
        plan = await create(client)  # uses 1 token
        ok = await client.post(f"/api/plans/{plan['id']}/chat", json={"question": "hi"})
        limited = await client.post("/api/plans/intake", json={"text": DEMO_TEXT})
        unlimited = await client.get(f"/api/plans/{plan['id']}")
    assert ok.status_code == 200
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.headers["x-content-type-options"] == "nosniff"
    assert unlimited.status_code == 200  # reads and edits are not rate limited
