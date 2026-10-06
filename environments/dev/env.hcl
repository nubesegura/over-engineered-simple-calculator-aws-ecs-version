# DEV environment configuration. Every difference between environments lives in
# env.hcl; the units and common are identical for dev and prod. Each environment is
# deployed to its own AWS account: the GitHub environment of the branch decides which
# role (and therefore which account) the deployment assumes.
locals {
  env        = "dev"
  aws_region = "us-east-2"

  # Local (untracked) secrets.yml next to this file, used only when running Terragrunt
  # by hand; the deploy workflow passes the values as environment variables.
  local_secrets = try(yamldecode(file("${dirname(find_in_parent_folders("env.hcl"))}/secrets.yml")), {})

  # --- Network ---
  vpc_cidr = "10.20.0.0/16"
  # VPC flow logs (VPC-01): prod only.
  enable_flow_logs = false

  # --- Security ---
  # KMS pending-deletion window (KMS-02: 30 days in prod).
  kms_deletion_window_days = 7

  # --- Alerts ---
  # GitHub environment secret SUPPORT_EMAIL (placeholder when running locally).
  alert_email = get_env("SUPPORT_EMAIL", try(local.local_secrets.SUPPORT_EMAIL, "alerts-dev@example.com"))

  # --- NAT schedule (scheduler unit) ---
  nat_off_schedule = "cron(0 22 * * ? *)" # 22:00 off
  nat_on_schedule  = "cron(0 8 * * ? *)"  # 08:00 on
  nat_time_zone    = "America/Panama"

  # --- Secret rotation window (rotation unit), in UTC ---
  # Assumes the owner time zone America/Panama = UTC-5: change it together with the NAT
  # schedule above. Starts one hour after the NAT turns on (09:00 local = 14:00 UTC) and
  # ends before it turns off (17:00 local). A window cannot cross midnight UTC.
  rotation_window_start_hour_utc = 14
  rotation_window_duration       = "8h"

  # --- Ingestion bucket lifecycle (days until expiry of each prefix) ---
  ingest_processed_retention_days = 30
  ingest_rejected_retention_days  = 30
  ingest_reports_retention_days   = 30

  # --- Teardown ---
  # Let destroy remove non-empty ECR repositories and S3 buckets (S3-15: never in prod).
  force_destroy_data = true

  # --- Observability ---
  # X-Ray active tracing of the scheduler function (LMB-07): prod only.
  enable_xray_tracing = false
  log_retention_days  = 14
  # Container Insights: prod only.
  enable_container_insights = false
  # ALB access log bucket lifecycle.
  alb_log_retention_days = 30

  # --- Alarms (same values in both environments) ---
  alarm_service_cpu_percent   = 80
  alarm_alb_5xx_count         = 5
  alarm_target_5xx_count      = 5
  alarm_db_free_storage_bytes = 2147483648 # 2 GiB of the 20 GB volume
  alarm_db_connections        = 60
  alarm_db_cpu_percent        = 80

  # --- Database ---
  db_backup_retention_days = 1
  db_deletion_protection   = false
  db_skip_final_snapshot   = true
  # PostgreSQL logs shipped to CloudWatch: prod only.
  db_log_exports = []

  # --- Tasks (Fargate, ARM64) ---
  task_cpu    = 256
  task_memory = 512

  # --- Edge ---
  # Origin of the web page (CORS) of this environment.
  web_origin = "https://over-engineered-simple-calculator.dev.nube-segura.com"
  # Shared regional WAF ACL (owned elsewhere): associated in prod only.
  enable_waf  = false
  waf_acl_arn = get_env("WAF_ACL_ARN", try(local.local_secrets.WAF_ACL_ARN, ""))

  # ALB deletion protection (ELB-03): prod only.
  alb_deletion_protection = false

  # Cognito user pool (created by another repository) whose ID tokens the load balancer
  # verifies. GitHub environment variables COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID.
  cognito_user_pool_id  = get_env("COGNITO_USER_POOL_ID", try(local.local_secrets.COGNITO_USER_POOL_ID, ""))
  cognito_app_client_id = get_env("COGNITO_APP_CLIENT_ID", try(local.local_secrets.COGNITO_APP_CLIENT_ID, ""))
}
