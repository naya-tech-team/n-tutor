"""Tests for the HR Skills API.  Run:  uv run pytest -q

Every test gets its own HRStore through `dependency_overrides`, so no test can
see another test's shortlist. That is the practical payoff of injecting the store
with `Depends()` instead of reaching for a module-level global.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import HRStore, get_store


@pytest.fixture
def client():
    # Build the store ONCE per test, then hand the same instance to every request.
    # `lambda: HRStore()` would look right and be wrong: the provider is called per
    # request, so each call would get a brand-new store and nothing would persist
    # between two requests in the same test.
    store = HRStore()
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_timing_middleware_runs_on_every_request(client):
    assert "X-Process-Time-ms" in client.get("/health").headers


# --- employees ------------------------------------------------------------
def test_list_employees(client):
    ids = [e["employee_id"] for e in client.get("/employees").json()]
    assert ids == ["E1002", "E1003", "E1005", "E1007"]


def test_available_only_filters_out_allocated_people(client):
    ids = [e["employee_id"] for e in client.get("/employees", params={"available_only": True}).json()]
    assert "E1007" not in ids          # Arjun Nair is allocated to a project


def test_get_employee_is_case_insensitive(client):
    assert client.get("/employees/e1002").json()["name"] == "Priya Raman"


def test_unknown_employee_is_404(client):
    assert client.get("/employees/E9999").status_code == 404


def test_create_employee_assigns_an_id(client):
    payload = {"name": "New Person", "location": "Pune", "experience_years": 2.0,
               "skills": [{"skill": "SQL", "level": 4}]}
    response = client.post("/employees", json=payload)
    assert response.status_code == 201
    assert response.json()["employee_id"].startswith("E")


@pytest.mark.parametrize("level", [0, 6, -1])
def test_skill_level_is_validated_at_the_edge(client, level):
    """No route checks this — the model does, so every route gets it for free."""
    payload = {"name": "X", "location": "Pune", "experience_years": 1,
               "skills": [{"skill": "SQL", "level": level}]}
    assert client.post("/employees", json=payload).status_code == 422


# --- matching -------------------------------------------------------------
def test_perfect_candidate_scores_100(client):
    match = client.get("/requisitions/J2001/candidates/E1002").json()
    assert match["score"] == 100
    assert match["verdict"] == "strong"
    assert match["blockers"] == []


def test_missing_mandatory_skill_blocks_regardless_of_score(client):
    match = client.get("/requisitions/J2001/candidates/E1005").json()
    assert set(match["blockers"]) == {"Python", "SQL"}
    assert match["verdict"] == "blocked"


def test_candidates_are_ranked_best_first(client):
    scores = [m["score"] for m in client.get("/requisitions/J2001/candidates").json()]
    assert scores == sorted(scores, reverse=True)


def test_candidates_limit_is_bounded(client):
    assert client.get("/requisitions/J2001/candidates", params={"limit": 0}).status_code == 422
    assert client.get("/requisitions/J2001/candidates", params={"limit": 99}).status_code == 422


def test_unknown_requisition_is_404(client):
    assert client.get("/requisitions/J9999/candidates").status_code == 404


# --- shortlist ------------------------------------------------------------
def test_shortlist_a_viable_candidate(client):
    response = client.post("/requisitions/J2001/shortlist", json={"employee_id": "E1002"})
    assert response.status_code == 201
    assert response.json()["name"] == "Priya Raman"


def test_shortlisting_is_idempotent(client):
    for _ in range(2):
        client.post("/requisitions/J2001/shortlist", json={"employee_id": "E1002"})
    assert len(client.get("/requisitions/J2001/shortlist").json()) == 1


def test_blocked_candidate_is_409_not_400(client):
    """The request is well-formed; it conflicts with the state of the requisition."""
    response = client.post("/requisitions/J2001/shortlist", json={"employee_id": "E1005"})
    assert response.status_code == 409
    assert "missing mandatory" in response.json()["detail"]


def test_each_test_gets_a_clean_store(client):
    """Proves the fixture works — the shortlist from the test above is not here."""
    assert client.get("/requisitions/J2001/shortlist").json() == []
