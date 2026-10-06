output "task_definition_arn" {
  value       = aws_ecs_task_definition.ingest.arn
  description = "ARN of the ingest task definition (with revision)"
}

output "task_role_arn" {
  value       = aws_iam_role.task.arn
  description = "ARN of the ingest task role"
}

output "exec_role_arn" {
  value       = aws_iam_role.exec.arn
  description = "ARN of the ingest execution role"
}

output "log_group_name" {
  value       = aws_cloudwatch_log_group.ingest.name
  description = "Name of the ingest log group"
}

output "ingest_rule_arn" {
  value       = aws_cloudwatch_event_rule.ingest.arn
  description = "ARN of the rule that starts the job when a CSV arrives"
}

output "task_failed_rule_arn" {
  value       = aws_cloudwatch_event_rule.task_failed.arn
  description = "ARN of the rule that alerts when a task of the cluster stops with a non-zero exit code"
}
