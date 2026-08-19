# AgentCore — runnable code

```bash
terraform init
```
```bash
terraform plan -var-file=dev.tfvars
```
```bash
terraform apply -var-file=dev.tfvars -auto-approve
```
```bash
terraform destroy -var-file=dev.tfvars -auto-approve
```

The hiring system from [`a2a-strands/`](../a2a-strands/README.md) and
[`mcp-server/`](../mcp-server/README.md), built for **Amazon Bedrock AgentCore**: five
runtimes, a Gateway over a Lambda over S3, Memory, Cognito and OpenTelemetry — and still
runnable end to end on your laptop with no AWS account.

The domain is the one the whole course uses: **employees have skills rated 1–5,
requisitions require skills at a minimum level, and some of those are mandatory.** A
candidate below a mandatory bar is *blocked* — no score saves them.

> **One codebase, two worlds.** `AGENTCORE`, `MODEL_PROVIDER` and `DATA_SOURCE` decide
> whether a file talks to Ollama and a Python list or to Bedrock and an S3 bucket.
> Nothing above `if __name__ == "__main__":` in any runtime knows which.

[`architecture.md`](architecture.md) is the design document — read it first if you want
the *why*. This file is how to run it.
[`terraform/README.md`](terraform/README.md) is the module-by-module reference: every
variable, data source, resource and output, and what depends on what.

## Table of contents

- [Layout](#layout)
- [Prerequisites](#prerequisites)
- [Run it locally](#run-it-locally)
- [Expected output](#expected-output)
- [The chat UI](#the-chat-ui)
- [Run the tests](#run-the-tests)
- [Deploy to AWS](#deploy-to-aws)
- [The two modes, concretely](#the-two-modes-concretely)
- [Troubleshooting](#troubleshooting)
- [Next steps](#next-steps)

## Layout

```
agent-core/
├── architecture.md              # the design document: diagrams, decisions, gotchas
├── app/
│   ├── _shared/
│   │   ├── config.py            # the dual-mode switch
│   │   ├── llm.py               # make_model(): Ollama | Bedrock — the one seam
│   │   ├── hr_data.py           # the domain. match() is arithmetic, never a model call
│   │   ├── store.py             # where records come from: a Python list, or S3
│   │   ├── a2a_serve.py         # A2AServer locally, serve_a2a in Runtime
│   │   └── tool_budget.py       # a per-invocation tool-call cap, as a hook
│   ├── runtimes/
│   │   ├── hiring_supervisor/   # HTTP · 8080 /invocations — the only front door
│   │   ├── talent_screening/    # A2A  · 9000 / — the only agent with tools
│   │   ├── recruiting_outreach/ # A2A  · 9000 / — no tools at all, by design
│   │   ├── people_compliance/   # A2A  · 9000 / — reviews the draft
│   │   └── hr_skills_mcp/       # MCP  · 8000 /mcp — the scoring engine
│   ├── lambda_fn/handler.py     # hr-data-fn: the estate, as MCP tools
│   └── clients/
│       ├── a2a_call.py          # one A2A round-trip, either address; SigV4 lives here
│       ├── raw_client.py        # the protocol with no AI on the calling side
│       └── tools.py             # direct MCP + gateway MCP, as one tool list
├── ui/                          # the chat UI
│   ├── src/
│   │   ├── App.jsx              # transcript, progress feed, the event reducer
│   │   ├── api.js               # SSE over fetch — EventSource cannot send a bearer token
│   │   ├── api.test.js          # the frame parser, against nasty chunk splits
│   │   └── Login.jsx            # the Cognito user from 03, no hosted UI
│   └── proxy/
│       ├── index.mjs            # Node, because Python Lambdas cannot stream
│       └── index.test.js        # REST vs function-URL event shapes
├── scripts/
│   ├── run_local.py             # start all four servers in one terminal
│   ├── ui_server.py             # the proxy's local twin — same wire, no AWS
│   ├── seed_s3.py               # hr_data.py -> the three S3 objects
│   ├── package.py               # arm64 deployment zips -> dist/
│   ├── probe_gateway.py         # the deployed Gateway, SigV4-signed, no agent
│   ├── gen_tfvars.py            # a dev.tfvars per module, keeping what you set
│   └── check_terraform_chain.py # the audit `terraform validate` cannot do
├── tests/                       # 93 python + 9 ui + 5 proxy, none of which touch AWS
└── terraform/01_… 08_           # one directory per capability, applied in order
```

## Prerequisites

Local only — no AWS account, no credentials, nothing leaves the machine:

```bash
ollama serve
ollama pull qwen2.5:7b     # see the note on models below
```

Managed with [uv](https://docs.astral.sh/uv/). Python 3.13 — uv installs it for you.

> **On the model.** This is tool-call heavy across five processes. `llama3.2` (3B) gets
> there but wanders. `qwen2.5:7b` runs it cleanly. Override per run:
> `OLLAMA_MODEL=qwen2.5:7b uv run scripts/run_local.py` — a real environment variable
> beats `.env`.

> **Python 3.13, not 3.12.** The rest of the repo pins `>=3.12`. The deployment artifacts
> target `PYTHON_3_13`, and building wheels on the same minor version the container runs
> avoids a class of import error you can only reproduce in AWS.

## Run it locally

```bash
cd agent-core
cp .env.example .env

# four servers in one terminal, then the pipeline, then exit
OLLAMA_MODEL=qwen2.5:7b uv run scripts/run_local.py --pipeline
```

Or take it apart. `run_local.py` with no flag starts the servers and idles, so you can
drive them by hand:

```bash
uv run scripts/run_local.py                         # terminal 1
uv run app/clients/raw_client.py                    # terminal 2 — protocol, no AI
uv run app/runtimes/hiring_supervisor/main.py J2002  # terminal 2 — a different req
```

### Or one process per terminal

The [`Makefile`](Makefile) is the same commands with the model and the TLS flag already
set. `make` on its own lists everything:

```bash
make mcp          # terminal 1 — :8000/mcp
make screening    # terminal 2 — :9001
make outreach     # terminal 3 — :9002
make compliance   # terminal 4 — :9007
make supervisor   # terminal 5 — the pipeline      (make supervisor JOB=J2002)

make probe        # one A2A round-trip, no model on the calling side
make ports        # what is listening
make down         # kill it — by port, because `pkill -f` misses `uv run` servers
```

Five terminals is how [`a2a-strands/`](../a2a-strands/README.md) teaches it, and it is
still the best way to watch a delegation land: run `make screening` in its own window and
`[screening] rank_for_requisition('J2001', limit=2)` appears there the instant the
supervisor delegates. `make up` collapses the four servers into one terminal once that
stops being interesting.

`raw_client.py` is the one to reach for when a delegation misbehaves: it reads a card and
sends one message with **no model on the calling side**, so whatever comes back came from
the remote agent alone.

## Expected output

`run_local.py --pipeline`, verbatim from a real run on `qwen2.5:7b`:

```text
Discovering agents:
  Talent Screening Agent at http://127.0.0.1:9001 — skills: candidate_screening
  Recruiting Outreach Agent at http://127.0.0.1:9002 — skills: candidate_outreach_note
  People Compliance Reviewer at http://127.0.0.1:9007 — skills: outreach_compliance_review

Asking: We need to fill J2001. Find the best candidate and draft a note to them.

Tool: ask_screening_agent({'job_id': 'J2001'})
  → delegating to Screening Agent: J2001

Tool: ask_outreach_agent({'screening_facts': 'J2001 Senior Data Engineer in Bengaluru\n\n
  E1002 Priya Raman — 100% strong, strengths: Python L4, Apache Spark L5, SQL L5, blockers: none\n
  E1003 Rahul Menon — 61% blocked, strengths: Python L4, SQL L4, blockers: Apache Spark'})
  → delegating to Outreach Agent

Tool: ask_compliance_reviewer({'employee_id': 'E1002', 'job_id': 'J2001', 'note': "Hi Priya, ..."})
  → delegating to Compliance Reviewer

[stop_reason: end_turn]

Hi Priya,

We're looking for a Senior Data Engineer in Bengaluru, and your strong skills in Python,
Apache Spark, and SQL caught our eye. Would you like to chat about this opportunity?

Verdict: APPROVED
```

The wording differs every run — three language models are generating prose. What stays
constant is the shape: **screen, then write, then review, and the note goes to Priya, not
to Rahul.**

Rahul scores 61% and is missing Apache Spark, which J2001 marks mandatory. That makes him
`blocked` — he cannot be shortlisted however good the rest looks. Five things have to hold
for the final note to be correct, and every one of them is a place this pipeline broke
while it was being written:

1. `match()` computes the blocker — arithmetic in `hr_skills_mcp`, not a model call.
2. The screener copies the **requisition line** as well as the candidates. Without it the
   writer has no role name and invents one — the first draft advertised a "Data Science"
   role for a Senior Data Engineer requisition.
3. The screener uses the verdict word exactly: `blocked` is never softened.
4. The supervisor passes the screening text through **verbatim**, not summarised.
5. The reviewer checks against the record — see the gotcha below.

## The chat UI

Everything above is a terminal. `ui/` is the same pipeline behind a chat box, and it runs
locally with no AWS at all — three terminals:

```bash
make up          # the four agent servers
make ui-api      # the chat proxy   :8123
make ui-dev      # vite             :5173   <- open this
```

Ask it anything: *"Find the best candidate for J2001 and draft a note to them"*, *"who is on
the bench in Bengaluru?"*. The supervisor now takes a free-form `prompt` as well as the
fixed `{"job_id": ...}` payload, so both callers keep working.

**Why it streams.** A full run is three remote delegations and a minute or two. The
supervisor's entrypoint is an `async def` with `yield` in it, which is the entire trick:
`bedrock_agentcore` sees an async generator and returns `text/event-stream` instead of a
JSON body. Each yielded dict arrives at the browser as one `data:` frame, and the UI turns
them into the progress feed:

```
  ✓ Screening Agent — ranking candidates          ×4
  ✓ Outreach Agent — drafting the note
  • Compliance Reviewer — checking it against the record
```

The counts are real. A local qwen2.5:7b filling J2001 called Screening four times — the
stream reports every delegation, and the UI groups them per agent so a model that went
round twice does not look like a rendering bug.

**Two things force the deployed shape**, and both are worth knowing before you redesign it:

- **A browser cannot call `InvokeAgentRuntime`.** It is a SigV4 AWS API call, and AWS
  endpoints send no CORS headers — so even handing the page real credentials from a Cognito
  identity pool fails at the preflight. Something server-side has to make the call.
- **That something cannot be Python.** Lambda response streaming exists on Node.js managed
  runtimes and custom runtimes only. `ui/proxy/index.mjs` is the one Node file in the repo
  and that is the whole reason for it.

CloudFront serves the React build and `/api/*` from **one distribution**, which is what
removes CORS entirely — there is no second origin for the browser to be told about.
`scripts/ui_server.py` reproduces that same-origin arrangement locally so `ui/src` is
written once and never learns which world it is in.

### The supervisor as an API

This is its own layer, `07_api`, with `08_ui` in front of it. The split is not cosmetic:
**07 has no browser in it.** It answers "how does an HTTP client reach an AgentCore
runtime?", and the web page is only its first caller — curl, another service or a CI job
use the same two routes. 08 answers a different question, "how does a web page reach that
API without a CORS problem?", and its answer is *put both behind one CloudFront domain*.

Apply 07 alone and you have a working API with no website. That is a legitimate place to
stop, which is the test for whether a layer deserves its own directory.

`/api/*` is **API Gateway REST**, not a raw Lambda URL — which only became possible in
[November 2025](https://aws.amazon.com/about-aws/whats-new/2025/11/api-gateway-response-streaming),
when `ResponseTransferMode: STREAM` arrived. Before that API Gateway buffered every Lambda
response and fixed the integration timeout at 29 seconds, and a two-minute pipeline did not
fit through it at all.

That buys throttling, access logs, and a Cognito authorizer that rejects unauthenticated
requests before any Lambda runs. It also means the supervisor is callable by things that
are not the browser — `terraform output api_url`:

```bash
API=$(terraform output -raw api_url)

TOKEN=$(curl -s $API/api/login -H 'content-type: application/json' \
          -d '{"username":"hr-agent","password":"..."}' | jq -r .token)

curl -N $API/api/chat -H "Authorization: $TOKEN" -H 'content-type: application/json' \
     -d '{"prompt":"Find the best candidate for J2001"}'
```

Two things there are not stylistic. It is a Cognito **ID** token, because an authorizer
with no `authorization_scopes` is the identity-claims path; and it goes in the header
**bare**, with no `Bearer ` prefix. Either one wrong is a 401 that explains nothing.

Unlike the function URL it replaced, the `execute-api` endpoint is publicly resolvable —
CloudFront's OAC has no `apigateway` origin type, so the authorizer is the gate rather than
network placement. That is the trade you accept when exposing an API deliberately, and it
is why the stage carries a throttle.

## Run the tests

```bash
make test        # all three suites: 93 python + 9 ui + 5 proxy
```

None of them touch AWS. Three runners because they guard three different seams — the
agents, the SSE frame parser, and the two gateway event shapes.

The JavaScript ones are all about one bug: **chunk boundaries are not frame boundaries.**
A `read()` can deliver half an SSE frame, or three and a half. Parsing each chunk as one
message passes every manual test against a local server and silently drops events in
production, so `api.test.js` feeds the parser splits mid-JSON, between the two terminating
newlines, and one byte at a time.

The one that matters most is `tests/test_store_parity.py`. It scores E1002, E1003 and
E1005 against J2001 twice — once from the Python dataset and once from JSON served
through the S3 code path — and demands byte-identical output:

```python
from_local = _score_all()
store.install()
from_s3 = _score_all()
assert from_s3 == from_local
```

**A score that changes when you move the data is a score nobody will defend.** If that
test ever fails, `store.install()` has started changing the domain instead of relocating
it, and every number in the system is suspect.

## Deploy to AWS

Nothing here has been applied — the Terraform is written, validated and internally
consistent, but **it has never been run against a real account.** Treat the first apply as
the real test, not as a formality.

### All at once

`terraform/00_all_at_once/` calls the eight numbered directories as child modules. Nothing
is copied by hand — `module.gateway.gateway_url` feeding `module.runtimes.gateway_url`
*is* the dependency, so Terraform works out the order itself:

```bash
cp terraform/00_all_at_once/example.tfvars terraform/00_all_at_once/my.tfvars
$EDITOR terraform/00_all_at_once/my.tfvars   # password + bedrock_model_id

make deploy
```

`make deploy` builds the seven zips, the seed JSON **and `ui/dist`** first, because
`filemd5()` and `fileset()` are evaluated at **plan** time, not apply. A missing `ui/dist`
fails with *"call to function fileset failed"*, which does not sound like *"you forgot to
build the UI"*.

Then `terraform output chat_url` gives you the CloudFront URL — sign in with `hr-agent` and
the password you set — and `terraform output env_file` prints the block to paste into `.env`
so local code can talk to the deployed stack. `make destroy` tears it down.

A UI-only change needs no cache invalidation: Vite fingerprints everything under `assets/`
so those are cached for a year, and `index.html` is uploaded `no-cache` precisely because it
is the file that names the new fingerprints.

Only two values are yours to choose. Everything else flows between modules.

Name your own tfvars anything except `example.tfvars` — the root `.gitignore` ignores
`*.tfvars` but explicitly un-ignores that one, so a password in it would be committed.

### Or one layer at a time

The numbered directories still work standalone, which is the point of the numbering: apply
one, look at what you made, then continue. They declare no provider of their own, so set
the region in the environment:

```bash
export AWS_REGION=us-west-2
cd terraform/01_s3_data && terraform init && terraform apply
cd ../02_lambda         && terraform apply -var bucket=… -var bucket_arn=…
```

Each has an `example.tfvars` naming exactly what the previous step's outputs must supply.
From the root module you can stop after a layer instead: `terraform apply -target=module.s3`.

**Memory is 04, before the runtimes**, because the supervisor takes `MEMORY_ID` as an
environment variable and memory itself depends on nothing. The five runtimes resolve in
one apply regardless of which path you take: they are split into three dependency tiers
(`leaf`, `screening`, `supervisor`) so Terraform wires each one's peer ARNs itself.

### Before you apply

```bash
make check
```

Four things `terraform validate` cannot tell you: that every required variable is some
earlier step's output, that the composing root passes all of them, that a `.tfvars`
documents the ones you must supply, and that the environment variables `05_runtimes` sets
are names `_shared/config.py` actually reads. The last one matters most — a typo there
does not fail, it falls back to `127.0.0.1`.

One thing Terraform cannot do for you:

- **`BEDROCK_MODEL_ID` has no safe default.** Model ids are account- and region-specific:
  `aws bedrock list-foundation-models --region us-west-2 --query 'modelSummaries[].modelId'`

And one it does, that you should know about before it does:

- **`enable_transaction_search` (default `true`) changes the whole account and region**, not
  just this stack — it points X-Ray trace segments at CloudWatch Logs, and grants X-Ray
  write access to the `aws/spans` log group. The traces delivery cannot be created without
  it. It bills, and `terraform destroy` turns it back off account-wide. Set it `false` in an
  account you do not own: you get application logs without traces, rather than a failed
  apply.
- **`manage_apigw_account_logging` (default `true`) does the same for API Gateway.** The
  CloudWatch Logs role is one setting per *region*, shared by every REST API in it, and a
  stage that logs cannot be created before it exists. `false` gives you the API with no
  access or execution logs, rather than a failed apply.

> **If a code change seems to have no effect, check `agent_runtime_version`.**
> A runtime points at its code as bucket + prefix. With a fixed S3 key, rebuilding the zip
> replaces the bytes and changes nothing Terraform can see — no diff, no update, **no new
> version** — so the container keeps running whatever it started with while your fix sits
> live in S3. The key now carries the artifact's content hash, and `make check` fails if
> that ever stops being true.
>
> ```bash
> terraform state show 'module.runtimes.aws_bedrockagentcore_agent_runtime.supervisor' \
>   | grep agent_runtime_version
> ```

### When the first apply goes wrong

Check these three before anything else — they are the failure modes this design has, and
none of them announces itself:

1. **`talent_screening` will not start.** Look for
   `RuntimeError: AGENTCORE=true needs SKILLS_MCP_ARN and GATEWAY_URL` in
   `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`. It means the tier-2 wiring did not land.
2. **Delegations time out.** The supervisor is addressing `127.0.0.1` inside its own
   container because one of `SCREENING_ARN` / `OUTREACH_ARN` / `COMPLIANCE_ARN` is unset —
   `agent_url()` falls back rather than failing.
3. **The dashboard is empty.** Either Transaction Search is off, or the traces delivery
   did not attach. Logs and traces are separate deliveries; logs working proves nothing
   about traces.

And three more once the UI is in play — the log group is in `terraform output
proxy_log_group`:

4. **The chat answers, but all at once after a long wait.** Something buffered the stream.
   The three candidates are `response_transfer_mode` on the integration (must be `STREAM`),
   `compress` on the `/api/*` behaviour (must be `false` — CloudFront buffers in order to
   compress), and an entrypoint that stopped being a generator.
5. **Every request is a 403 that reads like permissions.** Check the origin request policy
   is `AllViewerExceptHostHeader`, not `AllViewer`. API Gateway routes on `Host`, so
   forwarding the viewer's breaks every call.
6. **`ValidationException` on the session id.** `runtimeSessionId` has a minimum length of
   **33 characters**, on `InvokeAgentRuntime` *and* `GetAgentCard`. The obvious value to
   send is the requisition — and `J2001` is rejected by a constraint most people meet for
   the first time here. `safe_session_id()` pads it deterministically, because a random
   suffix per hop would give five traces that share nothing.
7. **401 on every chat, login fine.** The authorizer. It wants a Cognito **ID** token, bare
   — an access token or a `Bearer ` prefix both fail this way. `$context.authorizer.error`
   is in the stage's access log format for exactly this: `terraform output api_log_group`.
8. **A method change appears to do nothing.** An API Gateway deployment is a snapshot, so
   the stage can keep serving the previous version after a clean apply. The `triggers` hash
   on `aws_api_gateway_deployment` exists to prevent it; if you add a resource, add it there.
9. **500 with nothing in the Lambda log.** The integration URI. Streaming needs
   `2021-11-15/.../response-streaming-invocations`; paired with the ordinary
   `2015-03-31/.../invocations` API Gateway 500s rather than falling back to buffered.
10. **`CloudWatch Logs role ARN must be set in account settings`.** API Gateway does not
    log with the stage's own permissions — it assumes a role recorded once per *region*.
    `manage_apigw_account_logging` (default `true`) creates and sets it; set it `false` in
    an account you do not own and you get the API without logs instead of a failed apply.
11. **`Authorization method mismatch`.** The **supervisor** uses `custom_jwt_authorizer`, so
    it cannot be invoked with SigV4 — and the AWS SDK and CLI only speak SigV4. Call it over
    plain HTTPS with a bearer token, as `ui/proxy/index.mjs` does and `terraform output
    invoke_command` prints. The other half is the claim: `allowed_audience` matches an ID
    token's `aud`, `allowed_clients` matches an access token's `client_id`. This stack uses
    ID tokens, so it sets `allowed_audience`. The other four runtimes take SigV4 — see below.
12. **`The specific log group ... does not exist in this account or region`.** The runtime
    role needs `logs:CreateLogGroup`, not just `CreateLogStream` and `PutLogEvents` — with
    the latter two it can write into a group that exists and never create one. That grant is
    the whole fix: the container then makes its own group as it starts and writes its
    start-up failure into it. **Terraform deliberately does not create these** — the name
    contains a service-generated runtime id, so it cannot exist before the runtime does, and
    trying gives `ResourceAlreadyExistsException` on every apply. Fix this one first: without
    logs, everything below is undiagnosable.
13. **`AccessDenied ... s3:GetObject on .../skills/skills.json`.** `install()` sits at
    module scope in all five entrypoints, so with `DATA_SOURCE=s3` every runtime reads the
    records on import — not just the scoring engine. Scoping that grant to `hr_skills_mcp`
    made the other four crash before binding a port, which surfaces as a start-up failure
    rather than as anything mentioning S3. All five now get `GetObject` on the data
    prefixes; none gets `PutObject` on anything.
14. **`Failed to export span batch code: 403, reason: Forbidden`.** The runtime role needs
    `xray:PutTraceSegments` / `PutTelemetryRecords` / `GetSamplingRules` / `GetSamplingTargets`
    and `cloudwatch:PutMetricData`. Logging permissions do not cover it — spans go to X-Ray,
    not to CloudWatch Logs. **Non-fatal, which is the trap**: the agent answers normally and
    the dashboard just stays empty, so the 403 is only visible in the container's own log.
15. **`Runtime initialization time exceeded ... completes in 30s`** (HTTP 424). The
    container must unpack, import and answer its health check inside 30 seconds, and
    `opentelemetry-instrument` walks every installed instrumentation on the way up.
    `scripts/package.py` prunes packages nothing imports and `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS`
    turns off the ~30 instrumentations this system has no use for. If it still times out,
    the next levers are dropping `opentelemetry-instrument` from `entry_point`, and moving
    `install()`'s three S3 reads off the import path.

## The two modes, concretely

| | local | AgentCore |
|---|---|---|
| model | Ollama, `make_model()` returns `OllamaModel` | Bedrock, same call, `BedrockModel` |
| records | the lists in `hr_data.py` | `s3://hr-skills-…/employees/employees.json` |
| A2A agents | 127.0.0.1 on 9001 / 9002 / 9007 | all three on 9000, addressed by ARN |
| MCP server | `http://127.0.0.1:8000/mcp` | identical — the only file with no branch |
| screener's tools | in-process `@tool` functions | two MCP clients: direct + gateway |
| supervisor | a script you run | HTTP service on 8080 `/invocations` |
| memory | none | `AgentCoreMemorySessionManager`, session = requisition |
| tracing | `print()` in a hook | ADOT spans, one trace per requisition |

The screener is where this is most visible. Locally its tools are the two functions in its
own file; deployed, `clients/tools.py` hands it `hrskills___*` from a direct MCP
connection and `hrdata___*` from the Gateway. The prompt above that line never changes.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `TypeError: FastMCP() no longer accepts 'host'` | Every AWS example uses the FastMCP bundled in the `mcp` SDK. This repo uses standalone `fastmcp` 3.x, which moved `host`, `port` and `stateless_http` to `run()`. |
| The pipeline answers, but with a stale prompt | Something was already listening. `run_local.py` refuses to start on a busy port for exactly this reason — uvicorn logs `address already in use` and exits while the *old* process keeps answering. `lsof -tiTCP:9001 \| xargs kill -9`. |
| `✗ nothing at http://127.0.0.1:9001` | That agent is not running. Start `run_local.py` first. |
| The note names a role that does not exist | The screener dropped the requisition line. Its prompt requires the tool's first line; check the `ask_outreach_agent` argument in the trace. |
| The reviewer rejects true claims | It is asserting a check it never ran. `GroundTruth` in `people_compliance/main.py` attaches the HR record unconditionally so the model cannot skip it — see the gotcha below. |
| `invalid peer certificate: UnknownIssuer` | A TLS-inspecting proxy. `export UV_SYSTEM_CERTS=1`, or `uv sync --system-certs`. |
| `Attribute name value must match '^[a-zA-Z][a-zA-Z0-9_]{0,47}$'` | Memory strategy names take no hyphens. `candidate_facts`, not `candidate-facts`. |
| Deployed agent starts, then times out | The protocol/port contract. A2A is 9000 at `/`, MCP 8000 at `/mcp`, HTTP 8080 at `/invocations`. Bind the wrong one and the health check just fails. |
| Zip imports fine locally, crashes in Runtime | Wheels built for your Mac. `package.py` passes `--python-platform aarch64-manylinux2014 --only-binary=:all:`; without the second flag a package with no arm64 wheel silently builds from source for the wrong CPU. |
| Gateway or Memory logs are empty | Runtime creates its log group; those two do not. That is what `terraform/06_observability` is for. |

### The gotcha worth reading twice

The compliance reviewer's first version asked the model to call `verify_match_claim`
before judging. On `qwen2.5:7b` it did not — and then **rejected** a note for claims "not
verified by `verify_match_claim`", asserting a check it had never run, against facts that
were all true.

**A reviewer that rubber-stamps rejections is as broken as one that rubber-stamps
approvals.** The fix was to stop asking. `GroundTruth` extracts the ids with a regex, runs
`match()`, and appends the record to the request before the model sees it. The tool
remains for what the hook cannot see; the common path no longer depends on the model
choosing to check.

Same rule as everywhere else in this domain: `match()` is arithmetic, and arithmetic
should not be optional.

## Next steps

- [`architecture.md`](architecture.md) — the design document, and the mapping from
  [lesson 17](../strands-ai/app/17_memory_and_persistence/README.md)'s seven persistence
  layers onto what AWS takes over.
- [`a2a-strands/`](../a2a-strands/README.md) — the same topology in three terminals. Every
  failure here has a cheaper version there.
- [`mcp-server/`](../mcp-server/README.md) — the server `hr_skills_mcp` grew out of.
- [`terraform/`](../terraform/) — the 18-step Terraform track these six directories follow.
