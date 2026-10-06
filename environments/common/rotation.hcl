# Application secret, rotation function and its schedule, alarm and failed-rotation rule
# (rows 21a and 21b).
locals {
  env_vars  = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  repo_root = get_repo_root()
}

terraform {
  source = "${local.repo_root}/modules/db-rotation"

  # Builds .build/rotation/db-rotation.zip (handler plus pinned dependencies, deterministic).
  before_hook "package" {
    commands = ["validate", "plan", "apply", "destroy"]
    execute  = ["python", "${local.repo_root}/scripts/package_rotation.py"]
  }
}

dependency "network" {
  config_path = "../network"
  mock_outputs = {
    vpc_id                 = "vpc-00000000000000000"
    vpc_cidr               = "10.0.0.0/16"
    public_subnet_ids      = ["subnet-22222222222222222", "subnet-33333333333333333"]
    private_subnet_ids     = ["subnet-00000000000000000", "subnet-11111111111111111"]
    nat_public_subnet_id   = "subnet-22222222222222222"
    public_route_table_id  = "rtb-11111111111111111"
    private_route_table_id = "rtb-00000000000000000"
    alb_security_group_id  = "sg-33333333333333333"
    svc_security_group_id  = "sg-22222222222222222"
    job_security_group_id  = "sg-11111111111111111"
    db_security_group_id   = "sg-00000000000000000"
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

dependency "database" {
  config_path = "../database"
  mock_outputs = {
    endpoint_address    = "mock-db.example.internal"
    port                = 5432
    db_name             = "calculator"
    master_secret_arn   = "arn:aws:secretsmanager:us-east-2:111122223333:secret:rds!db-mock"
    instance_identifier = "rds-mock"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

inputs = {
  kms_key_arn       = dependency.security.outputs.kms_key_arn
  alert_topic_arn   = dependency.security.outputs.alert_topic_arn
  subnet_ids        = dependency.network.outputs.private_subnet_ids
  security_group_id = dependency.network.outputs.job_security_group_id
  master_secret_arn = dependency.database.outputs.master_secret_arn
  db_host           = dependency.database.outputs.endpoint_address
  db_port           = dependency.database.outputs.port
  db_name           = dependency.database.outputs.db_name

  package_path        = "${local.repo_root}/.build/rotation/db-rotation.zip"
  log_retention_days  = local.env_vars.locals.log_retention_days
  enable_xray_tracing = local.env_vars.locals.enable_xray_tracing

  # Dev is destroyed and recreated often: no recovery window, or the name stays reserved.
  secret_recovery_window_days = local.env_vars.locals.env == "prod" ? 30 : 0

  # Rotation inside the NAT-on hours: the window start hour and duration come from env.hcl
  # (09:00 to 17:00 America/Panama = 14:00 to 22:00 UTC), one hour after the NAT turns on.
  # The run days are fixed in the cron expression: gaps of 3 days (4 at the end of 31-day
  # months), always far below 90.
  rotation_schedule_expression = "cron(0 ${local.env_vars.locals.rotation_window_start_hour_utc} 1,4,7,10,13,16,19,22,25,28 * ? *)"
  rotation_window_duration     = local.env_vars.locals.rotation_window_duration
}
