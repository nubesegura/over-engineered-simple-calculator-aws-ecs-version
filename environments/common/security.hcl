# Customer managed key and alerts topic (rows 15 and 16 of the resource inventory).
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
}

terraform {
  source = "${get_repo_root()}/modules/security"
}

inputs = {
  deletion_window_in_days = local.env_vars.locals.kms_deletion_window_days
  alert_email             = local.env_vars.locals.alert_email
}
