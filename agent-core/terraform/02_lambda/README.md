# 02_lambda — `hr-data-fn`, the estate as MCP tools

One Python function and the IAM around it. It reads employees, requisitions and
shortlists out of S3 and writes shortlist decisions back.

It is **not** called by an agent directly. [`03_gateway`](../03_gateway/) points
an AgentCore Gateway target at it, and the Gateway is what turns a Lambda into
five MCP tools. This module exists separately because the Gateway needs a
`lambda_arn` before it can be created.

**5 resources · 2 data sources · 5 variables (2 required) · 2 outputs.**

```bash
uv run scripts/package.py hr_data_fn      # -> dist/hr_data_fn.zip, build this FIRST
terraform apply \
  -var bucket=$(cd ../01_s3_data && terraform output -raw bucket) \
  -var bucket_arn=$(cd ../01_s3_data && terraform output -raw bucket_arn)
```

---

## Why a Lambda and not another runtime

The Gateway is a **protocol adapter, not a router**. Its job is to make something
that cannot speak MCP speak MCP.

A Lambda cannot, so it needs one. `hr_skills_mcp` in
[`05_runtimes`](../05_runtimes/) already can, which is why there is deliberately
no second Gateway target for it — the screener reaches that runtime directly.
See `architecture.md`, *"Why two MCP connections and not one"*.

This is also **the only thing in the stack that writes to S3.** Every runtime
gets `s3:GetObject` and nothing else.

---

## Variables

| Variable | Type | Default | Where it comes from |
|---|---|---|---|
| `bucket` | string | **required** | `01_s3_data` → `terraform output -raw bucket` |
| `bucket_arn` | string | **required** | `01_s3_data` → `terraform output -raw bucket_arn` |
| `package` | string | `../../dist/hr_data_fn.zip` | Built by `scripts/package.py`, relative to *this* module |
| `region` | string | `us-west-2` | Tags only — see [`01_s3_data`](../01_s3_data/README.md#variables) |
| `env` | string | `dev` | Tags |

**Why both `bucket` and `bucket_arn`?** They are used in two different places and
neither is derivable from the other without string surgery. The name goes into
the function's environment (`S3_BUCKET`, which boto3 wants); the ARN goes into
the IAM policy (`arn:aws:s3:::name/*`, which IAM wants). Passing both is cheaper
than building one from the other and getting the partition wrong in `aws-cn` or
GovCloud.

---

## Locals

```hcl
package = "${path.module}/${var.package}"
```

Same rule as everywhere in this stack: `filebase64sha256("../../dist/…")`
resolves against the **process working directory**, which changes the moment this
module is called from `00_all_at_once`. `path.module` is always this directory.
It cannot go in the variable default, because Terraform requires defaults to be
literals — hence the local.

---

## Data sources

Both are `aws_iam_policy_document`, which is a policy **builder**, not a lookup.
Nothing is fetched from AWS; the provider renders JSON. It is used instead of a
heredoc because it validates structure at plan time and produces canonical JSON,
so a reformatted policy does not show up as a diff.

### `aws_iam_policy_document.assume`
The trust policy. `sts:AssumeRole` for the service principal
`lambda.amazonaws.com` — this is what makes the role *a Lambda execution role*
rather than just a role.

### `aws_iam_policy_document.s3`
Two statements, and the split is the point:

| Statement | Actions | Resources |
|---|---|---|
| read | `s3:GetObject` | `${bucket_arn}/*` — the whole bucket |
| write | `s3:PutObject` | `${bucket_arn}/shortlists/*` — **that prefix only** |

The function reads everything and can write to exactly one prefix. It has no
business rewriting the employee directory either, and a scoping mistake here
would not fail an apply — it would fail an audit, months later.

---

## Resources

### `aws_iam_role.fn`
Named `hr-data-fn-role`. Trust policy from `data.aws_iam_policy_document.assume`.

### `aws_iam_role_policy.s3`
An **inline** policy — it lives on the role and is deleted with it. Right choice
for a policy exactly one role will ever use. A managed policy
(`aws_iam_policy` + attachment) is what you want when several roles share it.

### `aws_iam_role_policy_attachment.logs`
`AWSLambdaBasicExecutionRole`, the AWS-managed policy that grants
`CreateLogGroup` / `CreateLogStream` / `PutLogEvents`. Writing these three out by
hand is a way to get them subtly wrong; the managed policy is maintained by AWS.

Without it the function runs and its logs go nowhere, so the first failure you
investigate has no evidence attached to it.

### `aws_lambda_function.hr_data`

```hcl
filename         = local.package
source_code_hash = filebase64sha256(local.package)
```

`source_code_hash` is what makes a rebuilt zip actually deploy. Without it
Terraform compares the *path*, which never changes, and every code fix is a
silent no-op.

| Argument | Value | Why |
|---|---|---|
| `handler` | `main.lambda_handler` | `main.py` at the zip root, as `package.py` builds it |
| `runtime` | `python3.13` | |
| `architectures` | `["arm64"]` | **Not the default.** AgentCore Runtime is arm64 and `package.py` builds aarch64 wheels — leaving this at `x86_64` imports those wheels into the wrong CPU, and the error is an obscure `ELF header` failure at import |
| `timeout` | `30` | A model waiting on a tool call is a session ticking along. Fail fast |
| `memory_size` | `512` | Memory also buys CPU on Lambda; 512 is where JSON parsing of the estate stops being the bottleneck |

Environment: `DATA_SOURCE=s3` and `S3_BUCKET`. **There is deliberately no
`AWS_REGION`** — it is a reserved environment variable that Lambda refuses to let
you set, the runtime provides it, and `Settings.aws_region` picks it up from the
environment either way.

### `aws_lambda_permission.gateway`

```hcl
principal = "bedrock-agentcore.amazonaws.com"
action    = "lambda:InvokeFunction"
```

**This is a resource-based policy, and it is the one people leave out.** IAM on
the Gateway's role says *the Gateway may call this Lambda*; this says *this
Lambda accepts calls from AgentCore*. Cross-service invocation needs both sides.

Without it the Gateway target creates successfully, the tools appear in
`tools/list`, and **every tool call is a 403** — which reads like a Gateway
problem, several layers away from the missing line.

> This grant is not scoped with `source_arn`, so any AgentCore Gateway in any
> account could invoke the function if it knew the ARN. Tightening it means a
> second apply, the same chicken-and-egg as `restrict_trust_to_gateway` in
> [`03_gateway`](../03_gateway/): the gateway ARN does not exist yet on the first
> run.

---

## Outputs

| Output | Consumed by |
|---|---|
| `lambda_arn` | [`03_gateway`](../03_gateway/) — the target, and the scope of its invoke policy |
| `lambda_name` | Convenience: `aws logs tail /aws/lambda/hr-data-fn --follow` |

---

## Things worth knowing

**The zip must exist before `plan`.** `filebase64sha256()` is evaluated at plan
time. `make artifacts` builds every artifact in the repo; `uv run
scripts/package.py hr_data_fn` builds just this one.

**The tool schemas are declared in [`03_gateway`](../03_gateway/), not here.**
The same five shapes exist in `app/lambda_fn/handler.py`, and **nothing checks
the two against each other.** A parameter renamed in one place and not the other
fails at call time with a validation error from the Gateway.

**The agent sees the tools prefixed**: `hrdata___find_by_skill`, three
underscores, from the target name. The handler strips the prefix itself.

**No log group is declared here**, unlike [`07_api`](../07_api/), which does
declare one for its proxy. The cost is retention — a Lambda-created group never
expires. `AWSLambdaBasicExecutionRole` grants `CreateLogGroup`, so the function
makes its own on first invocation.
