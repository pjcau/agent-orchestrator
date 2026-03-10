# ============================================================
# S3 Module — Jobs Archive Bucket
# ============================================================

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# --- Jobs Archive Bucket ---
resource "aws_s3_bucket" "jobs_archive" {
  bucket = "${var.project}-jobs-archive"

  tags = {
    Project = var.project
  }
}

# --- Block all public access ---
resource "aws_s3_bucket_public_access_block" "jobs_archive" {
  bucket = aws_s3_bucket.jobs_archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Server-side encryption (AES256) ---
resource "aws_s3_bucket_server_side_encryption_configuration" "jobs_archive" {
  bucket = aws_s3_bucket.jobs_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# --- Versioning disabled (archives only) ---
resource "aws_s3_bucket_versioning" "jobs_archive" {
  bucket = aws_s3_bucket.jobs_archive.id

  versioning_configuration {
    status = "Disabled"
  }
}

# --- Request Metrics (enables CloudWatch S3 metrics for monitoring) ---
resource "aws_s3_bucket_metric" "entire_bucket" {
  bucket = aws_s3_bucket.jobs_archive.id
  name   = "EntireBucket"
  # No filter = entire bucket metrics
}

# --- Lifecycle: Glacier after 90 days, delete after 365 days ---
resource "aws_s3_bucket_lifecycle_configuration" "jobs_archive" {
  bucket = aws_s3_bucket.jobs_archive.id

  rule {
    id     = "archive-to-glacier"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = 365
    }
  }
}
