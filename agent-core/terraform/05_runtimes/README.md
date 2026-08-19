# 05_runtimes — the five agent runtimes

Five containers, five execution roles, five artifacts, and the call graph between
them written as IAM.

**6 resource blocks producing 20 resources · 4 data sources · 11 variables
(8 required — the most in the stack) · 4 outputs.**

```bash
uv run scripts/package.py           # build all five zips FIRST
terraform apply -var-file=dev.tfvars
terraform output -raw supervisor_arn
```

---

## The three tiers

Read the three runtime resource blocks as three levels of a dependency graph:

| Tier | Resource | Runtimes | Needs |
|---|---|---|---|
| 1 | `aws_bedrockagentcore_agent_runtime.leaf` ×3 | `hr_skills_mcp`, `recruiting_outreach`, `people_compliance` | nobody |
| 2 | `aws_bedrockagentcore_agent_runtime.screening` | `talent_screening` | the MCP server's ARN |
| 3 | `aws_bedrockagentcore_agent_runtime.supervisor` | `hiring_supervisor` | all three specialists' ARNs |

An earlier version put all five in one `for_each`, which **cannot work**: a
resource cannot reference its own `for_each` siblings, so the peer ARNs had to be
set by a second apply. Splitting along the tiers lets Terraform build the DAG
itself, and the whole thing lands in one `apply`.

> **The environment variable names are load-bearing.** They are read by
> `app/_shared/config.py`, and a typo does not fail — `agent_url()` quietly falls
> back to `http://127.0.0.1:9001`, which inside a container reaches nothing.
> `make check` is what catches this; `terraform validate` cannot.

---

## Variables

| Variable | Required | From | Why |
|---|---|---|---|
| `bucket` | ✔ | `01` | Artifact upload target, and `S3_BUCKET` in every container |
| `bucket_arn` | ✔ | `01` | Scopes `s3:GetObject` in the execution policies |
| `gateway_url` | ✔ | `03` | `GATEWAY_URL` — the screener's MCP endpoint for `hr-data-fn` |
| `cognito_discovery_url` | ✔ | `03` | The supervisor's JWT authorizer |
| `cognito_client_id` | ✔ | `03` | `allowed_audience` on that authorizer |
| `gateway_arn` | ✔ | `03` | Scopes the screener's `InvokeGateway` to the one gateway |
| `memory_id` | ✔ | `04` | `MEMORY_ID` on the supervisor |
| `bedrock_model_id` | ✔ | **you** | Account- and region-specific; there is no safe default |
| `dist_dir` | | `../../dist` | Where `scripts/package.py` writes, relative to this module |
| `region`, `env` | | | Tags — the provider's region wins |

### `bedrock_model_id` has no default for a reason

Model ids are account- and region-specific. **Anthropic models on Bedrock need a
one-off use-case form approved per account**, and until it is, every call fails
with `ResourceNotFoundException: Model use case details have not been submitted`.
Amazon's own Nova models have no such gate, which is why the examples use
`us.amazon.nova-lite-v1:0`. Move to `us.amazon.nova-pro-v1:0` if Lite starts
wandering — this pipeline is tool-call heavy across five agents.

---

## Data sources

| Data source | Why |
|---|---|
| `aws_region.current`, `aws_caller_identity.current` | Build the runtime ARN **patterns** in `local.runtime_arn_pattern` |
| `aws_iam_policy_document.assume` | Trust policy — `bedrock-agentcore.amazonaws.com` |
| `aws_iam_policy_document.runtime` **×5** | One rendered policy per runtime, from `local.callees` |

---

## Locals — where the design actually lives

### `protocols` — the runtime → protocol map

```hcl
hr_skills_mcp       = "MCP"     # binds :8000 /mcp
recruiting_outreach = "A2A"     # binds :9000 /
people_compliance   = "A2A"
talent_screening    = "A2A"
hiring_supervisor   = "HTTP"    # binds :8080 /invocations
```

This map is also the `for_each` for the artifacts, roles and policies — so it is
the single list of runtimes. **The protocol determines the port and path the
container must bind**, and a mismatch is a health-check timeout with no mention
of ports.

### `callees` — the call graph, and now the entire authorization model

```hcl
callees = {
    hiring_supervisor = ["talent_screening", "recruiting_outreach", "people_compliance"]
    talent_screening  = ["hr_skills_mcp"]
}
```

The three leaves appear on **no left-hand side**, so they get no invoke grant at
all. `recruiting_outreach` and `people_compliance` are absent on purpose: they
receive work and answer, and a leaf that can invoke its own caller is a loop
waiting for a prompt that suggests it.

### `artifact_hash` — `filemd5()` per zip

Evaluated at **plan** time, so `dist/` must already hold the zips. It goes into
the S3 object key; see the artifacts section.

### `runtime_arn_pattern` — name patterns, not ARNs

```
arn:aws:bedrock-agentcore:<region>:<account>:runtime/<name>-*
```

`agent_runtime_name` is ours; the `-XXXXXXXXXX` suffix is the service's. Why
patterns and not the real ARNs is in [the IAM section](#the-invoke-grants).

### `base_env` — what every container gets

| Variable | Purpose |
|---|---|
| `AGENTCORE=true` | Selects the deployed code paths in `_shared/config.py` |
| `MODEL_PROVIDER=bedrock`, `BEDROCK_MODEL_ID` | Which model, and where from |
| `DATA_SOURCE=s3`, `S3_BUCKET` | Read the estate from the bucket, not from disk |
| `GATEWAY_URL` | The MCP endpoint for `hr-data-fn` |
| `AGENT_OBSERVABILITY_ENABLED`, `UNIFIED_TRACES_DESTINATION_ENABLED` | Turn on the OTLP exporter and point spans at one destination |
| `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS` | **A start-up budget setting** — see below |

**`OTEL_PYTHON_DISABLED_INSTRUMENTATIONS` is about start-up, not behaviour.**
`aws-opentelemetry-distro` ships ~45 instrumentation packages, and
`opentelemetry-instrument` walks every one of them before the server binds. That
is spent against a hard budget:

```
Runtime initialization time exceeded. Please make sure that initialization
completes in 30s.
```

The disabled list is everything these five containers have never talked to — a
PostgreSQL driver, an ORM, gRPC, Django, Celery. What stays instrumented is what
this system actually uses: **botocore** (Bedrock), **httpx** (A2A and MCP), and
**asgi/starlette/fastapi** (the servers themselves). Nothing breaks if you
shorten the list; it just costs start-up time.

Its partner lives in `scripts/package.py`, which prunes vendored packages nothing
imports. Together they took the supervisor artifact from **120 MB to 71**.

### The `allowed_audience` note

Two different checks against two different claims, and a Cognito ID token only
carries one of them:

| Setting | Matches claim | Token type |
|---|---|---|
| `allowed_audience` | `aud` | **ID token** |
| `allowed_clients` | `client_id` | access token |

This stack uses ID tokens end to end, because an API Gateway Cognito authorizer
with no `authorization_scopes` is the identity-claims path (see
[`07_api`](../07_api/)). So `allowed_clients` here would reject the very token
the front door just accepted:

```
Authorization method mismatch. The agent is configured for a different
authorization method than what was used in your request.
```

**Set one or the other, never both** — an ID token has no `client_id` claim to
match, so a config demanding both can never be satisfied.

---

## Resources

### `aws_s3_object.artifact` ×5 — the content-addressed key

```hcl
key    = "artifacts/${each.key}-${local.artifact_hash[each.key]}.zip"
source = "${local.dist}/${each.key}.zip"
# no etag
```

**The hash is IN THE KEY, and that is the whole point.**

A runtime references its code as bucket + prefix. With a fixed key like
`artifacts/hiring_supervisor.zip`, rebuilding the zip changes the bytes in S3 and
changes **nothing Terraform can see on the runtime** — same bucket, same prefix,
no diff, no update, no new version. The apply succeeds, the object is genuinely
replaced, and the container goes on running the code it started with.

That failure is completely silent. The only visible symptom is a runtime whose
`agent_runtime_version` never moves. Putting the hash in the key makes the
dependency real: new bytes → new key → the runtime's `prefix` changes → a new
runtime version running the code you just built.

It fixes something quieter too. AgentCore runtime versions are **immutable** —
but overwriting the object in place made version 4's "immutable" code change
underneath it. Content-addressed keys mean a version always points at the bytes
it was created with.

**Why no `etag`, when [`01_s3_data`](../01_s3_data/) sets one.** These zips are
~34 MB, so the provider uploads them multipart, and S3 returns an etag of the
form `<md5-of-part-md5s>-<partcount>` — `dbf0d032…-7`. That can never equal
`filemd5()`, so Terraform stores one value and compares against the other:

```
Plan: 0 to add, 5 to change, 0 to destroy.
```

forever, re-uploading 170 MB each time. Worse than the bandwidth, it **destroys
the signal** — the point of the content-addressed key is that a plan tells you
whether code changed, and a permanent diff means it cannot.

> **Cost:** old artifacts accumulate. Every live runtime version still references
> one, so do not blanket-expire them — prune by hand once you know which versions
> are retired.

### `aws_iam_role.runtime` ×5 and `aws_iam_role_policy.runtime` ×5
One role per runtime, so the policy differs per runtime. A shared role would make
the call graph below unexpressible.

### The execution policy, statement by statement

`data.aws_iam_policy_document.runtime` uses `for_each`, so each runtime gets its
own rendered document.

| Statement | Actions | Scope | Why |
|---|---|---|---|
| artifact | `s3:GetObject` | `${bucket_arn}/artifacts/*` | Read its own deployment zip |
| model | `bedrock:InvokeModel*` | `*` | The agent's model calls |
| logs | `logs:CreateLogGroup` + 5 more | `*` | See below |
| `Traces` | `xray:PutTraceSegments`, `PutTelemetryRecords`, `GetSamplingRules`, `GetSamplingTargets` | `*` | See below |
| `Metrics` | `cloudwatch:PutMetricData` | `*`, condition `namespace = bedrock-agentcore` | `PutMetricData` takes no resource ARN — the condition is the only bound |
| data | `s3:GetObject` | `employees/*`, `requisitions/*`, `skills/*` | See below |
| `InvokeItsOwnCalleesOnly` | `InvokeAgentRuntime`, `GetAgentCard` | this runtime's callees only | See below |
| memory | 7 `bedrock-agentcore:*Event*/*Memory*` | `*` | Supervisor only |
| `InvokeTheHRGateway` | `bedrock-agentcore:InvokeGateway` | the one gateway ARN | Screener only |

#### `logs:CreateLogGroup` is the one that is easy to leave out

With only `CreateLogStream` and `PutLogEvents`, a runtime can write into a group
that already exists and **cannot bring one into being**:

```
/aws/bedrock-agentcore/runtimes/<id>-DEFAULT does not exist in this account or region
```

Every other failure in the container then becomes undiagnosable, because the
place you would read about it is the thing that is missing. **This grant is the
only thing that creates these groups** — Terraform cannot (see
[the log-groups note](#log-groups-are-deliberately-not-created-here)).

Left on `"*"` deliberately: the same unknowable service-generated id is in the
log group ARN, and if a scoping pattern were ever wrong, the thing that breaks is
the place you would read about it breaking. Diagnosability wins.

#### Tracing needs `xray:*`, and logging permissions do not imply it

`AGENT_OBSERVABILITY_ENABLED=true` puts an OTLP exporter in every container.
Without these four actions it runs and fails on every batch:

```
Failed to export span batch code: 403, reason: Forbidden
```

**Non-fatal, which is the trap.** The agent answers normally, the GenAI
Observability dashboard simply stays empty, and the 403 only appears if you go
and read the container's own log. Spans go to X-Ray, not to CloudWatch Logs, so
the logging grants above cover none of this.

`GetSamplingRules`/`GetSamplingTargets` are called by the sampler at start-up;
without them the exporter falls back to a local default and logs about it.

#### Every runtime gets `s3:GetObject` on the data prefixes

Not just the scoring engine — and the reason is one line of Python. `install()`
sits at **module scope** in all five entrypoints, so with `DATA_SOURCE=s3` all
five fetch skills, employees and requisitions **on import**.

Scoping it to `hr_skills_mcp` alone was true of the design and false of the code:
the other four raised `AccessDenied` before binding a port, and an import-time
crash reads as a start-up failure rather than a permissions one.

The property worth keeping survives — **`GetObject` only.** Nothing here can
write. `shortlists/` belongs to `hr-data-fn` in [`02_lambda`](../02_lambda/) and
no runtime touches it.

#### The invoke grants

**IAM is now the whole check.** While the inner runtimes were `CUSTOM_JWT` the
token was the gate and the role was a second lock; moving them to SigV4 removed
the second lock. `InvokeAgentRuntime` on `"*"` — which this module used to grant
— let the outreach agent invoke the supervisor and the screener invoke anything
in the account, while the comment beside it read *"only the supervisor may
delegate"*.

So `local.callees` is turned straight into policy. Each edge grants **two**
actions, and the second is the one that catches people:

| Action | What it covers |
|---|---|
| `bedrock-agentcore:InvokeAgentRuntime` | sending the message |
| `bedrock-agentcore:GetAgentCard` | fetching `/.well-known/agent-card.json` **first** |

An A2A conversation is both calls, and discovery runs first — so a role with only
the invoke grant fails *before* the part it was granted:

```
GET .../invocations/.well-known/agent-card.json 403 Forbidden
```

which reads as *"the remote agent is down"*. It is not. One command settles it,
showing `allowed` beside `implicitDeny`:

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::<acct>:role/agentcore-hiring_supervisor-role \
  --action-names bedrock-agentcore:InvokeAgentRuntime bedrock-agentcore:GetAgentCard \
  --resource-arns <the callee's runtime arn>
```

**Why name patterns rather than the real ARNs — this is forced, not lazy.**
Referencing `aws_bedrockagentcore_agent_runtime.*.agent_runtime_arn` here would
make the policy depend on the runtimes. But the runtimes depend on this policy in
the way that matters: `screening_toolset()` wraps `a2a_serve.serve()`, so the
screener opens its MCP connections during container start — **inside
`CreateAgentRuntime`, while it waits for the health check.** Invert the order and
the container cannot reach `hr_skills_mcp`, never goes healthy, and the apply
fails on a timeout that mentions no IAM at all.

The `-*` covers the generated id suffix, and **IAM's `*` spans `/`**, so one
pattern also covers `.../runtime-endpoint/DEFAULT`.

#### `InvokeGateway` is a different action from `InvokeAgentRuntime`

Same trap, one layer down: **one conversation, several actions, and the failure
names none of them.** Scoped to `[gateway_arn, "${gateway_arn}/*"]`, because `*`
would be every gateway in the account.

### The three runtime blocks

All five share the same artifact and network shape:

```hcl
agent_runtime_artifact {
    code_configuration {
        entry_point = ["opentelemetry-instrument", "main.py"]
        runtime     = "PYTHON_3_13"
        code { s3 { bucket = var.bucket, prefix = aws_s3_object.artifact[…].key } }
    }
}
network_configuration { network_mode = "PUBLIC" }
protocol_configuration { server_protocol = local.protocols[…] }
```

**Two elements in `entry_point`.** `opentelemetry-instrument` is the ADOT
wrapper; drop it and you lose every span — the agent still works, silently.

`network_mode = "PUBLIC"` means AWS-managed networking with egress. A VPC mode
exists for reaching private resources; nothing here needs it.

#### Authorizers: only the supervisor has one

The four inner runtimes have **no `authorizer_configuration` block**, which means
**SigV4**, signed with the caller's execution role.

That is not a shortcut — it is the only thing that works. The natural design is
to forward the caller's bearer token down the chain, and AgentCore makes it
impossible: **it consumes `Authorization` at its edge and never passes it to the
container**, so there is nothing to reuse. A workload access token is not a
substitute either — AWS documents it as usable only against first-party AgentCore
identity services. The remaining option would be a Cognito machine password in
five container environments.

So: **humans authenticate with Cognito, machines with IAM.** The supervisor keeps
`custom_jwt_authorizer` because the thing on the other side of its door is a
person.

#### What each tier adds to `base_env`

| Runtime | Extra environment |
|---|---|
| the three leaves | none |
| `talent_screening` | `SKILLS_MCP_ARN` — read by `clients/tools.py`. Without it the container raises *"AGENTCORE=true needs SKILLS_MCP_ARN and GATEWAY_URL"* and never serves |
| `hiring_supervisor` | `SCREENING_ARN`, `OUTREACH_ARN`, `COMPLIANCE_ARN` (read by `clients/a2a_call.py:agent_url()`), and `MEMORY_ID` |

Miss one of the supervisor's three and that delegation **silently addresses
`127.0.0.1` inside the container**, which reaches nothing.

---

## There are deliberately no endpoint resources

`CreateAgentRuntime` creates a `DEFAULT` endpoint for you as part of the same
call. Declaring one named `DEFAULT` does not adopt it — it tries to create a
second by that name, and every apply fails:

```
ConflictException: An endpoint with the specified name already exists
```

This one is **not** eventual consistency and waiting will not help: the endpoint
genuinely exists, made microseconds earlier by the resource above.

Nothing is lost. `invoke-agent-runtime` with no `--qualifier` uses `DEFAULT`.
Endpoints are worth declaring when you want a **named** one — a `prod` alias you
repoint between runtime versions so callers keep one ARN across deploys:

```hcl
resource "aws_bedrockagentcore_agent_runtime_endpoint" "prod" {
    name             = "prod"          # any name but DEFAULT
    agent_runtime_id = aws_bedrockagentcore_agent_runtime.supervisor.agent_runtime_id
}
```

---

## Log groups are deliberately not created here

**Terraform cannot win this race, by construction.** The name is
`/aws/bedrock-agentcore/runtimes/{agent_runtime_id}-DEFAULT`, and
`agent_runtime_id` is generated by the service — `hiring_supervisor-Kt7PF58OuC`.
So the group can only be *declared* after the runtime exists, and creating the
runtime is what starts the container that creates the group. Terraform arrives
second, every time:

```
ResourceAlreadyExistsException: The specified log group already exists
```

[`07_api`](../07_api/) has the same fight and **wins** it, which is what makes
this worth stating: a Lambda's group is `/aws/lambda/{function-name}`, a name
known before the function exists, so `depends_on` puts Terraform first. There is
no equivalent here.

What actually fixed the original *"log group does not exist"* was granting
`logs:CreateLogGroup` on the execution role. The cost is retention — a
service-created group never expires — and that is one idempotent command needing
no Terraform state:

```bash
terraform output -json runtime_log_groups | jq -r '.[]' | xargs -I{} \
  aws logs put-retention-policy --log-group-name {} --retention-in-days 30
```

---

## Outputs

| Output | Notes |
|---|---|
| `runtime_arns` | Map of name → ARN. Feeds `.env` for local code |
| `runtime_log_groups` | **Computed, not managed** — read `hiring_supervisor`'s first when an invoke fails |
| `supervisor_arn` | The only runtime invoked from outside. Consumed by [`07_api`](../07_api/) |
| `invoke_command` | A ready-made `curl`, with the three things that go wrong baked in |

### `invoke_command` — curl, **not** the AWS CLI

The supervisor uses `CUSTOM_JWT` inbound auth, and the CLI and every AWS SDK sign
SigV4. AWS documents that an OAuth-configured agent **cannot be invoked through
them at all** — the CLI fails with *"Authorization method mismatch"*, and the
console's test button fails the same way.

Only the supervisor. For the other four the CLI is not merely allowed but the
only option: they have no token to send.

Three things the command encodes:

1. An **ID token**, not an access token.
2. The ARN **URL-encoded whole** — colons *and* slashes.
3. A session id of **33 characters minimum**, or `ValidationException`.

And the thing worth internalising: **the token is not forwarded.** AgentCore
consumes `Authorization` at its edge, so the supervisor never sees the token you
sent — delegation is SigV4 with its execution role, and works whether you sent
one or not.

---

## Things that bite

| Symptom | Cause |
|---|---|
| `Runtime initialization time exceeded … 30s` | Instrumentation walk, or an oversized artifact. See `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS` |
| Code change deployed, behaviour unchanged, version stuck | Fixed S3 key. This module uses content-addressed keys to prevent it |
| `Plan: 0 to add, 5 to change` on every plan | You added `etag` back to `aws_s3_object.artifact` |
| `403 Forbidden` on `agent-card.json` | `GetAgentCard` missing from the invoke grant |
| Delegation reaches nothing, no error | A misspelt `*_ARN` environment variable → `127.0.0.1` fallback. Run `make check` |
| Empty observability dashboard, agent works fine | Missing `xray:*` — read the container's own log for the 403 |
| Apply times out on `talent_screening` | The screener could not reach `hr_skills_mcp` during its health check |
| `ConflictException: endpoint already exists` | You declared a `DEFAULT` endpoint |
