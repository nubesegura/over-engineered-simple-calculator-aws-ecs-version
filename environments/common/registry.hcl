# ECR repositories and lifecycle policies (row 17).
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
}

terraform {
  source = "${get_repo_root()}/modules/registry"
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
  kms_key_arn  = dependency.security.outputs.kms_key_arn
  force_delete = local.env_vars.locals.force_destroy_data
}
