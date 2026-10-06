# One ECS service per HTTP service: task definition, roles, log group (rows 25 to 28).
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))

  mock_services  = ["add", "sub", "mul", "div", "history"]
  mock_repo_urls = { for s in local.mock_services : s => "111122223333.dkr.ecr.us-east-2.amazonaws.com/mock-${s}" }
  mock_repo_arns = { for s in local.mock_services : s => "arn:aws:ecr:us-east-2:111122223333:repository/mock-${s}" }
  mock_tgs       = { for s in local.mock_services : s => "arn:aws:elasticloadbalancing:us-east-2:111122223333:targetgroup/mock-${s}/0000000000000000" }
}

terraform {
  source = "${get_repo_root()}/modules/service"
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
    repository_urls  = local.mock_repo_urls
    repository_arns  = local.mock_repo_arns
    repository_names = { for s in local.mock_services : s => "mock-${s}" }
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

dependency "edge" {
  config_path = "../edge"
  mock_outputs = {
    alb_arn               = "arn:aws:elasticloadbalancing:us-east-2:111122223333:loadbalancer/app/mock/0000000000000000"
    alb_dns_name          = "mock-alb.example.internal"
    alb_zone_id           = "Z00000000000"
    alb_security_group_id = "sg-33333333333333333"
    target_group_arns     = local.mock_tgs
    https_listener_arn    = "arn:aws:elasticloadbalancing:us-east-2:111122223333:listener/app/mock/0000000000000000/0000000000000000"
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

  image_repository_urls = dependency.registry.outputs.repository_urls
  image_repository_arns = dependency.registry.outputs.repository_arns
  # The commit SHA from the pipeline; the placeholder only serves validate and plan by hand.
  image_tag = get_env("IMAGE_TAG", "unset-image-tag")

  target_group_arns = dependency.edge.outputs.target_group_arns
  subnet_ids        = dependency.network.outputs.private_subnet_ids
  security_group_id = dependency.network.outputs.svc_security_group_id

  app_secret_arn      = dependency.rotation.outputs.app_secret_arn
  db_host             = dependency.database.outputs.endpoint_address
  db_port             = dependency.database.outputs.port
  db_name             = dependency.database.outputs.db_name
  cors_allowed_origin = local.env_vars.locals.web_origin
}
