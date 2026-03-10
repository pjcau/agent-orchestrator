variable "project" {
  description = "Project name for resource tagging"
  type        = string
}

variable "jobs_archive_bucket_arn" {
  description = "ARN of the S3 jobs archive bucket (used to scope S3 permissions)"
  type        = string
}
