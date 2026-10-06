# Load balancer, listeners, target groups, token rules, WAF association and published
# target (rows 29 to 34).
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
}

terraform {
  source = "${get_repo_root()}/modules/edge"
}

dependency "network" {
  config_path = "../network"
  mock_outputs = {
    vpc_id                = "vpc-00000000000000000"
    public_subnet_ids     = ["subnet-00000000000000001", "subnet-00000000000000002"]
    private_subnet_ids    = ["subnet-00000000000000011", "subnet-00000000000000012"]
    alb_security_group_id = "sg-00000000000000000"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

dependency "storage" {
  config_path = "../storage"
  mock_outputs = {
    alb_log_bucket_id = "mock-alb-log-bucket"
    alb_log_prefix    = "alb"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

inputs = {
  vpc_id                = dependency.network.outputs.vpc_id
  public_subnet_ids     = dependency.network.outputs.public_subnet_ids
  alb_security_group_id = dependency.network.outputs.alb_security_group_id
  log_bucket_id         = dependency.storage.outputs.alb_log_bucket_id
  log_prefix            = dependency.storage.outputs.alb_log_prefix
  deletion_protection   = local.env_vars.locals.alb_deletion_protection
  cognito_user_pool_id  = local.env_vars.locals.cognito_user_pool_id
  cognito_app_client_id = local.env_vars.locals.cognito_app_client_id
  enable_waf            = local.env_vars.locals.enable_waf
  waf_acl_arn           = local.env_vars.locals.waf_acl_arn
}
