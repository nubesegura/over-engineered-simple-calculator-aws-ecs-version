output "alb_arn" {
  value       = aws_lb.api.arn
  description = "ARN of the load balancer"
}

output "alb_dns_name" {
  value       = aws_lb.api.dns_name
  description = "DNS name of the load balancer"
}

output "alb_zone_id" {
  value       = aws_lb.api.zone_id
  description = "Canonical hosted zone ID of the load balancer"
}

output "alb_security_group_id" {
  value       = var.alb_security_group_id
  description = "ID of the load balancer security group"
}

output "target_group_arns" {
  value       = { for s, tg in aws_lb_target_group.svc : s => tg.arn }
  description = "Target group ARN by service (add, sub, mul, div, history)"
}

output "https_listener_arn" {
  value       = aws_lb_listener.https.arn
  description = "ARN of the HTTPS listener"
}
