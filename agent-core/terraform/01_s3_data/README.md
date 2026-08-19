# 01_s3_data — the system of record

One bucket. It holds the three JSON files the agents read, the shortlists the
Lambda writes, and later the deployment zips `05_runtimes` uploads.

Everything downstream reads from here, so this is layer 01: nothing else in the
stack can be built until the bucket has a name.

**5 resources · 2 data sources · 3 variables (none required) · 2 outputs.**

```bash
uv run scripts/seed_s3.py                 # writes seed/**.json — do this first
terraform init && terraform apply -var-file=example.tfvars
terraform output -raw bucket              # -> S3_BUCKET in agent-core/.env
```

---

## Why the data is in S3 at all

These three files started life as Python literals. Moving them out is what makes
the deployed system and the local one the same system: `DATA_SOURCE=s3` reads
from here, `DATA_SOURCE=local` reads the same shapes off disk, and the agent code
does not know which it got.

`skills.json` is the one that is easy to forget, because it does not look like
data. It is the alias table — the thing that knows `pyspark` means
`Apache Spark`. Leave it behind and `find_by_skill("pyspark")` silently returns
nobody, which reads as "the model got it wrong".

---

## The `terraform` block

```hcl
required_providers {
    aws = { source = "hashicorp/aws", version = ">= 6.58" }
}
```

The rest of the repo lets the provider float; **the terraform directories pin
it.** `aws_bedrockagentcore_*` resources do not exist before provider 6.51, and
the failure on an older provider is `invalid resource type` — which reads like a
typo in your HCL, not like a version problem. The floor is set here even though
this module uses no AgentCore resource, so that `terraform init` in any module
resolves the same provider.

There is deliberately **no `provider` block**. Only [`00_all_at_once`](../00_all_at_once/)
declares one. Standalone, Terraform builds a default provider from `AWS_REGION`
and `~/.aws/config`.

---

## Variables

| Variable | Type | Default | Why it exists |
|---|---|---|---|
| `region` | string | `us-west-2` | Tags-and-convention only. **It does not configure the provider here** — see below |
| `env` | string | `dev` | Goes into `local.common_tags`. Other modules also use it as a name suffix |
| `seed_dir` | string | `"seed"` | Where `scripts/seed_s3.py` writes, *relative to this module* |

**`region` is nearly vestigial, on purpose.** Every module declares it so the
variable surface is uniform, but interpolations use
`data.aws_region.current.region` instead. Setting `region` on a child module is a
silent no-op — the provider's region wins. The alternative is worse: a value
built from `var.region` can name a different region than the resource actually
landed in, and the resulting 401 or 404 says nothing about regions.

**`seed_dir` holds only the relative part.** `path.module` cannot appear in a
variable default — Terraform requires defaults to be literals — so the join
happens in a local. See the next section for why that matters.

---

## Data sources

| Data source | Why |
|---|---|
| `aws_caller_identity.current` | The account id goes into the bucket name |
| `aws_region.current` | The region goes into the bucket name, and it is the *real* region rather than `var.region` |

S3 bucket names are **globally unique across every AWS account on earth**. A
literal `hr-skills` would fail for the second person who ran this. Account id +
region makes the name collision-free and derivable, so nothing has to be typed
into a tfvars file.

---

## Locals

```hcl
bucket_name = "hr-skills-${account_id}-${region}"
seed_root   = "${path.module}/${var.seed_dir}"
```

`seed_root` is the important one. `filemd5("seed/employees.json")` resolves
against the **process working directory**, so a bare relative path breaks the
moment this module is called from `00_all_at_once` one level up. `path.module` is
always *this* directory, in both modes — which is what lets the same code run
standalone and composed.

`seed_objects` maps three S3 keys to three local paths, and is what
`aws_s3_object.seed` iterates.

---

## Resources

### `aws_s3_bucket.hr`
The bucket. Nothing to configure — name and tags.

### `aws_s3_bucket_public_access_block.hr`
All four blocks on: `block_public_acls`, `block_public_policy`,
`ignore_public_acls`, `restrict_public_buckets`. Employee records. This is not a
website bucket, and nothing needs to reach it without credentials.

Separate resource rather than an argument because AWS models it as a separate
API. That is also why it can be applied to a bucket someone else created.

### `aws_s3_bucket_versioning.hr`
`status = "Enabled"`. An employee record is a thing people argue about.
Versioning is what lets you answer *"what did the record say when we scored
her?"* — which is a real question the first time a shortlist decision is
challenged.

### `aws_s3_bucket_server_side_encryption_configuration.hr`
`AES256` (SSE-S3). Encryption at rest with no key to manage. KMS would be the
upgrade if you need per-key audit trails or cross-account grants; it also adds a
`kms:Decrypt` grant to every role that reads the bucket, including all five
runtime roles in [`05_runtimes`](../05_runtimes/).

### `aws_s3_object.seed` **×3**
`for_each` over `local.seed_objects` — `employees/`, `requisitions/`, `skills/`.

```hcl
etag = filemd5(each.value)
```

**Without `etag` this is a no-op on every re-apply.** Terraform compares only the
`source` *path*, which does not change when you re-run `seed_s3.py` — so edited
records would never reach the bucket and the plan would say `No changes`.

> `etag` is right *here* and wrong in [`05_runtimes`](../05_runtimes/), for a
> reason worth knowing: these files are small enough for a single-part upload, so
> the S3 etag really is the MD5. The 34 MB runtime zips upload multipart, where
> the etag is `<md5-of-part-md5s>-<partcount>` and can never equal `filemd5()` —
> giving you a permanent five-object diff. That module puts the hash in the
> object *key* instead.

---

## Outputs

| Output | Consumed by |
|---|---|
| `bucket` | [`02_lambda`](../02_lambda/), [`05_runtimes`](../05_runtimes/), and `S3_BUCKET` in `.env` |
| `bucket_arn` | [`02_lambda`](../02_lambda/), [`05_runtimes`](../05_runtimes/) — both scope IAM with it |

---

## Things worth knowing

**Artifacts must exist before `plan`, not before `apply`.** `filemd5()` is
evaluated at plan time. Run `uv run scripts/seed_s3.py` (or `make artifacts`)
first, or the plan fails on a missing file.

**Two places point at `seed/`.** This module's `seed_dir` and
`app/_shared/config.py`. Move the directory and you must move both, or the apply
uploads whatever was there last.

**Who writes what, once the whole stack is up:**

| Prefix | Written by | Read by |
|---|---|---|
| `employees/`, `requisitions/`, `skills/` | terraform, from `seed/` | all five runtimes (`s3:GetObject` only) |
| `shortlists/` | `hr-data-fn` in [`02_lambda`](../02_lambda/) | `hr-data-fn` |
| `artifacts/` | [`05_runtimes`](../05_runtimes/) | the runtimes, at container start |

No runtime can write anywhere in this bucket. That is enforced in
[`05_runtimes`](../05_runtimes/)'s policy, not here — a scoring engine that can
edit the employee record is a scoring engine nobody will trust.

**`terraform destroy` will fail while the bucket has objects in it** that
terraform does not manage — the shortlists the Lambda wrote, and old artifacts
from previous deploys. Empty it first (`aws s3 rm s3://<bucket> --recursive`) or
add `force_destroy = true` here. It is deliberately absent: this bucket holds
data, unlike the UI bucket in [`08_ui`](../08_ui/) which holds a build output and
does set it.
