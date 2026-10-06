# Unit alarms: ECS CPU and target group health per service, ALB 5XX, database and
# ingest rule alarms (row 41). The scheduler and rotation alarms live in their modules.
locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))

  mock_services = ["add", "sub", "mul", "div", "history"]
  mock_tgs      = { for s in local.mock_services : s => "arn:aws:elasticloadbalancing:us-east-2:111122223333:targetgroup/mock-${s}/0000000000000000" }
}

terraform {
  source = "${get_repo_root()}/modules/alarms"
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

dependency "services" {
  config_path = "../services"
  mock_outputs = {
    service_names        = { for s in local.mock_services : s => "mock-${s}" }
    task_definition_arns = { for s in local.mock_services : s => "arn:aws:ecs:us-east-2:111122223333:task-definition/mock-${s}:1" }
    task_role_arns       = { for s in local.mock_services : s => "arn:aws:iam::111122223333:role/mock-${s}-task" }
    exec_role_arns       = { for s in local.mock_services : s => "arn:aws:iam::111122223333:role/mock-${s}-exec" }
    log_group_names      = { for s in local.mock_services : s => "/mock/ecs/${s}" }
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

dependency "ingestion" {
  config_path = "../ingestion"
  mock_outputs = {
    task_definition_arn  = "arn:aws:ecs:us-east-2:111122223333:task-definition/mock-ingest:1"
    task_role_arn        = "arn:aws:iam::111122223333:role/mock-ingest-task"
    exec_role_arn        = "arn:aws:iam::111122223333:role/mock-ingest-exec"
    log_group_name       = "/mock/ecs/ingest"
    ingest_rule_arn      = "arn:aws:events:us-east-2:111122223333:rule/mock-ingest"
    task_failed_rule_arn = "arn:aws:events:us-east-2:111122223333:rule/mock-task-failed"
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
  alert_topic_arn        = dependency.security.outputs.alert_topic_arn
  cluster_name           = dependency.cluster.outputs.cluster_name
  service_names          = dependency.services.outputs.service_names
  target_group_arns      = dependency.edge.outputs.target_group_arns
  alb_arn                = dependency.edge.outputs.alb_arn
  db_instance_identifier = dependency.database.outputs.instance_identifier
  ingest_rule_arn        = dependency.ingestion.outputs.ingest_rule_arn

  service_cpu_threshold_percent   = local.env_vars.locals.alarm_service_cpu_percent
  alb_5xx_threshold               = local.env_vars.locals.alarm_alb_5xx_count
  target_5xx_threshold            = local.env_vars.locals.alarm_target_5xx_count
  db_free_storage_threshold_bytes = local.env_vars.locals.alarm_db_free_storage_bytes
  db_connections_threshold        = local.env_vars.locals.alarm_db_connections
  db_cpu_threshold_percent        = local.env_vars.locals.alarm_db_cpu_percent
}
