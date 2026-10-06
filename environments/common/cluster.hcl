# ECS cluster and the migrate task (rows 24, 25 for migrate, 27 and 28). The service task
# definitions arrive with the services unit.
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
}

terraform {
  source = "${get_repo_root()}/modules/cluster"
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

dependency "registry" {
  config_path = "../registry"
  mock_outputs = {
    repository_urls  = { history = "111122223333.dkr.ecr.us-east-2.amazonaws.com/mock-history" }
    repository_arns  = { history = "arn:aws:ecr:us-east-2:111122223333:repository/mock-history" }
    repository_names = { history = "mock-history" }
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

dependency "rotation" {
  config_path = "../rotation"
  mock_outputs = {
    app_secret_arn    = "arn:aws:secretsmanager:us-east-2:111122223333:secret:mock-app"
    app_secret_name   = "mock-app"
    function_name     = "mock-rotation"
    function_role_arn = "arn:aws:iam::111122223333:role/mock-rotation"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

inputs = {
  kms_key_arn               = dependency.security.outputs.kms_key_arn
  enable_container_insights = local.env_vars.locals.enable_container_insights
  log_retention_days        = local.env_vars.locals.log_retention_days
  task_cpu                  = local.env_vars.locals.task_cpu
  task_memory               = local.env_vars.locals.task_memory

  # The migration image is built from the history image: same repository.
  image_repository_url = dependency.registry.outputs.repository_urls["history"]
  image_repository_arn = dependency.registry.outputs.repository_arns["history"]
  # The commit SHA from the pipeline. Without IMAGE_TAG the placeholder makes the module
  # validation fail at plan time (image_tag must not start with unset-).
  # The migrate image is built with SERVICE=migrate and pushed to the history repository under this tag.
  image_tag = "migrate-${get_env("IMAGE_TAG", "unset-image-tag")}"

  master_secret_arn = dependency.database.outputs.master_secret_arn
  app_secret_arn    = dependency.rotation.outputs.app_secret_arn
  rotation_role_arn = dependency.rotation.outputs.function_role_arn
  db_host           = dependency.database.outputs.endpoint_address
  db_port           = dependency.database.outputs.port
  db_name           = dependency.database.outputs.db_name

  job_subnet_ids        = dependency.network.outputs.private_subnet_ids
  job_security_group_id = dependency.network.outputs.job_security_group_id
}
