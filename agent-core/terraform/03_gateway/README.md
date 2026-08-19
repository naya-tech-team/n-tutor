# 03_gateway — Cognito, and the AgentCore Gateway with one target

Two things that could be separate modules and are not, because they are created
in one breath and three later modules need outputs from both:

1. **Cognito** — the user pool, client and single machine user. The only way
   anyone mints a token for this stack.
2. **hr-gateway** — an AgentCore Gateway with exactly one target, which turns
   `hr-data-fn` into five MCP tools.

**8 resources · 4 data sources · 9 variables (2 required) · 7 outputs** — the
widest output surface in the stack, because [`05_runtimes`](../05_runtimes/),
[`06_observability`](../06_observability/) and [`07_api`](../07_api/) each need
something from here.

```bash
terraform apply \
  -var lambda_arn=$(cd ../02_lambda && terraform output -raw lambda_arn) \
  -var password='something-at-least-8-chars'
```

---

## Why the Gateway exists

It is a **protocol adapter**, not a router. A Lambda cannot speak MCP; the
Gateway makes it look like it does. `hr_skills_mcp` already speaks MCP natively,
so there is deliberately **no second target for it** — the screener connects to
that runtime directly.

The tool schemas below are the tax for that translation. The same five shapes
live in `app/lambda_fn/handler.py`, and nothing validates one against the other.

---

## Variables

| Variable | Type | Default | Notes |
|---|---|---|---|
| `lambda_arn` | string | **required** | From [`02_lambda`](../02_lambda/). The target, and the scope of the invoke policy |
| `password` | string | **required**, `sensitive` | The machine user's password. Min 8 chars |
| `username` | string | `hr-agent` | The one machine user that mints bearer tokens |
| `gateway_name` | string | `hr-gateway` | Names the gateway **and** its role, and scopes the trust condition |
| `gateway_authorizer_type` | string | `AWS_IAM` | `AWS_IAM` or `CUSTOM_JWT`. **Immutable** — see below |
| `iam_propagation_delay` | string | `30s` | How long to wait for the role's trust policy to propagate |
| `restrict_trust_to_gateway` | bool | `false` | Confused-deputy conditions. `true` on a **second** apply |
| `region`, `env` | string | `us-west-2`, `dev` | Tags. `env` also suffixes the pool name |

### `password` — put it somewhere that is not `example.tfvars`

The root `.gitignore` ignores `*.tfvars` but **explicitly un-ignores
`example.tfvars`**, so a password left in that file would be committed. Copy it
to `dev.tfvars` (gitignored) first. `sensitive = true` keeps it out of plan
output, but not out of state — `terraform.tfstate` holds it in plaintext.

### `gateway_authorizer_type` — immutable, and `AWS_IAM` for a real reason

There is a `validation` block so a typo fails at plan rather than at apply.

`AWS_IAM` is not a preference. The only caller of this gateway is
`talent_screening`, which builds its MCP client **at container start-up** — when
no request, and therefore no token, exists. And nothing in a container can obtain
a Cognito token anyway: **AgentCore consumes the caller's `Authorization` header
at its edge and never passes it to the container.** So `CUSTOM_JWT` here means
either a machine password in five container environments, or restructuring the
toolset to open a connection per request.

SigV4 removes the problem instead of working around it. Each request is signed as
it is sent, and botocore refreshes the role's credentials itself.

> **Changing this replaces the gateway and every target under it**, and the
> replacement has a different `gateway_url`. That is exactly why the URL is an
> output that [`05_runtimes`](../05_runtimes/) consumes rather than anything
> written down by hand.

### `restrict_trust_to_gateway` — false first, true second

AWS documents `aws:SourceAccount` / `aws:SourceArn` on a service trust policy as
a best practice **and** says to omit them on the first create, because you cannot
know the gateway ARN before the role that creates the gateway exists. Apply once
with `false`, then flip to `true` and apply again.

---

## Data sources

| Data source | Why |
|---|---|
| `aws_region.current` | The **real** region, for the discovery URL and the trust condition |
| `aws_caller_identity.current` | Account id for `aws:SourceAccount` and the gateway ARN pattern |
| `aws_iam_policy_document.assume` | The gateway role's trust policy |
| `aws_iam_policy_document.invoke` | `lambda:InvokeFunction` on the one Lambda |

**Why `data.aws_region.current` and not `var.region`?** A discovery URL built
from `var.region` can name a different region than the pool actually lives in,
and Cognito answers that with a bare 401 that says nothing about regions. Same
for the trust condition: a hardcoded region there is a known way to produce
*"Gateway service is not authorized to perform AssumeRole"* — the condition
silently stops matching and the trust policy does nothing.

---

## Resources

### Identity

#### `aws_cognito_user_pool.hr`
`hr-agents-${env}`. `minimum_length = 8` and nothing else — this pool has one
machine user, so a complexity policy would only make the tfvars harder to write.

#### `aws_cognito_user_pool_client.hr`

```hcl
generate_secret     = false
explicit_auth_flows = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
```

**No client secret**, because the browser in [`08_ui`](../08_ui/) and the login
route in [`07_api`](../07_api/) are both public clients — a secret shipped to a
browser is not a secret. `ALLOW_USER_PASSWORD_AUTH` is what makes
`initiate-auth --auth-flow USER_PASSWORD_AUTH` work; without it that call is
rejected and there is no way to get a token at all.

#### `aws_cognito_user.agent`

```hcl
password       = var.password     # NOT temporary_password
message_action = "SUPPRESS"
```

Without this user, the gateway and every runtime sit behind an authorizer with no
way to mint a token: the stack deploys and **nothing is callable.**

`password` rather than `temporary_password` is what makes it usable immediately —
a temporary password puts an interactive `NEW_PASSWORD_REQUIRED` challenge in
front of the first auth, which no script handles. `SUPPRESS` means no welcome
email to a machine account.

### The gateway's own identity

#### `aws_iam_role.gateway` + `aws_iam_role_policy.invoke`
`${gateway_name}-role`, trusted by `bedrock-agentcore.amazonaws.com`, allowed to
invoke exactly one Lambda. This is the role the **Gateway service** assumes to
reach your function — the counterpart to `aws_lambda_permission.gateway` in
[`02_lambda`](../02_lambda/). Both halves are required.

#### `time_sleep.iam_propagation` — load-bearing

```hcl
depends_on      = [aws_iam_role.gateway, aws_iam_role_policy.invoke]
create_duration = var.iam_propagation_delay
```

**IAM is eventually consistent, and this is where that bites.**

`CreateGateway` only *stores* the role ARN, so it succeeds immediately.
`CreateGatewayTarget` actually **assumes** the role to reach the Lambda — and if
the trust policy has not propagated everywhere yet:

```
ValidationException: Gateway service is not authorized to perform AssumeRole
on Gateway role. Update trust policy and retry
```

The message points at the trust policy, which is why this wastes an afternoon:
the policy is correct, it just does not exist everywhere yet. **"and retry" is
the real instruction.** This resource waits instead. Raise
`iam_propagation_delay` before concluding a policy is wrong.

### `aws_bedrockagentcore_gateway.hr`

```hcl
depends_on = [time_sleep.iam_propagation]
```

Not decorative. Nothing else makes the gateway wait for the trust policy to
exist everywhere, because the gateway only references the role's **ARN** — which
Terraform knows the instant the role is created. Terraform's graph is built from
references, and here the reference is not the dependency you need.

```hcl
protocol_configuration {
    mcp {
        instructions = "HR skills matching: …"
        search_type  = "SEMANTIC"
    }
}
```

`instructions` is what the model reads before choosing a tool. `search_type =
"SEMANTIC"` enables `tools/search` so an agent can ask for the right tool rather
than being handed all of them.

> Four tools do not need semantic search. It is set anyway because it can **only
> be enabled at creation** — turning it on later means recreating the gateway and
> every target under it.

The `authorizer_configuration` is a `dynamic` block that renders **only** when
`gateway_authorizer_type == "CUSTOM_JWT"`. With `AWS_IAM` the block is absent
entirely, which is what the API expects.

### `aws_bedrockagentcore_gateway_target.hrdata`

```hcl
depends_on = [time_sleep.iam_propagation]   # this is the call that ASSUMES the role
credential_provider_configuration { gateway_iam_role {} }
```

`gateway_iam_role {}` — empty on purpose — means *use the gateway's own role*.
The alternatives are an API key or an OAuth provider, for targets that reach
something outside AWS.

The `tool_schema` declares five `inline_payload` tools:

| Tool | What it does |
|---|---|
| `find_by_skill` | Employees with a skill at or above a level. Resolves aliases — `pyspark` → `Apache Spark` |
| `get_requisition` | One requisition and its required skills, with `min_level`, `mandatory`, `weight` |
| `list_bench` | Everyone unallocated, optionally by location |
| `record_shortlist` | Record a decision. Refuses candidates whose verdict is `blocked` |
| `get_shortlist` | Who has been shortlisted so far |

The descriptions are **prompts, not documentation.** *"The score from
hr_skills_mcp. Do not invent one."* and *"exactly as scoring returned it"* are
there because a model handed a `score` field will happily produce a plausible
number. This is the cheapest place in the stack to prevent that.

The agent sees these as `hrdata___find_by_skill` — three underscores, prefixed
with the target name. The handler strips it.

---

## Outputs

| Output | Consumed by |
|---|---|
| `gateway_url` | [`05_runtimes`](../05_runtimes/) → `GATEWAY_URL`; `.env` locally |
| `gateway_id` | Diagnostics — `aws bedrock-agentcore-control get-gateway` |
| `gateway_arn` | [`06_observability`](../06_observability/) (log delivery source), [`05_runtimes`](../05_runtimes/) (scopes `InvokeGateway`) |
| `cognito_discovery_url` | [`05_runtimes`](../05_runtimes/) — the supervisor's JWT authorizer |
| `cognito_client_id` | [`05_runtimes`](../05_runtimes/) (`allowed_audience`), [`07_api`](../07_api/) (authorizer + proxy env) |
| `cognito_user_pool_id` | [`07_api`](../07_api/) — the JWKS URL, and the ARN the API authorizer needs |
| `bearer_token_command` | You, by hand |

### `bearer_token_command` — read the note in it

```bash
export BEARER_TOKEN=$(aws cognito-idp initiate-auth \
  --client-id <client-id> --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=hr-agent,PASSWORD=<yours> \
  --query 'AuthenticationResult.IdToken' --output text)
```

**`IdToken`, not `AccessToken`.** The supervisor authorizes on
`allowed_audience`, which matches an ID token's `aud` claim; an access token
carries `client_id` instead and is rejected with a bare 401. The two look
identical in a terminal, which is what makes this worth stating twice.

This token buys **the front door only.** The other four runtimes and — by default
— this gateway take SigV4.

---

## Things that bite

| Symptom | Cause |
|---|---|
| `ValidationException: Gateway service is not authorized to perform AssumeRole` | IAM propagation. Raise `iam_propagation_delay` |
| Tools list fine, every call is 403 | `aws_lambda_permission.gateway` missing in [`02_lambda`](../02_lambda/) |
| 401 with nothing in it | Access token instead of ID token |
| `gateway_url` changed after an apply | You changed `gateway_authorizer_type`. It is immutable; the gateway was replaced |
| Want `search_type` on an existing gateway | Not possible. Recreate the gateway and its targets |

**`make gateway` probes this layer with no agent in the loop:**

```bash
make gateway                                             # list the published tools
make gateway CALL=hrdata___get_requisition ARGS='{"job_id":"J2001"}'
```

It signs with **your** credentials, so it needs `bedrock-agentcore:InvokeGateway`
on them. Whatever comes back came from the Gateway and Lambda alone.
