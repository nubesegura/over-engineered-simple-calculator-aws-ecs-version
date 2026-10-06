# Listener rules (row 32). Two rules per service on the HTTPS listener:
#   preflight  (priority 10..50):   OPTIONS on the service paths, forward without token
#   token      (priority 110..150): any method on the same paths, jwt-validation then forward
# Every preflight priority is lower than every token priority, so a CORS preflight is
# never challenged for a token.
locals {
  issuer        = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${var.cognito_user_pool_id}"
  jwks_endpoint = "${local.issuer}/.well-known/jwks.json"

  service_paths = {
    for s in local.services : s => ["/api/v1/${s}", "/api/ecs/v1/${s}"]
  }
  service_index = { for i, s in local.services : s => i + 1 }
}

resource "aws_lb_listener_rule" "preflight" {
  for_each = toset(local.services)

  listener_arn = aws_lb_listener.https.arn
  priority     = local.service_index[each.key] * 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.svc[each.key].arn
  }

  condition {
    http_request_method {
      values = ["OPTIONS"]
    }
  }

  condition {
    path_pattern {
      values = local.service_paths[each.key]
    }
  }
}

resource "aws_lb_listener_rule" "token" {
  for_each = toset(local.services)

  listener_arn = aws_lb_listener.https.arn
  priority     = 100 + local.service_index[each.key] * 10

  action {
    type  = "jwt-validation"
    order = 1

    jwt_validation {
      issuer        = local.issuer
      jwks_endpoint = local.jwks_endpoint

      additional_claim {
        format = "single-string"
        name   = "aud"
        values = [var.cognito_app_client_id]
      }

      additional_claim {
        format = "single-string"
        name   = "token_use"
        values = ["id"]
      }
    }
  }

  action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.svc[each.key].arn
  }

  condition {
    path_pattern {
      values = local.service_paths[each.key]
    }
  }
}
