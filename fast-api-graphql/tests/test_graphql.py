"""Tests for the HR Skills GraphQL API.  Run:  uv run pytest -q

Every test gets its own HRStore through `dependency_overrides`, so no test can
see another test's shortlist. The store reaches resolvers via the GraphQL
*context*, but the context is built by a FastAPI dependency — so the override
trick that worked for REST routes works here unchanged.

The recurring assertion to notice: `response.status_code == 200`. A GraphQL
server answers 200 for a blocked candidate, a missing requisition and a rejected
skill level alike. What happened is in the body, not the status line.
"""

import pytest
from fastapi.testclient import TestClient

from app.graph.schema import schema
from app.main import app
from app.store import HRStore, get_store


@pytest.fixture
def store():
    return HRStore()


@pytest.fixture
def client(store):
    # Build the store ONCE per test, then hand the same instance to every request.
    # `lambda: HRStore()` would look right and be wrong: the provider is called per
    # request, so each call would get a brand-new store and nothing would persist
    # between two requests in the same test.
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def gql(client, query, **variables):
    """POST one operation and return the parsed body — errors included, not raised."""
    response = client.post("/graphql", json={"query": query, "variables": variables})
    assert response.status_code == 200, response.text
    return response.json()


def data(client, query, **variables):
    """Same, but for queries that must succeed outright."""
    body = gql(client, query, **variables)
    assert "errors" not in body, body["errors"]
    return body["data"]


SHORTLIST = """
mutation Shortlist($jobId: ID!, $employeeId: ID!) {
  shortlistCandidate(jobId: $jobId, employeeId: $employeeId) {
    __typename
    ... on ShortlistEntry { employeeId name score verdict }
    ... on CandidateBlocked { message blockers score }
    ... on NotFound { message kind id }
  }
}
"""

ADD_EMPLOYEE = """
mutation Add($employee: EmployeeInput!) {
  addEmployee(employee: $employee) {
    __typename
    ... on Employee { employeeId name }
    ... on ValidationFailed { message invalidFields { field message } }
  }
}
"""


# --- the transport --------------------------------------------------------
def test_health_is_still_plain_http(client):
    """A load balancer should not have to speak GraphQL to check liveness."""
    assert client.get("/health").json() == {"status": "ok"}


def test_health_through_graphql(client):
    assert data(client, "{ health }") == {"health": "ok"}


def test_timing_middleware_runs_on_the_graphql_endpoint(client):
    response = client.post("/graphql", json={"query": "{ health }"})
    assert "X-Process-Time-ms" in response.headers


def test_graphiql_ide_is_served_to_a_browser(client):
    response = client.get("/graphql", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "graphiql" in response.text.lower()


def test_a_query_can_also_arrive_as_a_get(client):
    response = client.get("/graphql", params={"query": "{ health }"},
                          headers={"Accept": "application/json"})
    assert response.json()["data"] == {"health": "ok"}


def test_the_client_chooses_the_fields(client):
    """The point of GraphQL: no over-fetching, and the server did not decide this."""
    people = data(client, "{ employees { name } }")["employees"]
    assert list(people[0]) == ["name"]


def test_an_unknown_field_is_rejected_before_any_resolver_runs(client):
    body = gql(client, "{ employees { salary } }")
    assert body["data"] is None                      # nothing executed at all
    assert "Cannot query field 'salary'" in body["errors"][0]["message"]


# --- employees ------------------------------------------------------------
def test_list_employees(client):
    people = data(client, "{ employees { employeeId } }")["employees"]
    assert [p["employeeId"] for p in people] == ["E1002", "E1003", "E1005", "E1007"]


def test_available_only_filters_out_allocated_people(client):
    people = data(client, "{ employees(availableOnly: true) { employeeId } }")["employees"]
    assert "E1007" not in [p["employeeId"] for p in people]   # Arjun Nair is allocated


def test_get_employee_is_case_insensitive(client):
    person = data(client, '{ employee(employeeId: "e1002") { name } }')["employee"]
    assert person["name"] == "Priya Raman"


def test_unknown_employee_is_null_not_an_error(client):
    """"No such row" is an ordinary answer to a lookup — the rest of a query survives."""
    body = gql(client, '{ employee(employeeId: "E9999") { name } health }')
    assert body["data"] == {"employee": None, "health": "ok"}
    assert "errors" not in body


def test_add_employee_assigns_an_id(client):
    payload = {"name": "New Person", "location": "Pune", "experienceYears": 2.0,
               "skills": [{"skill": "SQL", "level": 4}]}
    result = data(client, ADD_EMPLOYEE, employee=payload)["addEmployee"]
    assert result["__typename"] == "Employee"
    assert result["employeeId"].startswith("E")


@pytest.mark.parametrize("level", [0, 6, -1])
def test_skill_level_is_validated_even_though_graphql_calls_it_an_int(client, level):
    """`level: Int!` promises a whole number, never a range. Pydantic still owns 1–5."""
    payload = {"name": "X", "location": "Pune", "experienceYears": 1,
               "skills": [{"skill": "SQL", "level": level}]}
    result = data(client, ADD_EMPLOYEE, employee=payload)["addEmployee"]
    assert result["__typename"] == "ValidationFailed"
    assert result["invalidFields"][0]["field"] == "skills.0.level"


def test_a_wrong_type_is_rejected_by_graphql_itself(client):
    """Contrast with the test above: this one never reaches Python."""
    payload = {"name": "X", "location": "Pune", "experienceYears": 1,
               "skills": [{"skill": "SQL", "level": "expert"}]}
    body = gql(client, ADD_EMPLOYEE, employee=payload)
    assert body["data"] is None
    assert "Int cannot represent" in body["errors"][0]["message"]


# --- matching -------------------------------------------------------------
def test_perfect_candidate_scores_100(client):
    """`match` is a field on Employee, so the score arrives in the same round trip."""
    person = data(client, """
      { employee(employeeId: "E1002") {
          name
          match(jobId: "J2001") { score verdict blockers }
      } }
    """)["employee"]
    assert person["match"] == {"score": 100, "verdict": "strong", "blockers": []}


def test_missing_mandatory_skill_blocks_regardless_of_score(client):
    match = data(client, """
      { employee(employeeId: "E1005") { match(jobId: "J2001") { verdict blockers } } }
    """)["employee"]["match"]
    assert set(match["blockers"]) == {"Python", "SQL"}
    assert match["verdict"] == "blocked"


def test_match_against_an_unknown_requisition_is_null(client):
    person = data(client, """
      { employee(employeeId: "E1002") { match(jobId: "J9999") { score } } }
    """)["employee"]
    assert person["match"] is None


def test_candidates_are_ranked_best_first(client):
    candidates = data(client, """
      { requisition(jobId: "J2001") { candidates { score } } }
    """)["requisition"]["candidates"]
    scores = [c["score"] for c in candidates]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("limit", [0, 99])
def test_candidates_limit_is_bounded_by_the_resolver(client, limit):
    """GraphQL types an argument; only the resolver can bound it."""
    body = gql(client, "query Rank($limit: Int!) { requisition(jobId: \"J2001\") "
                       "{ candidates(limit: $limit) { score } } }", limit=limit)
    assert "limit must be between 1 and 20" in body["errors"][0]["message"]
    assert body["errors"][0]["path"] == ["requisition", "candidates"]


def test_unknown_requisition_is_null(client):
    assert data(client, '{ requisition(jobId: "J9999") { title } }')["requisition"] is None


# --- shortlist ------------------------------------------------------------
def test_shortlist_a_viable_candidate(client):
    result = data(client, SHORTLIST, jobId="J2001", employeeId="E1002")["shortlistCandidate"]
    assert result["__typename"] == "ShortlistEntry"
    assert result["name"] == "Priya Raman"


def test_shortlisting_is_idempotent(client):
    for _ in range(2):
        data(client, SHORTLIST, jobId="J2001", employeeId="E1002")
    entries = data(client, '{ requisition(jobId: "J2001") { shortlist { employeeId } } }')
    assert len(entries["requisition"]["shortlist"]) == 1


def test_blocked_candidate_is_a_union_member_not_an_error(client):
    """The REST 409, moved into the schema: refusal is data the client must handle."""
    body = gql(client, SHORTLIST, jobId="J2001", employeeId="E1005")
    assert "errors" not in body
    result = body["data"]["shortlistCandidate"]
    assert result["__typename"] == "CandidateBlocked"
    assert "missing mandatory" in result["message"]
    assert set(result["blockers"]) == {"Python", "SQL"}


def test_shortlisting_for_an_unknown_requisition_reports_not_found(client):
    result = data(client, SHORTLIST, jobId="J9999", employeeId="E1002")["shortlistCandidate"]
    assert result == {"__typename": "NotFound", "message": "requisition not found",
                      "kind": "requisition", "id": "J9999"}


def test_each_test_gets_a_clean_store(client):
    """Proves the fixture works — the shortlist from the tests above is not here."""
    assert data(client, '{ requisition(jobId: "J2001") { shortlist { employeeId } } }') == {
        "requisition": {"shortlist": []}
    }


# --- the GraphQL-specific machinery ---------------------------------------
def test_dataloader_batches_employee_lookups(client, store):
    """Two shortlist entries, each asking for its employee: one trip to the store."""
    for employee_id in ("E1002", "E1007"):   # both clear every mandatory skill on J2001
        data(client, SHORTLIST, jobId="J2001", employeeId=employee_id)

    store.batch_calls = 0
    entries = data(client, """
      { requisition(jobId: "J2001") { shortlist { name employee { location } } } }
    """)["requisition"]["shortlist"]

    assert len(entries) == 2
    assert store.batch_calls == 1            # N+1 would make this 2


def test_one_round_trip_replaces_four_rest_calls(client):
    """Both open roles, their ranked candidates and their shortlists, in one POST."""
    result = data(client, """
      { requisitions {
          title
          candidates(limit: 2) { name score verdict }
          shortlist { name }
      } }
    """)["requisitions"]
    assert [r["title"] for r in result] == ["Senior Data Engineer", "Streaming Platform Engineer"]
    assert all(len(r["candidates"]) == 2 for r in result)


def test_the_schema_publishes_every_way_a_mutation_can_end(client):
    """A client cannot forget a case it can read in the SDL."""
    sdl = schema.as_str()
    assert "union ShortlistResult = ShortlistEntry | NotFound | CandidateBlocked" in sdl
    assert "union AddEmployeeResult = Employee | ValidationFailed" in sdl
