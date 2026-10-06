data "aws_region" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  name_mid = "${replace(data.aws_region.current.region, "-", "")}-${var.context}"

  # Order matters: it fixes the rule priorities (see rules.tf).
  services = ["add", "sub", "mul", "div", "history"]
}

# Certificate ARN published by the webpage repository (owner of the DNS zone).
data "aws_ssm_parameter" "certificate" {
  name = "/${var.context}/${var.env_type}/api-certificate-arn"

  lifecycle {
    postcondition {
      condition     = length(trimspace(self.insecure_value)) > 0
      error_message = "SSM parameter /${var.context}/${var.env_type}/api-certificate-arn is empty: the webpage repository must publish the API certificate ARN before the edge unit can be planned."
    }
  }
}

# =========================================================
# Load balancer (row 29)
# =========================================================
resource "aws_lb" "api" {
  name                       = "alb-${local.name_mid}-api-${var.env_type}"
  load_balancer_type         = "application"
  internal                   = false
  ip_address_type            = "ipv4"
  subnets                    = var.public_subnet_ids
  security_groups            = [var.alb_security_group_id]
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection

  access_logs {
    enabled = true
    bucket  = var.log_bucket_id
    prefix  = var.log_prefix
  }
}

# =========================================================
# Listeners (row 30)
# =========================================================
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = data.aws_ssm_parameter.certificate.insecure_value

  # Anything that matches no service rule is a 404; no default forward.
  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      message_body = jsonencode({ error = { code = "NOT_FOUND", message = "Not found" } })
      status_code  = "404"
    }
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# =========================================================
# Target groups (row 31): HTTPS to the tasks, health route without token
# =========================================================
resource "aws_lb_target_group" "svc" {
  for_each = toset(local.services)

  name                 = "tg-${local.name_mid}-${each.key}-${var.env_type}"
  target_type          = "ip"
  protocol             = "HTTPS"
  protocol_version     = "HTTP1"
  port                 = var.task_port
  vpc_id               = var.vpc_id
  deregistration_delay = 30

  health_check {
    enabled             = true
    protocol            = "HTTPS"
    path                = "/health"
    port                = "traffic-port"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}
