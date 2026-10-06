# Target published for the webpage repository (row 34): weighted alias record.
resource "aws_ssm_parameter" "dns_name" {
  name        = "/${var.context}/${var.env_type}/api-backends/ecs/dns-name"
  type        = "String"
  tier        = "Standard"
  description = "DNS name of the ECS API load balancer"
  value       = aws_lb.api.dns_name
}

resource "aws_ssm_parameter" "hosted_zone_id" {
  name        = "/${var.context}/${var.env_type}/api-backends/ecs/hosted-zone-id"
  type        = "String"
  tier        = "Standard"
  description = "Canonical hosted zone ID of the ECS API load balancer"
  value       = aws_lb.api.zone_id
}
