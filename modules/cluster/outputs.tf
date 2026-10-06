output "cluster_name" {
  value       = aws_ecs_cluster.main.name
  description = "Name of the ECS cluster"
}

output "cluster_arn" {
  value       = aws_ecs_cluster.main.arn
  description = "ARN of the ECS cluster"
}

output "migrate_task_definition_arn" {
  value       = aws_ecs_task_definition.migrate.arn
  description = "ARN of the migrate task definition (with revision)"
}

output "migrate_task_definition_family" {
  value       = aws_ecs_task_definition.migrate.family
  description = "Family of the migrate task definition"
}

output "migrate_task_role_arn" {
  value       = aws_iam_role.migrate_task.arn
  description = "ARN of the migrate task role"
}

output "migrate_exec_role_arn" {
  value       = aws_iam_role.migrate_exec.arn
  description = "ARN of the migrate execution role"
}

output "migrate_log_group_name" {
  value       = aws_cloudwatch_log_group.migrate.name
  description = "Name of the migrate log group"
}

output "migrate_subnet_ids" {
  value       = var.job_subnet_ids
  description = "Private subnets for the run-task call of the migration"
}

output "migrate_security_group_id" {
  value       = var.job_security_group_id
  description = "Jobs security group for the run-task call of the migration"
}
