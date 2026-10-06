output "repository_urls" {
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
  description = "Repository URL of each image, keyed by service name"
}

output "repository_arns" {
  value       = { for k, r in aws_ecr_repository.service : k => r.arn }
  description = "Repository ARN of each image, keyed by service name"
}

output "repository_names" {
  value       = { for k, r in aws_ecr_repository.service : k => r.name }
  description = "Repository name of each image, keyed by service name"
}
