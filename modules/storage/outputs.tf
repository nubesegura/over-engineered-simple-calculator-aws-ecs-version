output "ingest_bucket_id" {
  value       = aws_s3_bucket.ingest.id
  description = "Name of the CSV ingestion bucket"
}

output "ingest_bucket_arn" {
  value       = aws_s3_bucket.ingest.arn
  description = "ARN of the CSV ingestion bucket"
}

output "alb_log_bucket_id" {
  value       = aws_s3_bucket.alb_logs.id
  description = "Name of the load balancer access log bucket"
}

output "alb_log_bucket_arn" {
  value       = aws_s3_bucket.alb_logs.arn
  description = "ARN of the load balancer access log bucket"
}

output "alb_log_prefix" {
  value       = var.alb_log_prefix
  description = "Prefix the load balancer writes its access logs under"
}
