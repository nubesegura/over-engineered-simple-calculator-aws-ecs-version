# Ingestion bucket and load balancer log bucket (rows 22 and 23).
locals {
  env_vars   = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  account_id = get_aws_account_id()
}

terraform {
  source = "${get_repo_root()}/modules/storage"
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
  account_id               = local.account_id
  kms_key_arn              = dependency.security.outputs.kms_key_arn
  processed_retention_days = local.env_vars.locals.ingest_processed_retention_days
  rejected_retention_days  = local.env_vars.locals.ingest_rejected_retention_days
  reports_retention_days   = local.env_vars.locals.ingest_reports_retention_days
  alb_log_retention_days   = local.env_vars.locals.alb_log_retention_days
  force_destroy            = local.env_vars.locals.force_destroy_data
}
