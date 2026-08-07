terraform {
    backend "s3" {
        key        = "terraform/tfstate.tfstate"
        bucket     = "demo-remote-backend-2026"
        region     = "us-west-2"
        encrypt        = true
        use_lockfile   = true # Enables native S3 state locking
        # access_key = "your_access_key"
        # secret_key = "your_secret_key"
    }
}