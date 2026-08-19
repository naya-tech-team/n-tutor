# 07_api — the supervisor, exposed over HTTP

A streaming Node.js Lambda behind a streaming REST API, with a Cognito
authorizer in front.

**There is no browser in this layer.** It answers one question — *how does an
HTTP client reach an AgentCore runtime?* — and the chat UI in
[`08_ui`](../08_ui/) is only its first caller. `curl`, another service or a CI
job use the same two routes.

```
POST /api/login    open, necessarily: it is where you get the token
POST /api/chat     Cognito authorizer -> the proxy -> the supervisor
```

**20 resources (4 of them gated) · 5 data sources · 13 variables (3 required) ·
5 outputs** — the largest module in the stack.

```bash
uv run scripts/package.py chat_proxy      # -> dist/chat_proxy.zip, FIRST
terraform apply -var-file=dev.tfvars
```

---

## Why a Lambda in the middle at all

Two constraints, neither optional:

**A browser cannot call `InvokeAgentRuntime`.** It is a SigV4 AWS API call, and
AWS endpoints send no CORS headers — so even a Cognito identity pool handing the
page real credentials fails at the preflight.

**That proxy cannot be Python.** Lambda response streaming works on **Node.js
managed runtimes and custom runtimes only.** Hence `nodejs22.x` and
`ui/proxy/index.mjs` in a repo that is otherwise Python.

And until **November 2025** API Gateway could not have fronted it at all: it
buffered every Lambda response and fixed the integration timeout at 29 seconds,
while a full run is three remote delegations and one to two minutes.
`response_transfer_mode = "STREAM"` is what changed that.

> Note it is the **streaming** that matters, not the timeout. A `BUFFERED`
> integration now allows 300 seconds, which is long enough to finish — and would
> still show the caller nothing until the last delegation returned.

---

## Variables

| Variable | Type | Default | Notes |
|---|---|---|---|
| `supervisor_arn` | string | **required** | From [`05_runtimes`](../05_runtimes/). The **only** runtime this proxy may invoke |
| `cognito_user_pool_id` | string | **required** | From [`03_gateway`](../03_gateway/). JWKS URL for the proxy + the authorizer's ARN |
| `cognito_client_id` | string | **required** | From [`03_gateway`](../03_gateway/) |
| `stage_name` | string | `v1` | Becomes `origin_path` in [`08_ui`](../08_ui/) |
| `proxy_timeout_seconds` | number | `300` | Applied to the Lambda **and** the integration |
| `throttle_rate` / `throttle_burst` | number | `5` / `10` | Cost control, not capacity |
| `manage_apigw_account_logging` | bool | `true` | **Account + region wide.** See below |
| `iam_propagation_delay` | string | `30s` | For the role that setting points at |
| `proxy_zip` | string | `../../dist/chat_proxy.zip` | Relative to this module |
| `retention_days` | number | `30` | Both log groups |
| `region`, `env` | string | | Tags; `env` also suffixes every name here |

### `proxy_timeout_seconds` is deliberately used twice

The Lambda's `timeout` and the integration's `timeout_milliseconds` come from the
same variable. **A lower value on the integration would cut off a function still
working**, and the client would see a truncated stream rather than an error.
Ceiling is 900 with `STREAM`, 300 with `BUFFERED`.

### `throttle_rate` is about money

Every chat request runs an agent for a minute or two and spends Bedrock tokens.
5 rps steady / 10 burst is a spend limit, not a capacity estimate. It also
matters because `/api/login` is the **only unauthenticated compute in the
stack.**

### `manage_apigw_account_logging` — the second account-scoped setting

**API Gateway does not log with the stage's own permissions.** It assumes a role
recorded in *account settings* — one per region, shared by every REST API in it —
and a stage that logs cannot be created before that role exists:

```
BadRequestException: CloudWatch Logs role ARN must be set in account settings
to enable logging
```

The error names the account, which is unusually helpful; what it does not say is
that **the fix is a resource nothing in your API refers to.**

The provider does not document what `terraform destroy` does to it, so **set this
`false` in an account you do not own.** You then get the API with no access logs
and no execution logs — including `$context.authorizer.error`, which is the thing
that explains a 401 — rather than a failed apply. If the setting already exists
in your account, leaving this `true` makes Terraform take ownership of it.

---

## Locals

| Local | Why |
|---|---|
| `zip` | `path.module` join — defaults must be literals, so the variable holds the relative part |
| `user_pool_arn` | Rebuilt from the id. 03 exports the **id** because that is what the proxy needs for the JWKS URL; the authorizer wants the ARN, and deriving it here beats a second output that can disagree |
| `streaming_uri` | See below |

### `streaming_uri` — **not** the ordinary Lambda proxy URI

```
arn:aws:apigateway:<region>:lambda:path/2021-11-15/functions/<arn>/response-streaming-invocations
```

Streaming integrations invoke through `InvokeWithResponseStream`, which is a
different API **version** *and* a different **action** — `2021-11-15` and
`/response-streaming-invocations` rather than `2015-03-31` and `/invocations`.

**Pairing `STREAM` with the ordinary URI is not a graceful degradation to
buffered: API Gateway returns a 500.**

---

## Data sources

| Data source | Why |
|---|---|
| `aws_caller_identity.current`, `aws_region.current` | Build the pool ARN and the integration URI |
| `aws_iam_policy_document.proxy_assume` | Lambda trust policy |
| `aws_iam_policy_document.proxy` | The proxy's permissions — logs, and nothing else |
| `aws_iam_policy_document.apigw_assume` (`count`-gated) | Trust policy for the account-level logging role |

---

## Resources — the proxy

### `aws_iam_role.proxy` + `aws_iam_role_policy.proxy` — **logs, and nothing else**

There is deliberately **no `bedrock-agentcore:InvokeAgentRuntime`** here. The
supervisor uses `CUSTOM_JWT` inbound auth, so the proxy reaches it over plain
HTTPS carrying the caller's bearer token — **not** with SigV4 — and an IAM
permission it never exercises would be a misleading grant rather than a harmless
one.

> If you ever switch the supervisor to SigV4 inbound auth, this is the statement
> to add back, scoped to `[supervisor_arn, "${supervisor_arn}/*"]`. **The second
> ARN is the easy one to miss:** invoking without a qualifier targets the
> `DEFAULT` *endpoint*, whose ARN is the runtime ARN plus a suffix — so a policy
> naming only the runtime ARN passes review and fails at runtime.

### `aws_cloudwatch_log_group.proxy`

Created here rather than left to the service, so **the retention is ours** — a
Lambda-created group defaults to never expiring.

This works only because `/aws/lambda/{function-name}` is knowable *before* the
function exists, so `depends_on` on the function below puts Terraform first.
[`05_runtimes`](../05_runtimes/) loses the identical race by construction, and so
declares no log group at all.

### `aws_lambda_function.proxy`

| Argument | Value | Why |
|---|---|---|
| `runtime` | `nodejs22.x` | Response streaming is Node-only |
| `architectures` | `["arm64"]` | Cheaper, and matches the rest of the stack |
| `handler` | `index.handler` | `ui/proxy/index.mjs` |
| `timeout` | `var.proxy_timeout_seconds` | Three delegations run inside one invocation |
| `memory_size` | `512` | |
| `depends_on` | the log group | Wins the naming race described above |

Environment: `AGENT_RUNTIME_ARN` (the supervisor), `COGNITO_USER_POOL_ID` and
`COGNITO_CLIENT_ID` — the proxy handles `/api/login` itself.

---

## Resources — the API

**What API Gateway buys over a plain Lambda function URL:** throttling and burst
limits, access logs, a custom domain if you want one, WAF, and — the reason it is
worth a layer of its own — **a Cognito authorizer that rejects unauthenticated
traffic before a Lambda is invoked.**

**What it costs:** the endpoint is publicly resolvable. A function URL can be
OAC-locked to one CloudFront distribution; `execute-api` cannot, because
CloudFront OAC has no `apigateway` origin type. The authorizer is the gate now,
which is the trade you make when you expose an API on purpose.

### `aws_api_gateway_rest_api.chat`
`REGIONAL` endpoint — edge-optimized would put a second CloudFront in front of
the one [`08_ui`](../08_ui/) already creates.

### `aws_api_gateway_authorizer.cognito` — an **ID** token, not an access token

```hcl
type            = "COGNITO_USER_POOLS"
provider_arns   = [local.user_pool_arn]
identity_source = "method.request.header.Authorization"
# authorization_scopes deliberately unset
```

A `COGNITO_USER_POOLS` authorizer has two modes, described in one sentence each:
**identity claims** (the ID token) or **custom scopes** (the access token).
Leaving `authorization_scopes` unset selects the first — so an access token here
is the configuration that half-works and fails per-request.

Using access tokens instead would mean a Cognito resource server, a custom scope,
and that scope allowed on the app client: three resources to avoid one word. It
would also have to match `allowed_audience` on the supervisor in
[`05_runtimes`](../05_runtimes/), which is the ID-token setting.

### The routes

`aws_api_gateway_resource.api` → `/api`, then
`aws_api_gateway_resource.route` **×2** → `/api/chat` and `/api/login`. The paths
match what the React calls and what [`08_ui`](../08_ui/) routes, so **neither has
to know this module's shape.**

| Method | Path | `authorization` |
|---|---|---|
| `aws_api_gateway_method.chat` | `POST /api/chat` | `COGNITO_USER_POOLS` |
| `aws_api_gateway_method.login` | `POST /api/login` | `NONE` |

`/api/login` is open **necessarily** — it is where you *get* the token, so it
cannot require one.

### `aws_api_gateway_integration.route` ×2

Both methods take the **identical** integration — one Lambda, which routes on the
path itself. They are separate method resources only because their
`authorization` differs.

```hcl
type                    = "AWS_PROXY"
integration_http_method = "POST"      # always POST, whatever the client used
uri                     = local.streaming_uri
response_transfer_mode  = "STREAM"    # the whole reason this API can front the supervisor
timeout_milliseconds    = var.proxy_timeout_seconds * 1000
```

`integration_http_method` is the method API Gateway uses **to call Lambda**, not
the method it accepts from the client. It is always `POST` for `AWS_PROXY`.

### `aws_lambda_permission.apigw`

```hcl
source_arn = "${aws_api_gateway_rest_api.chat.execution_arn}/*/*"
```

Any method, any path on **this** API — and nothing else. The resource-based half
of the pair; the IAM half is API Gateway's own service permission.

### `aws_api_gateway_deployment.chat` — the `triggers` are not optional

```hcl
triggers = { redeploy = sha1(jsonencode([ …every method, resource, integration, authorizer… ])) }
lifecycle { create_before_destroy = true }
```

**A deployment is a snapshot, and Terraform cannot see inside it.** Without these
triggers you change a method, apply cleanly, and serve the previous version —
**the single most common way an API Gateway change appears not to have worked.**

`create_before_destroy` keeps the stage pointing at something valid throughout
the replacement.

---

## Resources — the account-wide logging prerequisite

All four are `count`-gated on `manage_apigw_account_logging`.

| Resource | Why |
|---|---|
| `aws_iam_role.apigw_cloudwatch` | The role API Gateway assumes to write logs |
| `aws_iam_role_policy_attachment.apigw_cloudwatch` | `AmazonAPIGatewayPushToCloudWatchLogs`. Writing the permissions by hand gets them subtly wrong — it needs `CreateLogGroup` and `DescribeLogGroups`, not just `PutLogEvents` |
| `time_sleep.apigw_account` | **Third time in this stack.** API Gateway validates that it can assume the role at the moment you set it |
| `aws_api_gateway_account.this` | The account setting itself |

Same shape as [`03_gateway`](../03_gateway/) and
[`06_observability`](../06_observability/): a role created seconds earlier has
not propagated, and the failure reports a **permissions** problem rather than a
**timing** one.

---

## Resources — the stage

### `aws_api_gateway_stage.chat`

```hcl
depends_on = [aws_api_gateway_account.this]
```

Nothing in the stage's arguments refers to the account setting, so `depends_on`
is what orders them. Creating a logging stage before the role is recorded is the
`BadRequestException` above.

`access_log_settings` is a **`dynamic` block gated on the same flag**, and it has
to be: asking for access logs without the role is precisely what fails. `false`
gives you a working API with no logs instead of no API.

The log format includes two fields that are the whole reason to have it:

```json
"authorizerError": "$context.authorizer.error",
"errorMessage":    "$context.error.message"
```

**Without them a Cognito authorizer failure is a bare 401 with nothing anywhere
to say what was wrong with the token.**

### `aws_api_gateway_method_settings.throttle`

`method_path = "*/*"` — every method on the stage.

```hcl
logging_level = var.manage_apigw_account_logging ? "ERROR" : "OFF"
```

`ERROR`, not `INFO`: full request/response execution logging on a streaming
endpoint is a lot of CloudWatch for a chat transcript you already have. `OFF`
when the account role is not ours to set — execution logging needs it just as
access logging does, and asking for either without it **fails the apply rather
than degrading.**

---

## Outputs

| Output | Consumed by |
|---|---|
| `api_domain` | [`08_ui`](../08_ui/) → CloudFront's origin `domain_name` |
| `api_stage` | [`08_ui`](../08_ui/) → CloudFront's `origin_path` |
| `api_url` | You, `curl`, CI |
| `proxy_log_group` | Where a failed invoke explains itself |
| `api_log_group` | Where a 401 explains itself — **empty** when `manage_apigw_account_logging = false` |

> `api_domain` and `api_stage` are separate rather than one invoke URL, because
> CloudFront wants them in two different arguments — and splitting a URL back
> apart in HCL is how you end up with `https://` in a `domain_name`.

### Calling it by hand

```bash
TOKEN=$(curl -s $API/api/login -d '{"username":"hr-agent","password":"..."}' \
          -H 'content-type: application/json' | jq -r .token)
curl -N $API/api/chat -H "Authorization: $TOKEN" \
     -H 'content-type: application/json' \
     -d '{"prompt":"Find the best candidate for J2001"}'
```

**The bare token, with no `Bearer ` prefix.** The authorizer is documented only
as *"include the token in the Authorization header"*, and the bare form is the
one that reliably passes — a prefix gets you a 401 with nothing in it to say why.
The proxy strips an optional prefix regardless.

---

## Three settings that are load-bearing, and none of them errors when wrong

| Setting | Wrong value | What you see |
|---|---|---|
| `response_transfer_mode = "STREAM"` | `BUFFERED` | The whole SSE stream arrives at the end. Works, feels broken |
| `uri` = the `2021-11-15` streaming path | the ordinary `2015-03-31/invocations` | **500**, not a fallback |
| ID token, via unset `authorization_scopes` | an access token | 401 per request, config looks fine |

## Things that bite

| Symptom | Cause |
|---|---|
| `CloudWatch Logs role ARN must be set in account settings` | `manage_apigw_account_logging` off, or the role has not propagated |
| Method change applied cleanly, old behaviour served | `triggers` on the deployment |
| 401 with no explanation anywhere | Access logging is off, so `$context.authorizer.error` is not being written |
| Stream cut off mid-answer | Not this module — `origin_read_timeout` in [`08_ui`](../08_ui/) |
| 500 on every `/api/chat` | The integration URI is not the streaming one |
