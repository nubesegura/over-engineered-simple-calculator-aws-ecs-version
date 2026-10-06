output "service_names" {
  value       = { for s, v in aws_ecs_service.svc : s => v.name }
  description = "ECS service name by service"
}

output "task_definition_arns" {
  value       = { for s, v in aws_ecs_task_definition.svc : s => v.arn }
  description = "Task definition ARN (with revision) by service"
}

output "task_role_arns" {
  value       = { for s, v in aws_iam_role.task : s => v.arn }
  description = "Task role ARN by service"
}

output "exec_role_arns" {
  value       = { for s, v in aws_iam_role.exec : s => v.arn }
  description = "Execution role ARN by service"
}

output "log_group_names" {
  value       = { for s, v in aws_cloudwatch_log_group.svc : s => v.name }
  description = "Log group name by service"
}
