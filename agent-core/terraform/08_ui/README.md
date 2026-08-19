# 08_ui — the React build on CloudFront

A private S3 bucket and one CloudFront distribution with **two origins**.

**6 resource blocks (one multiplied per built file) · 5 data sources ·
7 variables (2 required) · 3 outputs.**

```bash
make ui-build                             # -> ui/dist, FIRST
terraform apply \
  -var api_domain=$(cd ../07_api && terraform output -raw api_domain) \
  -var api_stage=$(cd ../07_api && terraform output -raw api_stage)
terraform output -raw chat_url            # open this
```

---

## The shape here is the whole lesson

```
/*        ->  S3, the React build       (OAC-signed, bucket stays private)
/api/*    ->  the API Gateway from 07   (Cognito authorizer on /api/chat)
```

Serving the API from the **same distribution** as the page is not a convenience.
**It is what removes CORS entirely** — there is no second origin for the browser
to be told about, so there is no preflight, no `Access-Control-Allow-Origin`, and
no credentials-in-a-cross-origin-request problem. Split them and you spend an
afternoon on headers instead.

Which is why this layer is separate from [`07_api`](../07_api/) but still *in
front of* it: 07 is an API anything can call, and this makes one of those callers
a web page without giving it a cross-origin problem.

---

## Variables

| Variable | Type | Default | Notes |
|---|---|---|---|
| `api_domain` | string | **required** | From [`07_api`](../07_api/). The bare `execute-api` host — **no scheme, no path** |
| `api_stage` | string | **required** | From [`07_api`](../07_api/). Becomes `origin_path` |
| `ui_dist_dir` | string | `../../ui/dist` | The vite build, relative to this module |
| `price_class` | string | `PriceClass_100` | North America + Europe, the cheapest. `_All` adds the rest of the world |
| `origin_read_timeout` | number | `60` | **Raised from the default 30 on purpose** — see below |
| `region`, `env` | string | | Tags; `env` suffixes the bucket and OAC names |

### `origin_read_timeout` — 30 is not enough, and the failure looks like a crash

This is how long CloudFront waits **between bytes** from the API. The default is
30s, and the supervisor is regularly silent for longer than that while a
delegation runs — which cuts the stream mid-answer and looks exactly like the
agent died.

**60 is the maximum without a quota increase.** The `start` and `status` frames
the proxy emits exist partly to keep the gap under it.

---

## Locals

### `mime` — a lookup table, because S3 serves what you tell it to

```hcl
content_type = lookup(local.mime, reverse(split(".", each.value))[0], "application/octet-stream")
```

**A wrong value here does not error.** The browser simply refuses to execute a
stylesheet delivered as `text/plain`, and the page renders unstyled with a
console warning most people never read.

`reverse(split(...))[0]` takes the last extension, which is what makes
`index-a1b2c3.js` and `app.css.map` both resolve correctly.

### `files` and `dist`

```hcl
dist  = "${path.module}/${var.ui_dist_dir}"
files = fileset(local.dist, "**")
```

`path.module`, not a bare relative path — same rule as every other module, and
it cannot go in the variable default because defaults must be literals.

**`fileset()` and `filemd5()` are evaluated at PLAN time**, so `ui/dist` must
exist before you go near Terraform. A missing build fails with
`call to function fileset failed`, which does not sound like *"you forgot to
build the UI"*. `make ui-build` does it; `make plan` chains it.

---

## Data sources

| Data source | Why |
|---|---|
| `aws_caller_identity.current` | Account id in the bucket name — S3 names are globally unique |
| `aws_iam_policy_document.bucket` | The OAC bucket policy |
| `aws_cloudfront_cache_policy.optimized` | `Managed-CachingOptimized`, for the static build |
| `aws_cloudfront_cache_policy.disabled` | `Managed-CachingDisabled`, for `/api/*` |
| `aws_cloudfront_origin_request_policy.all_viewer_except_host` | `Managed-AllViewerExceptHostHeader` |

The last three are **AWS-managed policies looked up by name rather than by
hardcoded id.** The ids are stable but opaque; the names are readable and
reviewable in a diff.

> `Managed-AllViewerExceptHostHeader`, **not** `Managed-AllViewer`. API Gateway
> routes on the `Host` header, so forwarding the viewer's makes every request a
> **403 that reads like a permissions problem.**

---

## Resources

### `aws_s3_bucket.site` + `aws_s3_bucket_public_access_block.site`

Private. Nothing is public-read; CloudFront reaches it with a signed request.

```hcl
force_destroy = true   # it holds a build output, not data
```

Deliberately different from [`01_s3_data`](../01_s3_data/), which does **not**
set it — that bucket holds employee records, and a `destroy` that silently
empties it is not a behaviour you want.

### `aws_s3_object.site` ×N — one per built file

Uploaded by Terraform rather than by `aws s3 sync`, so that **`terraform apply`
is the whole deploy** and `terraform destroy` actually empties the bucket.

```hcl
cache_control = startswith(each.value, "assets/")
  ? "public, max-age=31536000, immutable"
  : "no-cache"
```

Vite fingerprints everything under `assets/`, so those can be cached for a year.
**`index.html` cannot**: it is the file that *names* the new fingerprints, and a
cached one keeps pointing at the previous deploy's assets — a stale page that
loads no longer-existing JavaScript.

`etag = filemd5(...)` is correct here (unlike in [`05_runtimes`](../05_runtimes/))
because these files are small enough for single-part uploads, where the S3 etag
really is the MD5.

### `aws_cloudfront_origin_access_control.s3`

```hcl
origin_access_control_origin_type = "s3"
signing_behavior                  = "always"
signing_protocol                  = "sigv4"
```

**Only S3 gets an OAC.** CloudFront OAC has no `apigateway` origin type, so the
API origin is reached unsigned and its Cognito authorizer is what protects it.
That is the trade [`07_api`](../07_api/) makes explicitly.

### `aws_s3_bucket_policy.site`

```hcl
principals { type = "Service", identifiers = ["cloudfront.amazonaws.com"] }
condition  { test = "StringEquals", variable = "AWS:SourceArn",
             values = [aws_cloudfront_distribution.site.arn] }
```

**Without that condition the bucket is readable by *any* CloudFront distribution
in *any* account**, which is a much larger door than it looks. The service
principal alone is not a boundary.

Note the reference to the distribution's ARN: Terraform resolves the cycle
because the distribution does not reference the bucket policy, only the bucket.

### `aws_cloudfront_distribution.site`

#### The two origins

| `origin_id` | Points at | Notes |
|---|---|---|
| `s3` | `bucket_regional_domain_name` | Signed by the OAC |
| `api` | `var.api_domain` | `origin_path = "/${var.api_stage}"`, `https-only`, TLSv1.2 |

**`origin_path` is what keeps the stage invisible.** The stage is a path segment
on `execute-api`, so without it the origin would be asked for `/api/chat` while
the API only answers at `/v1/api/chat`. Putting it here rather than in the React
also keeps `npm run dev` — which has no stage at all — calling the same paths.

#### The two behaviours

| Behaviour | Origin | Cache policy | Notes |
|---|---|---|---|
| `default_cache_behavior` | `s3` | `Managed-CachingOptimized` | `redirect-to-https`, `compress = true` |
| `ordered_cache_behavior` `/api/*` | `api` | `Managed-CachingDisabled` | All methods, `compress = false` |

```hcl
compress = false   # on /api/* — deliberately
```

**CloudFront buffers in order to compress**, which holds the SSE frames back
until the response ends — the exact failure this whole design is built to avoid,
arriving via a setting that looks like a performance tweak.

#### `custom_error_response` ×2 — the SPA fallback

```hcl
403 -> 200 /index.html
404 -> 200 /index.html
```

**Both are required.** With OAC and no `s3:ListBucket`, a missing key comes back
as **403 rather than 404** — so mapping only 404 leaves a page refresh on any
route but `/` showing CloudFront's own error page.

This is **distribution-wide**, which is why `ui/proxy/index.mjs` answers an
unknown route with **400 rather than 404**: a 404 from the API would be rewritten
into the HTML page and arrive at `fetch()` as a JSON parse error.

#### The rest

`viewer_certificate { cloudfront_default_certificate = true }` — the
`*.cloudfront.net` domain. A custom domain needs ACM **in us-east-1** plus an
`aliases` block. `geo_restriction` is `none`.

---

## Outputs

| Output | Notes |
|---|---|
| `chat_url` | Open this. Sign in with the Cognito user from [`03_gateway`](../03_gateway/) |
| `distribution_id` | For `aws cloudfront create-invalidation` after a UI-only change |
| `ui_bucket` | |

```bash
aws cloudfront create-invalidation --distribution-id $(terraform output -raw distribution_id) --paths '/*'
```

Needed because `index.html` is `no-cache` but CloudFront may still be holding
it — and because a redeploy that changes only `index.html` produces no new
fingerprinted assets to bust the cache for you.

---

## Things that bite

| Symptom | Cause |
|---|---|
| `call to function fileset failed` | `ui/dist` does not exist. Run `make ui-build` |
| Page loads unstyled | A `content_type` fell through to `application/octet-stream` — add the extension to `local.mime` |
| Every `/api/*` call is 403 | `Managed-AllViewer` instead of `Managed-AllViewerExceptHostHeader` |
| `/api/*` returns 403 from API Gateway itself | `origin_path` missing or the wrong stage |
| Answer arrives all at once at the end | `compress = true` on the `/api/*` behaviour |
| Stream cut off mid-answer | `origin_read_timeout` back at the 30s default |
| Refresh on `/anything` shows a CloudFront error page | One of the two `custom_error_response` blocks is missing |
| Old assets served after a deploy | `index.html` was cached. Invalidate |
| New JS deployed, page still runs the old build | `cache_control` on `assets/` is right, but `index.html` picked up `immutable` — check the `startswith` |

## Developing the React against the deployed backend

```bash
make ui-dev API_ORIGIN=https://d111111abcdef8.cloudfront.net
```

Vite proxies `/api/*` to the deployed distribution, so you get hot reload against
real agents. `make ui-api` + `make ui-dev` with no `API_ORIGIN` runs entirely
locally — the local proxy accepts any login and there is no AWS in the loop.
