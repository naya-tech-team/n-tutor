# 04_memory — AgentCore Memory, keyed the way the business is keyed

One memory resource, three extraction strategies, and the IAM role that lets the
service call a model on your behalf.

**6 resources · 1 data source · 2 variables (neither required) · 2 outputs.**

```bash
terraform init && terraform apply
terraform output -raw memory_id      # -> MEMORY_ID in agent-core/.env
```

---

## Why this is `04` and not `07`

**It depends on nothing but its own IAM role.** No bucket, no gateway, no
runtime — which is what lets it sit this early.

It has to. The supervisor takes `MEMORY_ID` as an environment variable, so the
memory must *exist* before [`05_runtimes`](../05_runtimes/) is applied. That is
deliberately different from the order you *verify* things in: memory is the last
thing you exercise, because you cannot see it work until an agent has held two
conversations.

---

## The keying decision, which is the whole module

```
actorId   = the recruiter
sessionId = the requisition   (J2001)
```

One conversation per open requisition. This is a **business key, not plumbing** —
and picking it badly produces a system that is wrong in a way that looks like a
model problem. `sessionId = uuid4()` gives you an agent that forgets J2001
between Tuesday and Thursday, and nothing in the logs says so.

Every namespace template below is built from these two, which is why they appear
in the strategy definitions rather than anywhere in the agent code.

---

## Variables

| Variable | Type | Default | Notes |
|---|---|---|---|
| `region` | string | `us-west-2` | Tags only — the provider's region wins |
| `env` | string | `dev` | Tags |

That is the entire surface. Nothing here comes from an earlier step, and nothing
needs to.

---

## Data sources

### `aws_iam_policy_document.assume`
`sts:AssumeRole` for `bedrock-agentcore.amazonaws.com`. This is the **memory
service** assuming a role in your account, not an agent doing it.

---

## Resources

### `aws_iam_role.memory`
`hr-memory-role`. Assumed by AgentCore when it runs extraction.

### `aws_iam_role_policy_attachment.inference`

```hcl
policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy"
```

**Long-term memory is not storage, it is extraction — and extraction is a model
call.** Without this attachment the memory resource exists, events land in it
happily, and **no memory record is ever produced.** There is no error at the
point you would look for one, because the failure happens asynchronously on the
service side after the agent has already answered.

AWS's managed policy rather than a hand-written one: the exact set of
`bedrock:InvokeModel` grants it needs is not documented as a list, and getting it
subtly wrong reproduces exactly the silent failure above.

### `aws_bedrockagentcore_memory.hiring_desk`

```hcl
name                  = "hr_hiring_desk"
event_expiry_duration = 90         # days, for RAW events
```

A requisition that has been open a quarter is a requisition in trouble. 90 days
of raw events is long enough to see that and short enough to bound the cost.

Note what expires: the **raw event log**. Extracted long-term records live under
the namespaces below and are governed separately.

### The three strategies

Each one is a separate resource attached to the memory by `memory_id`.

| Resource | `name` | `type` | Namespace template | What it holds |
|---|---|---|---|---|
| `.facts` | `candidate_facts` | `SEMANTIC` | `/requisitions/{sessionId}/facts` | *"E1005 is blocked on Python and SQL for J2001"* |
| `.preferences` | `recruiter_preferences` | `USER_PREFERENCE` | `/recruiters/{actorId}/preferences` | *"never shortlists below 70%"* |
| `.summaries` | `requisition_summary` | `SUMMARIZATION` | `/summaries/{actorId}/{sessionId}` | A week of work on one requisition, in a paragraph |

Read the namespaces as the retrieval query you will be able to write later.
Facts are keyed by **requisition** because a fact about a candidate is only true
in the context of a job. Preferences are keyed by **recruiter** because they
follow the person across requisitions. Summaries need both.

> **Read summaries for context, never for verdicts.** A summariser will
> paraphrase *"blocked on Apache Spark"* into *"some gaps"* — and that is exactly
> how a blocked candidate gets a warm outreach note next Tuesday.

---

## Two traps in this module

### The `type` values have two spellings, and neither doc mentions the other

| Terraform wants | boto3 wants |
|---|---|
| `SEMANTIC` | `semanticMemoryStrategy` |
| `SUMMARIZATION` | `summaryMemoryStrategy` |
| `USER_PREFERENCE` | `userPreferenceMemoryStrategy` |

You will meet both, often in the same afternoon, and searching the docs for one
will not find the other.

### Strategy names must match `^[a-zA-Z][a-zA-Z0-9_]{0,47}$`

**No hyphens.** `candidate-facts` is rejected; `candidate_facts` is not. Which is
why these three names break the naming convention used everywhere else in the
stack.

---

## The extraction model is not named here — and that matters

None of the three strategies specifies a model, so **AgentCore uses its own
default**, which is an Anthropic model. In an account without Anthropic model
access, extraction fails with:

```
ResourceNotFoundException: Model use case details have not been submitted
for this account
```

…from a component nobody was watching, asynchronously, after the agent has
already answered.

**Pinning it is not one line.** `configuration.extraction` requires **both**
`model_id` and `append_to_prompt`, so overriding the model means replacing AWS's
tuned extraction prompt with your own — a real change to what gets remembered,
not a configuration detail. Worth doing deliberately; not worth doing to silence
an error you have not confirmed came from here.

**To test whether memory is the source of a problem at all:** unset `MEMORY_ID`
on the supervisor. `_session_manager()` returns `None` and the entire memory path
is skipped, so anything still broken is broken somewhere else.

---

## Outputs

| Output | Consumed by |
|---|---|
| `memory_id` | [`05_runtimes`](../05_runtimes/) → the supervisor's `MEMORY_ID`; `.env` locally |
| `memory_arn` | [`06_observability`](../06_observability/) — the log delivery source |

---

## Things worth knowing

**Memory produces no logs of its own until [`06_observability`](../06_observability/)
wires the delivery.** Runtime creates its own log group; gateway and memory do
not. Until then, an extraction failure is completely invisible.

**The supervisor's IAM grant for memory is still `resources = ["*"]`** in
[`05_runtimes`](../05_runtimes/). Scoping it needs `memory_arn`, which this
module outputs and that one does not currently take — a known follow-up, recorded
rather than left to be rediscovered.

**The header comment in `main.tf` says `# 05 —`.** It is a mislabel in a comment;
the directory and everything else are correct.
