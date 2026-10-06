output "kms_key_arn" {
  value       = aws_kms_key.main.arn
  description = "ARN of the environment KMS key"
}

output "kms_key_alias" {
  value       = aws_kms_alias.main.name
  description = "Alias of the environment KMS key"
}

output "alert_topic_arn" {
  value       = aws_sns_topic.alerts.arn
  description = "ARN of the alerts SNS topic"
}
