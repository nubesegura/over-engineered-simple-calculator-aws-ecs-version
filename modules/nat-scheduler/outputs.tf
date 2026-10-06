output "function_name" {
  value       = aws_lambda_function.nat_scheduler.function_name
  description = "Name of the NAT scheduler function (invoked with on before anything that needs egress)"
}

output "function_arn" {
  value       = aws_lambda_function.nat_scheduler.arn
  description = "ARN of the NAT scheduler function"
}

output "nat_name_tag" {
  value       = local.nat_name
  description = "Name tag of the NAT gateway created by the function"
}
