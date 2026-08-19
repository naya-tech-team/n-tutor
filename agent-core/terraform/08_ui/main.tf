# 08 — the browser's half: a private S3 bucket and one CloudFront distribution.
#
# The shape here is the whole lesson. Two origins under one domain:
#
#     /*        -> S3, the React build      (OAC-signed, bucket stays private)
#     /api/*    -> the API Gateway from 07  (Cognito authorizer on /api/chat)
#
# Serving the API from the same distribution as the page is not a convenience.
# It is what removes CORS entirely — there is no second origin for the browser to
# be told about, so there is no preflight, no Access-Control-Allow-Origin, and no
# credentials-in-a-cross-origin-request problem. Split them and you spend an
# afternoon on headers instead.
#
# Which is why this layer exists separately from 07 but is still *in front* of it:
# 07 is an API anything can call, and this makes one of those callers a web page
# without giving it a cross-origin problem.
#
#   terraform apply -var api_domain=... -var api_stage=v1

terraform {
    required_providers {
        aws = {
            source  = "hashicorp/aws"
            version = ">= 6.58"
        }
    }
}

variable "region" {
    type    = string
    default = "us-west-2"
}

variable "env" {
    type    = string
    default = "dev"
}

variable "api_domain" {
    type        = string
    description = "terraform output -raw api_domain, from 07_api. The execute-api host, with no scheme and no path."
}

variable "api_stage" {
    type        = string
    description = "terraform output -raw api_stage, from 07_api. Becomes the origin_path, which is what keeps the stage out of the browser's URLs."
}

variable "ui_dist_dir" {
    type        = string
    description = "The vite build, relative to this module. Built by `make ui-build`."
    default     = "../../ui/dist"
}

variable "price_class" {
    type        = string
    description = "PriceClass_100 is North America + Europe and the cheapest. _All adds the rest of the world."
    default     = "PriceClass_100"
}

variable "origin_read_timeout" {
    type        = number
    description = <<-EOT
        How long CloudFront waits between bytes from the API. The default is 30s,
        and the supervisor is regularly silent for longer than that while a
        delegation runs — which cuts the stream mid-answer and looks exactly like
        the agent crashed.

        60 is the maximum without a quota increase. The `start` and `status`
        frames exist partly to keep the gap under it.
    EOT
    default     = 60
}

locals {
    common_tags = {
        Project = "ai-agent-platform"
        Env     = var.env
        Track   = "agent-core"
    }

    # path.module cannot appear in a variable default — defaults must be literals
    # — so the variable holds the relative part and this joins it. path.module is
    # this directory in both modes, which is what makes the module work standalone
    # AND as a child of 00_all_at_once.
    dist = "${path.module}/${var.ui_dist_dir}"

    # S3 serves what this says, and a wrong value here does not error — the
    # browser just refuses to execute a stylesheet delivered as text/plain, and
    # the page renders unstyled with a console warning most people never read.
    mime = {
        html  = "text/html"
        js    = "text/javascript"
        css   = "text/css"
        svg   = "image/svg+xml"
        png   = "image/png"
        jpg   = "image/jpeg"
        ico   = "image/x-icon"
        json  = "application/json"
        webp  = "image/webp"
        woff2 = "font/woff2"
        map   = "application/json"
    }

    files = fileset(local.dist, "**")
}

data "aws_caller_identity" "current" {}

# --- the bucket -------------------------------------------------------------
# Private. Nothing is public-read; CloudFront reaches it with a signed request.

resource "aws_s3_bucket" "site" {
    bucket        = "hr-chat-ui-${var.env}-${data.aws_caller_identity.current.account_id}"
    force_destroy = true # it holds a build output, not data

    tags = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "site" {
    bucket = aws_s3_bucket.site.id

    block_public_acls       = true
    block_public_policy     = true
    ignore_public_acls      = true
    restrict_public_buckets = true
}

# Uploaded by terraform rather than by `aws s3 sync`, so that `terraform apply` is
# the whole deploy and `terraform destroy` actually empties the bucket.
#
# filemd5 is evaluated at PLAN time, so `ui/dist` has to exist before you go near
# terraform — same rule as the runtime zips. `make ui-build` is what does it, and
# `make plan` chains it for you.
resource "aws_s3_object" "site" {
    for_each = local.files

    bucket = aws_s3_bucket.site.id
    key    = each.value
    source = "${local.dist}/${each.value}"
    etag   = filemd5("${local.dist}/${each.value}")

    content_type = lookup(local.mime, reverse(split(".", each.value))[0], "application/octet-stream")

    # Vite fingerprints everything under assets/, so those can be cached for a
    # year. index.html cannot: it is the file that names the new fingerprints, and
    # a cached one keeps pointing at the previous deploy's assets.
    cache_control = startswith(each.value, "assets/") ? "public, max-age=31536000, immutable" : "no-cache"

    tags = local.common_tags
}

# --- origin access ----------------------------------------------------------
# Only S3 gets an OAC. CloudFront OAC has no apigateway origin type, so the API
# origin is reached unsigned and its Cognito authorizer is what protects it.

resource "aws_cloudfront_origin_access_control" "s3" {
    name                              = "hr-chat-s3-${var.env}"
    origin_access_control_origin_type = "s3"
    signing_behavior                  = "always"
    signing_protocol                  = "sigv4"
}

data "aws_iam_policy_document" "bucket" {
    statement {
        sid       = "AllowCloudFrontRead"
        effect    = "Allow"
        actions   = ["s3:GetObject"]
        resources = ["${aws_s3_bucket.site.arn}/*"]

        principals {
            type        = "Service"
            identifiers = ["cloudfront.amazonaws.com"]
        }

        # Without this the bucket is readable by *any* CloudFront distribution in
        # any account, which is a much larger door than it looks.
        condition {
            test     = "StringEquals"
            variable = "AWS:SourceArn"
            values   = [aws_cloudfront_distribution.site.arn]
        }
    }
}

resource "aws_s3_bucket_policy" "site" {
    bucket = aws_s3_bucket.site.id
    policy = data.aws_iam_policy_document.bucket.json
}

# --- the distribution -------------------------------------------------------

data "aws_cloudfront_cache_policy" "optimized" {
    name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "disabled" {
    name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
    # Not "AllViewer". API Gateway routes on the Host header, so forwarding the
    # viewer's makes every request a 403 that reads like a permissions problem.
    name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "site" {
    enabled             = true
    default_root_object = "index.html"
    comment             = "HR hiring desk chat UI (${var.env})"
    price_class         = var.price_class

    origin {
        origin_id                = "s3"
        domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
        origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
    }

    origin {
        origin_id   = "api"
        domain_name = var.api_domain

        # The stage is a path segment on execute-api, so without this the origin
        # would be asked for `/api/chat` and the API only answers at
        # `/v1/api/chat`. Putting it here rather than in the React is what keeps
        # the stage invisible to the browser — and keeps `npm run dev`, which has
        # no stage at all, calling the same paths.
        origin_path = "/${var.api_stage}"

        custom_origin_config {
            http_port              = 80
            https_port             = 443
            origin_protocol_policy = "https-only"
            origin_ssl_protocols   = ["TLSv1.2"]

            origin_read_timeout = var.origin_read_timeout
        }
    }

    default_cache_behavior {
        target_origin_id       = "s3"
        viewer_protocol_policy = "redirect-to-https"
        allowed_methods        = ["GET", "HEAD", "OPTIONS"]
        cached_methods         = ["GET", "HEAD"]
        cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
        compress               = true
    }

    ordered_cache_behavior {
        path_pattern           = "/api/*"
        target_origin_id       = "api"
        viewer_protocol_policy = "https-only"
        allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
        cached_methods         = ["GET", "HEAD"]

        cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
        origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id

        # Off deliberately. CloudFront buffers in order to compress, which holds
        # the SSE frames back until the response ends — the exact failure this
        # whole design is built to avoid, arriving via a setting that looks like a
        # performance tweak.
        compress = false
    }

    # The SPA fallback. With OAC and no s3:ListBucket, a missing key comes back as
    # 403 rather than 404, so both have to be mapped or a page refresh on any
    # route but / shows CloudFront's own error page.
    #
    # This is distribution-wide, which is why ui/proxy/index.mjs answers an unknown
    # route with 400 rather than 404 — a 404 from the API would be rewritten into
    # the HTML page and arrive at fetch() as a JSON parse error.
    custom_error_response {
        error_code            = 403
        response_code         = 200
        response_page_path    = "/index.html"
        error_caching_min_ttl = 0
    }

    custom_error_response {
        error_code            = 404
        response_code         = 200
        response_page_path    = "/index.html"
        error_caching_min_ttl = 0
    }

    restrictions {
        geo_restriction {
            restriction_type = "none"
        }
    }

    viewer_certificate {
        cloudfront_default_certificate = true
    }

    tags = local.common_tags
}

output "chat_url" {
    value       = "https://${aws_cloudfront_distribution.site.domain_name}"
    description = "Open this. Sign in with the Cognito user from 03_gateway."
}

output "distribution_id" {
    value       = aws_cloudfront_distribution.site.id
    description = "For `aws cloudfront create-invalidation` after a UI-only change."
}

output "ui_bucket" {
    value = aws_s3_bucket.site.bucket
}
