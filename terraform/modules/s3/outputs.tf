output "bucket_name" {
  value = aws_s3_bucket.jobs_archive.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.jobs_archive.arn
}
