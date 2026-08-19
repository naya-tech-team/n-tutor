# 06_observability — logs and traces

Wires CloudWatch delivery for the two components that produce none on their own,
and throws the account-level switch that makes traces possible at all.

**10 resource blocks (6 of them gated) · 3 data sources · 7 variables (2 required) ·
3 outputs.**

```bash
terraform apply \
  -var gateway_arn=$(cd ../03_gateway && terraform output -raw gateway_arn) \
  -var memory_arn=$(cd ../04_memory && terraform output -raw memory_arn)
```

---

## Why this is a step of its own

**Runtime creates its own log group. Gateway and Memory do not.**

Until you wire the delivery below, the busiest component in the system is the
only silent one. That asymmetry is the single most surprising thing in AgentCore
observability, and it is not something you discover by reading a dashboard — you
discover it when a Gateway tool call fails and there is nowhere to look.

With Transaction Search on, the GenAI Observability dashboard shows **one trace
per requisition across all five runtimes**, because every hop carries the same
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`.

---

## Variables

| Variable | Type | Default | Notes |
|---|---|---|---|
| `gateway_arn` | string | **required** | From [`03_gateway`](../03_gateway/) — the log delivery source |
| `memory_arn` | string | **required** | From [`04_memory`](../04_memory/) — the other delivery source |
| `enable_transaction_search` | bool | `true` | **Account + region wide.** Read this one before applying |
| `retention_days` | number | `30` | On the two **vended** groups this module creates |
| `iam_propagation_delay` | string | `30s` | Same knob, same hazard, as [`03_gateway`](../03_gateway/) |
| `region`, `env` | string | | Tags |

### `enable_transaction_search` — the one variable that reaches outside the stack

It points X-Ray trace segments at CloudWatch Logs **for the whole account and
region**, not just for this stack. Required for the traces delivery below;
without it `CreateDelivery` fails with:

```
ValidationException: X-Ray Delivery Destination is supported with CloudWatch
Logs as a Trace Segment Destination
```

Three things to know:

- **It bills.** Spans are ingested as CloudWatch Logs.
- **`terraform destroy` sets the account back to X-Ray-only**, which will
  silently stop traces for anything *else* in the account relying on it.
- **Set it `false` in a shared account you do not own.** You still get
  application logs; the three traces resources are simply not created, rather
  than created and broken.

### `retention_days` applies to two groups, not five

The runtime log groups belong to the service, are created by the container, and
**never expire**. Setting their retention is a one-off loop — see
[`05_runtimes`](../05_runtimes/README.md#log-groups-are-deliberately-not-created-here).

---

## Locals

```hcl
vended = {
    gateway = { arn = var.gateway_arn, id = "hr-gateway" }
    memory  = { arn = var.memory_arn,  id = "hr_hiring_desk" }
}
traced = var.enable_transaction_search ? local.vended : {}
```

`vended` reproduces **the console's own default naming**, so a delivery you
create here and one someone creates by clicking do not end up in two different
log groups.

`traced` is the gate. All three traces resources `for_each` over it, so
`enable_transaction_search = false` leaves nothing half-built — you get logs
without traces rather than a failed apply. Logs are unaffected: they never go
near X-Ray.

---

## Data sources

| Data source | Why |
|---|---|
| `aws_caller_identity.current`, `aws_region.current` | Build the `aws/spans` log group ARN and the confused-deputy conditions |
| `aws_iam_policy_document.xray_spans` (`count`-gated) | The resource policy that lets X-Ray write spans |

---

## Resources — application logs

**Delivery is three resources, not one**: a *source* (what emits), a
*destination* (where it lands), and the *delivery* that joins them. Create two of
the three and you get no error and no logs.

### `aws_cloudwatch_log_group.vended` ×2
```
/aws/vendedlogs/bedrock-agentcore/{gateway|memory}/APPLICATION_LOGS/{id}
```
`/aws/vendedlogs/` is the conventional prefix for logs a service delivers on your
behalf. Retention is ours because we created the group.

### `aws_cloudwatch_log_delivery_source.logs` ×2
`log_type = "APPLICATION_LOGS"`, `resource_arn` = the gateway or memory ARN. This
is what says *"this AgentCore resource emits logs"*.

### `aws_cloudwatch_log_delivery_destination.logs` ×2
`delivery_destination_type = "CWL"`, `output_format = "json"`, pointed at the
group above. The other destination types are S3 and Firehose.

### `aws_cloudwatch_log_delivery.logs` ×2
Joins one source to one destination. **This is the piece that is easy to omit**,
and its absence is not an error — it is an empty log group.

---

## Resources — Transaction Search

The docs and the console make this look like one switch:

```bash
aws xray update-trace-segment-destination --destination CloudWatchLogs
```

**It is two.** Clicking "enable" in the console also writes a CloudWatch Logs
resource policy behind your back; the API call does not — it just fails:

```
AccessDeniedException: XRay does not have permission to call PutLogEvents
on the aws/spans Log Group
```

The CLI is not an option on a machine behind a TLS-inspecting proxy either: it
fails with `CERTIFICATE_VERIFY_FAILED` where Terraform, whose Go TLS reads the
macOS keychain, works fine. So **both halves live here.**

### Half one — `aws_cloudwatch_log_resource_policy.xray_spans`

```hcl
principals { type = "Service", identifiers = ["xray.amazonaws.com"] }
actions = ["logs:PutLogEvents"]
```

This is a **resource** policy, attached to the log groups rather than to a role —
which is why there is no role anywhere in this file, and why searching IAM for
the missing permission turns up nothing.

Two groups are named:

| Group | Why |
|---|---|
| `aws/spans` | Where Transaction Search puts trace segments |
| `/aws/application-signals/data` | AWS documents the pair together; leaving it out works right up until you turn Application Signals on |

**Neither group is created here.** The service makes `aws/spans` on first write,
and a policy may name a log group that does not exist yet.

The `aws:SourceArn` / `aws:SourceAccount` conditions are the confused-deputy
pair. Unlike the gateway trust policy in [`03_gateway`](../03_gateway/), these
**can** be set on the first apply: both values are known before anything exists,
so there is no chicken-and-egg to opt out of.

### `time_sleep.policy_propagation`
Resource policies propagate the same way trust policies do, and X-Ray checks this
one **synchronously** during the call below. Same failure mode as the gateway
role in 03, same fix, same variable name.

### Half two — `aws_xray_trace_segment_destination.cwl`

```hcl
destination = "CloudWatchLogs"
depends_on  = [time_sleep.policy_propagation]
```

The switch itself. Fails with the 403 above unless half one landed first, and
**nothing in its arguments says so** — hence `depends_on`.

---

## Resources — traces

Same three-part shape as logs, with one difference that costs an afternoon:

### `aws_cloudwatch_log_delivery_destination.traces` ×2

```hcl
delivery_destination_type = "XRAY"
# and NO delivery_destination_configuration block
```

**X-Ray is not a resource you point at.** Passing the gateway's own ARN there —
the obvious first guess — creates a destination that accepts the apply and
delivers nothing.

### `aws_cloudwatch_log_delivery.traces` ×2

```hcl
depends_on = [aws_xray_trace_segment_destination.cwl]
```

The piece that was missing entirely in an earlier version. A source and a
destination are two halves of nothing until a delivery joins them — and **the
symptom is not an error, it is an empty GenAI Observability dashboard.**

This is also the resource that fails when Transaction Search is off. The source
and destination above are both created happily first, which is why the error
arrives last and names neither of them.

---

## Outputs

| Output | Notes |
|---|---|
| `log_groups` | Map of `gateway`/`memory` → group name |
| `runtime_logs_note` | A signpost: runtime logs are `terraform output runtime_log_groups` on [`05_runtimes`](../05_runtimes/) |
| `transaction_search` | Human-readable `on` / `OFF`, so a plan diff tells you which you have |

> `runtime_logs_note` is **deliberately not named `runtime_log_groups`.** 05 has a
> real output by that name, and two outputs sharing a name across modules is how
> you end up reading a signpost as data. It is also what the name-matching in
> `check_terraform_chain.py` resolves against, so a collision could make that
> audit answer about the wrong module.

---

## Where to look when something is wrong

| Component | Log group | Created by |
|---|---|---|
| the five runtimes | `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT` | **the service**, via `logs:CreateLogGroup` on the execution role |
| the gateway | `/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/hr-gateway` | this module |
| memory | `/aws/vendedlogs/bedrock-agentcore/memory/APPLICATION_LOGS/hr_hiring_desk` | this module |
| `hr-data-fn` | `/aws/lambda/hr-data-fn` | Lambda itself |
| the chat proxy | `/aws/lambda/hr-chat-proxy-<env>` | [`07_api`](../07_api/) |
| API Gateway access logs | `/aws/apigateway/hr-chat-<env>` | [`07_api`](../07_api/) |

**Start with the supervisor's runtime log** when an invoke fails. It is the only
one that sees the whole conversation.

---

## Things that bite

| Symptom | Cause |
|---|---|
| `AccessDeniedException: XRay does not have permission to call PutLogEvents on the aws/spans Log Group` | Half one has not propagated. Raise `iam_propagation_delay` |
| `X-Ray Delivery Destination is supported with CloudWatch Logs as a Trace Segment Destination` | Transaction Search is off |
| Empty dashboard, no errors anywhere | A delivery resource is missing — a source + destination with no delivery is silent |
| Traces destination created, still nothing | You added `delivery_destination_configuration` to the `XRAY` destination |
| Traces missing for one runtime only | Not this module — check `xray:*` on that runtime's role in [`05_runtimes`](../05_runtimes/) |
| Someone else's traces stopped after a `destroy` | `enable_transaction_search` is account-wide. This is the documented cost |
