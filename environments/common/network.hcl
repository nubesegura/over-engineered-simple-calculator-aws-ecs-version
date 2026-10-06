# VPC, subnets, route tables, S3 endpoint, security groups and the NAT Elastic IP
# (rows 1 to 4 and 6 to 14). The private default route and the NAT gateway belong to
# the scheduler unit.
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
}

terraform {
  source = "${get_repo_root()}/modules/network"
}

# The flow log group (prod) is encrypted with the environment key.
dependency "security" {
  config_path = "../security"
  mock_outputs = {
    kms_key_arn     = "arn:aws:kms:us-east-2:111122223333:key/mock-kms-key"
    kms_key_alias   = "alias/mock-kms-key"
    alert_topic_arn = "arn:aws:sns:us-east-2:111122223333:mock-topic"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

inputs = {
  vpc_cidr           = local.env_vars.locals.vpc_cidr
  enable_flow_logs   = local.env_vars.locals.enable_flow_logs
  log_retention_days = local.env_vars.locals.log_retention_days
  kms_key_arn        = dependency.security.outputs.kms_key_arn
}
