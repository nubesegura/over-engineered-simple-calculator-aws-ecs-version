# DB subnet group, parameter group, PostgreSQL instance and the rotation schedule of its
# managed master secret (rows 18 to 21).
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
}

terraform {
  source = "${get_repo_root()}/modules/database"
}

dependency "network" {
  config_path = "../network"
  mock_outputs = {
    private_subnet_ids     = ["subnet-00000000000000000", "subnet-11111111111111111"]
    db_security_group_id   = "sg-00000000000000000"
    job_security_group_id  = "sg-11111111111111111"
    svc_security_group_id  = "sg-22222222222222222"
    vpc_id                 = "vpc-00000000000000000"
    vpc_cidr               = "10.0.0.0/16"
    public_subnet_ids      = ["subnet-22222222222222222", "subnet-33333333333333333"]
    nat_public_subnet_id   = "subnet-22222222222222222"
    public_route_table_id  = "rtb-11111111111111111"
    private_route_table_id = "rtb-00000000000000000"
    alb_security_group_id  = "sg-33333333333333333"
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
  subnet_ids            = dependency.network.outputs.private_subnet_ids
  security_group_id     = dependency.network.outputs.db_security_group_id
  kms_key_arn           = dependency.security.outputs.kms_key_arn
  backup_retention_days = local.env_vars.locals.db_backup_retention_days
  deletion_protection   = local.env_vars.locals.db_deletion_protection
  skip_final_snapshot   = local.env_vars.locals.db_skip_final_snapshot
  log_exports           = local.env_vars.locals.db_log_exports
  log_retention_days    = local.env_vars.locals.log_retention_days
}
