# NAT scheduler: function, roles, schedules, invocation at apply and destroy, error alarm
# (rows 38 to 40). The NAT gateway and the private default route are created by the
# function, never by Terraform.
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
}

terraform {
  source = "${get_repo_root()}/modules/nat-scheduler"
}

dependency "network" {
  config_path = "../network"
  mock_outputs = {
    nat_public_subnet_id   = "subnet-00000000000000000"
    private_route_table_id = "rtb-00000000000000000"
    nat_eip_allocation_id  = "eipalloc-00000000000000000"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

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
  public_subnet_id       = dependency.network.outputs.nat_public_subnet_id
  private_route_table_id = dependency.network.outputs.private_route_table_id
  eip_allocation_id      = dependency.network.outputs.nat_eip_allocation_id
  kms_key_arn            = dependency.security.outputs.kms_key_arn
  alert_topic_arn        = dependency.security.outputs.alert_topic_arn
  off_schedule           = local.env_vars.locals.nat_off_schedule
  on_schedule            = local.env_vars.locals.nat_on_schedule
  time_zone              = local.env_vars.locals.nat_time_zone
  log_retention_days     = local.env_vars.locals.log_retention_days
  enable_xray_tracing    = local.env_vars.locals.enable_xray_tracing
}
