# Shared regional WAF ACL (row 33, ELB-11): prod only, when its ARN is provided.
resource "aws_wafv2_web_acl_association" "api" {
  count = var.enable_waf && var.waf_acl_arn != "" ? 1 : 0

  resource_arn = aws_lb.api.arn
  web_acl_arn  = var.waf_acl_arn
}

check "waf_acl_provided" {
  assert {
    condition     = !var.enable_waf || var.waf_acl_arn != ""
    error_message = "enable_waf is true but WAF_ACL_ARN is empty: the load balancer would be exposed without the shared WAF ACL (ELB-11)."
  }
}
