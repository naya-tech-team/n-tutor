"""The one domain every lesson in this course runs on: **skills matching**.

Three tables and one scoring function. That is the whole world:

    SKILLS     a controlled vocabulary — "pyspark" and "Spark" are the same skill
    EMPLOYEES  people, each with rated skills (level 1-5) and years of practice
    JOBS       open requisitions, each with *required* skills, a minimum level,
               and a weight saying how much that skill matters

    match(employee, job) -> a score, the skills that matched, the ones that did not

Everything is in-memory and deterministic. No database, no network, no API key —
so a lesson about *hooks* stays a lesson about hooks instead of a lesson about
connection strings. Swap these dicts for real repository calls and every example
in the course keeps working unchanged.

    from _shared import EMPLOYEES, JOBS, get_employee, get_job, match
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 1. The skill catalog — a controlled vocabulary with aliases.
#
# Why this exists: a hiring manager writes "pyspark", the HRMS stores "Apache
# Spark", a CV says "Spark SQL". Without canonicalisation every match is a miss.
# ---------------------------------------------------------------------------

SKILLS: list[dict[str, Any]] = [
    # Programming
    {"skill": "Python", "category": "Programming", "aliases": ["py", "python3"]},
    {"skill": "Java", "category": "Programming", "aliases": ["core java", "java se"]},
    {"skill": "Scala", "category": "Programming", "aliases": []},
    {"skill": "SQL", "category": "Programming", "aliases": ["ansi sql", "t-sql", "plsql"]},
    {"skill": "TypeScript", "category": "Programming", "aliases": ["ts"]},
    # Data engineering
    {"skill": "Apache Spark", "category": "Data Engineering", "aliases": ["spark", "pyspark", "spark sql"]},
    {"skill": "Apache Kafka", "category": "Data Engineering", "aliases": ["kafka", "msk"]},
    {"skill": "Apache Airflow", "category": "Data Engineering", "aliases": ["airflow", "mwaa"]},
    {"skill": "dbt", "category": "Data Engineering", "aliases": ["data build tool"]},
    {"skill": "Snowflake", "category": "Data Engineering", "aliases": []},
    {"skill": "Databricks", "category": "Data Engineering", "aliases": []},
    {"skill": "Data Modeling", "category": "Data Engineering", "aliases": ["dimensional modeling", "star schema"]},
    # ML
    {"skill": "Machine Learning", "category": "ML", "aliases": ["ml", "classical ml"]},
    {"skill": "PyTorch", "category": "ML", "aliases": ["torch"]},
    {"skill": "MLOps", "category": "ML", "aliases": ["ml ops", "model ops"]},
    # Cloud & platform
    {"skill": "AWS", "category": "Cloud", "aliases": ["amazon web services", "aws cloud"]},
    {"skill": "Terraform", "category": "Cloud", "aliases": ["tf", "iac"]},
    {"skill": "Kubernetes", "category": "Cloud", "aliases": ["k8s", "eks"]},
    {"skill": "Docker", "category": "Cloud", "aliases": ["containers"]},
    {"skill": "CI/CD", "category": "Cloud", "aliases": ["jenkins", "github actions", "pipelines"]},
    # Application
    {"skill": "REST API", "category": "Application", "aliases": ["rest", "api design"]},
    {"skill": "React", "category": "Application", "aliases": ["reactjs"]},
    # Human skills — deliberately in the same table. A requisition that ignores
    # them produces technically-perfect, practically-useless shortlists.
    {"skill": "Stakeholder Management", "category": "Human", "aliases": ["stakeholder mgmt"]},
    {"skill": "Mentoring", "category": "Human", "aliases": ["coaching"]},
]

# skill name (lowercased) or alias -> canonical skill name
_ALIAS_INDEX: dict[str, str] = {}
for _entry in SKILLS:
    _ALIAS_INDEX[_entry["skill"].lower()] = _entry["skill"]
    for _alias in _entry["aliases"]:
        _ALIAS_INDEX[_alias.lower()] = _entry["skill"]


# ---------------------------------------------------------------------------
# 2. Employees. `level` is the 1-5 proficiency the last review agreed on;
#    `years` is how long they have actually practised it.
# ---------------------------------------------------------------------------

EMPLOYEES: list[dict[str, Any]] = [
    {
        "employee_id": "E1001",
        "name": "Anjali Deshpande",
        "email": "anjali.deshpande@example.com",
        "designation": "Director, Data & Analytics",
        "department": "Data & Analytics",
        "location": "Bengaluru",
        "availability": "allocated",
        "bench_since": None,
        "experience_years": 17.0,
        "skills": [
            {"skill": "Data Modeling", "level": 5, "years": 14},
            {"skill": "Stakeholder Management", "level": 5, "years": 12},
            {"skill": "Snowflake", "level": 4, "years": 5},
            {"skill": "SQL", "level": 5, "years": 16},
        ],
    },
    {
        "employee_id": "E1002",
        "name": "Priya Raman",
        "email": "priya.raman@example.com",
        "designation": "Senior Data Engineer",
        "department": "Data & Analytics",
        "location": "Bengaluru",
        "availability": "bench",
        "bench_since": "2026-05-18",
        "experience_years": 8.5,
        "skills": [
            {"skill": "Python", "level": 4, "years": 7},
            {"skill": "Apache Spark", "level": 5, "years": 6},
            {"skill": "Apache Airflow", "level": 4, "years": 4},
            {"skill": "SQL", "level": 5, "years": 8},
            {"skill": "Databricks", "level": 4, "years": 3},
            {"skill": "AWS", "level": 3, "years": 4},
        ],
    },
    {
        "employee_id": "E1003",
        "name": "Rahul Menon",
        "email": "rahul.menon@example.com",
        "designation": "Data Engineer",
        "department": "Data & Analytics",
        "location": "Hyderabad",
        "availability": "bench",
        "bench_since": "2026-06-30",
        "experience_years": 5.0,
        "skills": [
            {"skill": "Python", "level": 4, "years": 5},
            {"skill": "Apache Spark", "level": 3, "years": 3},
            {"skill": "SQL", "level": 4, "years": 5},
            {"skill": "dbt", "level": 3, "years": 2},
            {"skill": "Snowflake", "level": 3, "years": 2},
        ],
    },
    {
        "employee_id": "E1004",
        "name": "Meera Krishnan",
        "email": "meera.krishnan@example.com",
        "designation": "Principal Engineer, Streaming",
        "department": "Data & Analytics",
        "location": "Chennai",
        "availability": "allocated",
        "bench_since": None,
        "experience_years": 12.0,
        "skills": [
            {"skill": "Apache Kafka", "level": 5, "years": 8},
            {"skill": "Apache Spark", "level": 4, "years": 7},
            {"skill": "Scala", "level": 4, "years": 6},
            {"skill": "Java", "level": 4, "years": 10},
            {"skill": "Mentoring", "level": 4, "years": 5},
        ],
    },
    {
        "employee_id": "E1005",
        "name": "Vikram Iyer",
        "email": "vikram.iyer@example.com",
        "designation": "Streaming Engineer",
        "department": "Data & Analytics",
        "location": "Chennai",
        "availability": "bench",
        "bench_since": "2026-07-02",
        "experience_years": 7.5,
        "skills": [
            {"skill": "Apache Kafka", "level": 4, "years": 5},
            {"skill": "Apache Spark", "level": 4, "years": 5},
            {"skill": "Java", "level": 3, "years": 7},
            {"skill": "AWS", "level": 3, "years": 4},
            {"skill": "Python", "level": 3, "years": 3},
        ],
    },
    {
        "employee_id": "E1006",
        "name": "Sneha Kapoor",
        "email": "sneha.kapoor@example.com",
        "designation": "Analytics Engineer",
        "department": "Data & Analytics",
        "location": "Pune",
        "availability": "bench",
        "bench_since": "2026-06-11",
        "experience_years": 4.0,
        "skills": [
            {"skill": "SQL", "level": 4, "years": 4},
            {"skill": "dbt", "level": 4, "years": 3},
            {"skill": "Snowflake", "level": 3, "years": 3},
            {"skill": "Data Modeling", "level": 3, "years": 2},
            {"skill": "Python", "level": 3, "years": 3},
        ],
    },
    {
        "employee_id": "E1007",
        "name": "Arjun Nair",
        "email": "arjun.nair@example.com",
        "designation": "ML Engineer",
        "department": "AI & ML",
        "location": "Bengaluru",
        "availability": "allocated",
        "bench_since": None,
        "experience_years": 6.0,
        "skills": [
            {"skill": "Python", "level": 5, "years": 6},
            {"skill": "Machine Learning", "level": 4, "years": 5},
            {"skill": "PyTorch", "level": 4, "years": 3},
            {"skill": "MLOps", "level": 3, "years": 2},
            {"skill": "AWS", "level": 3, "years": 3},
        ],
    },
    {
        "employee_id": "E1008",
        "name": "Fatima Sheikh",
        "email": "fatima.sheikh@example.com",
        "designation": "Cloud Platform Engineer",
        "department": "Platform",
        "location": "Hyderabad",
        "availability": "bench",
        "bench_since": "2026-07-21",
        "experience_years": 6.5,
        "skills": [
            {"skill": "AWS", "level": 5, "years": 6},
            {"skill": "Terraform", "level": 4, "years": 4},
            {"skill": "Kubernetes", "level": 4, "years": 4},
            {"skill": "Docker", "level": 4, "years": 5},
            {"skill": "CI/CD", "level": 4, "years": 5},
            {"skill": "Python", "level": 3, "years": 4},
        ],
    },
    {
        "employee_id": "E1009",
        "name": "Karthik Subramanian",
        "email": "karthik.s@example.com",
        "designation": "Backend Engineer",
        "department": "Payments",
        "location": "Chennai",
        "availability": "bench",
        "bench_since": "2026-06-02",
        "experience_years": 5.5,
        "skills": [
            {"skill": "Java", "level": 4, "years": 5},
            {"skill": "SQL", "level": 4, "years": 5},
            {"skill": "REST API", "level": 4, "years": 5},
            {"skill": "Apache Kafka", "level": 3, "years": 2},
            {"skill": "Kubernetes", "level": 2, "years": 1},
        ],
    },
    {
        "employee_id": "E1010",
        "name": "Divya Pillai",
        "email": "divya.pillai@example.com",
        "designation": "Data Analyst",
        "department": "Data & Analytics",
        "location": "Kochi",
        "availability": "bench",
        "bench_since": "2026-05-05",
        "experience_years": 3.0,
        "skills": [
            {"skill": "SQL", "level": 4, "years": 3},
            {"skill": "Data Modeling", "level": 2, "years": 1},
            {"skill": "Python", "level": 2, "years": 2},
            # No dbt at all — she is the "one course away" candidate the
            # gap-analysis examples are built around.
        ],
    },
    {
        "employee_id": "E1011",
        "name": "Rohan Gupta",
        "email": "rohan.gupta@example.com",
        "designation": "SRE",
        "department": "Platform",
        "location": "Pune",
        "availability": "allocated",
        "bench_since": None,
        "experience_years": 9.0,
        "skills": [
            {"skill": "Kubernetes", "level": 5, "years": 6},
            {"skill": "Terraform", "level": 4, "years": 5},
            {"skill": "AWS", "level": 4, "years": 7},
            {"skill": "CI/CD", "level": 5, "years": 8},
            {"skill": "Docker", "level": 4, "years": 6},
        ],
    },
    {
        "employee_id": "E1012",
        "name": "Aisha Khan",
        "email": "aisha.khan@example.com",
        "designation": "Full-stack Engineer",
        "department": "Digital",
        "location": "Bengaluru",
        "availability": "bench",
        "bench_since": "2026-07-15",
        "experience_years": 4.5,
        "skills": [
            {"skill": "TypeScript", "level": 4, "years": 4},
            {"skill": "React", "level": 4, "years": 4},
            {"skill": "REST API", "level": 3, "years": 3},
            {"skill": "Python", "level": 3, "years": 2},
        ],
    },
]


# ---------------------------------------------------------------------------
# 3. Jobs. `mandatory` decides whether a gap is a blocker or a negotiation;
#    `weight` decides how much of the score the skill is worth.
# ---------------------------------------------------------------------------

JOBS: list[dict[str, Any]] = [
    {
        "job_id": "J2001",
        "title": "Senior Data Engineer",
        "department": "Data & Analytics",
        "location": "Bengaluru",
        "status": "open",
        "openings": 2,
        "min_experience_years": 6,
        "description": "Own batch and streaming pipelines on the lakehouse: quality, orchestration, cost.",
        "required_skills": [
            {"skill": "Python", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "Apache Spark", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "SQL", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "Apache Airflow", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "Databricks", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "AWS", "min_level": 3, "mandatory": False, "weight": 1},
        ],
    },
    {
        "job_id": "J2002",
        "title": "Streaming Platform Engineer",
        "department": "Data & Analytics",
        "location": "Chennai",
        "status": "open",
        "openings": 1,
        "min_experience_years": 7,
        "description": "Design event-driven data products on Kafka: topic design, exactly-once, Spark consumers.",
        "required_skills": [
            {"skill": "Apache Kafka", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "Apache Spark", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "Scala", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "Java", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "AWS", "min_level": 3, "mandatory": False, "weight": 1},
        ],
    },
    {
        "job_id": "J2003",
        "title": "Analytics Engineer",
        "department": "Data & Analytics",
        "location": "Pune",
        "status": "open",
        "openings": 2,
        "min_experience_years": 3,
        "description": "Model the warehouse in dbt. Own semantics, tests and documentation for the retail domain.",
        "required_skills": [
            {"skill": "SQL", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "dbt", "min_level": 3, "mandatory": True, "weight": 2},
            {"skill": "Snowflake", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "Data Modeling", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "Python", "min_level": 2, "mandatory": False, "weight": 1},
        ],
    },
    {
        "job_id": "J2004",
        "title": "ML Engineer",
        "department": "AI & ML",
        "location": "Bengaluru",
        "status": "open",
        "openings": 1,
        "min_experience_years": 5,
        "description": "Take models from notebook to production: training pipelines, serving, monitoring.",
        "required_skills": [
            {"skill": "Python", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "Machine Learning", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "PyTorch", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "MLOps", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "AWS", "min_level": 3, "mandatory": False, "weight": 1},
        ],
    },
    {
        "job_id": "J2005",
        "title": "Cloud Platform Engineer",
        "department": "Platform",
        "location": "Hyderabad",
        "status": "open",
        "openings": 1,
        "min_experience_years": 5,
        "description": "Run the landing zone: Terraform modules, EKS, golden pipelines for product teams.",
        "required_skills": [
            {"skill": "AWS", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "Terraform", "min_level": 3, "mandatory": True, "weight": 2},
            {"skill": "Kubernetes", "min_level": 3, "mandatory": True, "weight": 2},
            {"skill": "Docker", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "CI/CD", "min_level": 3, "mandatory": False, "weight": 1},
        ],
    },
    {
        "job_id": "J2006",
        "title": "Backend Engineer, Payments",
        "department": "Payments",
        "location": "Chennai",
        "status": "open",
        "openings": 3,
        "min_experience_years": 4,
        "description": "Build idempotent payment APIs and the event stream behind refunds and settlements.",
        "required_skills": [
            {"skill": "Java", "min_level": 4, "mandatory": True, "weight": 2},
            {"skill": "SQL", "min_level": 3, "mandatory": True, "weight": 2},
            {"skill": "REST API", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "Apache Kafka", "min_level": 3, "mandatory": False, "weight": 1},
            {"skill": "Kubernetes", "min_level": 3, "mandatory": False, "weight": 1},
        ],
    },
]


# ---------------------------------------------------------------------------
# 4. Lookups. Every tool in this course is a thin wrapper over one of these.
# ---------------------------------------------------------------------------


def canonical_skill(name: str) -> str | None:
    """Resolve a free-text skill name to its catalog entry. "pyspark" -> "Apache Spark"."""
    return _ALIAS_INDEX.get(name.strip().lower())


def get_employee(employee_id: str) -> dict[str, Any] | None:
    """One employee by id, or None."""
    return next((e for e in EMPLOYEES if e["employee_id"].upper() == employee_id.strip().upper()), None)


def find_employee_by_name(name: str) -> dict[str, Any] | None:
    """First employee whose name contains `name`, case-insensitively."""
    needle = name.strip().lower()
    return next((e for e in EMPLOYEES if needle in e["name"].lower()), None)


def get_job(job_id: str) -> dict[str, Any] | None:
    """One job by id, or None."""
    return next((j for j in JOBS if j["job_id"].upper() == job_id.strip().upper()), None)


def skill_level(employee: dict[str, Any], skill: str) -> int:
    """Proficiency 1-5 for one skill, or 0 if the employee does not have it."""
    canon = canonical_skill(skill) or skill
    entry = next((s for s in employee["skills"] if s["skill"].lower() == canon.lower()), None)
    return entry["level"] if entry else 0


def employees_with_skill(skill: str, min_level: int = 1, available_only: bool = False) -> list[dict[str, Any]]:
    """Everyone at or above `min_level` in a skill, best first."""
    canon = canonical_skill(skill)
    if canon is None:
        return []
    hits = [
        e
        for e in EMPLOYEES
        if skill_level(e, canon) >= min_level and (not available_only or e["availability"] == "bench")
    ]
    return sorted(hits, key=lambda e: skill_level(e, canon), reverse=True)


# ---------------------------------------------------------------------------
# 5. The scoring function. The single piece of business logic in the course.
#
# Deliberately boring arithmetic, not a model call: a match score you cannot
# reproduce by hand is a match score nobody will trust in a hiring review.
# ---------------------------------------------------------------------------


def match(employee: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """Score one employee against one job.

    Each required skill earns its full weight when the employee is at or above
    `min_level`, and a pro-rata share when they are below it. A mandatory skill
    below its bar is a *blocker*: the score still reports, but the verdict does
    not, because "82% but cannot do the mandatory thing" is not a shortlist.

    Returns a JSON-safe dict — tools can hand it straight back to the model.
    """
    earned = 0.0
    total = 0.0
    matched: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    blockers: list[str] = []

    for req in job["required_skills"]:
        weight = req["weight"]
        total += weight
        have = skill_level(employee, req["skill"])

        if have >= req["min_level"]:
            earned += weight
            matched.append({"skill": req["skill"], "required": req["min_level"], "actual": have})
            continue

        # Partial credit: someone at level 3 against a level-4 bar is a coaching
        # problem, someone at 0 is a hiring problem. The score should know.
        earned += weight * (have / req["min_level"])
        gap = {
            "skill": req["skill"],
            "required": req["min_level"],
            "actual": have,
            "mandatory": req["mandatory"],
        }
        gaps.append(gap)
        if req["mandatory"]:
            blockers.append(req["skill"])

    score = round(100 * earned / total) if total else 0
    meets_experience = employee["experience_years"] >= job["min_experience_years"]

    if blockers:
        verdict = "blocked"
    elif score >= 80 and meets_experience:
        verdict = "strong"
    elif score >= 55:
        verdict = "possible"
    else:
        verdict = "weak"

    return {
        "employee_id": employee["employee_id"],
        "name": employee["name"],
        "job_id": job["job_id"],
        "title": job["title"],
        "score": score,
        "verdict": verdict,
        "matched_skills": matched,
        "gaps": gaps,
        "blockers": blockers,
        "meets_experience": meets_experience,
        "same_location": employee["location"] == job["location"],
        "availability": employee["availability"],
    }


def rank_candidates(job_id: str, available_only: bool = True, limit: int = 5) -> list[dict[str, Any]]:
    """Score every employee against a job and return the best `limit`, best first."""
    job = get_job(job_id)
    if job is None:
        return []
    pool = [e for e in EMPLOYEES if not available_only or e["availability"] == "bench"]
    ranked = sorted((match(e, job) for e in pool), key=lambda m: m["score"], reverse=True)
    return ranked[:limit]


def rank_jobs_for_employee(employee_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """The mirror image: which open roles suit this person best?"""
    employee = get_employee(employee_id)
    if employee is None:
        return []
    ranked = sorted((match(employee, j) for j in JOBS), key=lambda m: m["score"], reverse=True)
    return ranked[:limit]


def summarize_match(result: dict[str, Any]) -> str:
    """One human-readable line. Used wherever a lesson needs compact output."""
    gaps = ", ".join(f"{g['skill']} {g['actual']}/{g['required']}" for g in result["gaps"]) or "none"
    return f"{result['name']:<22} {result['score']:>3}%  {result['verdict']:<8} gaps: {gaps}"
