# Ingestion job: task definition, roles, log group, S3 trigger rule with its ECS target
# and the failed-task alert rule (rows 25 to 28 for ingest and 35 to 37).
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
}

terraform {
  source = "${get_repo_root()}/modules/ingestion"
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

dependency "storage" {
  config_path = "../storage"
  mock_outputs = {
    ingest_bucket_id   = "mock-ingest-bucket"
    ingest_bucket_arn  = "arn:aws:s3:::mock-ingest-bucket"
    alb_log_bucket_id  = "mock-alb-log-bucket"
    alb_log_bucket_arn = "arn:aws:s3:::mock-alb-log-bucket"
    alb_log_prefix     = "alb"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

dependency "registry" {
  config_path = "../registry"
  mock_outputs = {
    repository_urls  = { ingest = "111122223333.dkr.ecr.us-east-2.amazonaws.com/mock-ingest" }
    repository_arns  = { ingest = "arn:aws:ecr:us-east-2:111122223333:repository/mock-ingest" }
    repository_names = { ingest = "mock-ingest" }
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
    app_secret_arn  = "arn:aws:secretsmanager:us-east-2:111122223333:secret:mock-app"
    app_secret_name = "mock-app"
    function_name   = "mock-rotation"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

dependency "cluster" {
  config_path = "../cluster"
  mock_outputs = {
    cluster_name                   = "mock-cluster"
    cluster_arn                    = "arn:aws:ecs:us-east-2:111122223333:cluster/mock-cluster"
    migrate_task_definition_arn    = "arn:aws:ecs:us-east-2:111122223333:task-definition/mock-migrate:1"
    migrate_task_definition_family = "mock-migrate"
    migrate_task_role_arn          = "arn:aws:iam::111122223333:role/mock-migrate-task"
    migrate_exec_role_arn          = "arn:aws:iam::111122223333:role/mock-migrate-exec"
    migrate_log_group_name         = "/mock/ecs/migrate"
    migrate_subnet_ids             = ["subnet-00000000000000000", "subnet-11111111111111111"]
    migrate_security_group_id      = "sg-11111111111111111"
  }
  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]
}

inputs = {
  cluster_arn        = dependency.cluster.outputs.cluster_arn
  kms_key_arn        = dependency.security.outputs.kms_key_arn
  alert_topic_arn    = dependency.security.outputs.alert_topic_arn
  log_retention_days = local.env_vars.locals.log_retention_days
  task_cpu           = local.env_vars.locals.task_cpu
  task_memory        = local.env_vars.locals.task_memory

  image_repository_url = dependency.registry.outputs.repository_urls["ingest"]
  image_repository_arn = dependency.registry.outputs.repository_arns["ingest"]
  # The commit SHA from the pipeline; the placeholder only serves validate and plan by hand.
  image_tag = get_env("IMAGE_TAG", "unset-image-tag")

  subnet_ids         = dependency.network.outputs.private_subnet_ids
  security_group_id  = dependency.network.outputs.job_security_group_id
  ingest_bucket_name = dependency.storage.outputs.ingest_bucket_id
  ingest_bucket_arn  = dependency.storage.outputs.ingest_bucket_arn

  app_secret_arn      = dependency.rotation.outputs.app_secret_arn
  db_host             = dependency.database.outputs.endpoint_address
  db_port             = dependency.database.outputs.port
  db_name             = dependency.database.outputs.db_name
  cors_allowed_origin = local.env_vars.locals.web_origin
}
