output "endpoint_address" {
  value       = aws_db_instance.main.address
  description = "DNS name of the database instance"
}

output "port" {
  value       = aws_db_instance.main.port
  description = "Database port"
}

output "db_name" {
  value       = aws_db_instance.main.db_name
  description = "Name of the database"
}

output "master_secret_arn" {
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
  description = "ARN of the master secret created and rotated by RDS"
}

output "instance_identifier" {
  value       = aws_db_instance.main.identifier
  description = "Identifier of the database instance"
}
