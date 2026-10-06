output "app_secret_arn" {
  value       = aws_secretsmanager_secret.app.arn
  description = "ARN of the application secret"
}

output "app_secret_name" {
  value       = aws_secretsmanager_secret.app.name
  description = "Name of the application secret"
}

output "function_role_arn" {
  value       = aws_iam_role.function.arn
  description = "ARN of the rotation function role"
}

output "function_name" {
  value       = aws_lambda_function.rotation.function_name
  description = "Name of the rotation function"
}
