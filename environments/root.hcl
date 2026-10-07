locals {
  # Read environment variables
  env_vars = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  env      = local.env_vars.locals.env
  region   = local.env_vars.locals.aws_region

  # Account ID
  account_id = get_aws_account_id()

  # Naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>.
  # `context` is the same in every environment, so it lives here and not in env.hcl.
  context     = "oecalc"
  region_code = replace(local.region, "-", "")

  # Mandatory business tags (team standard). repo-name must equal the GitHub repository name
  # (this repository's CI does not check it).
  mandatory_tags = {
    "team-owner"   = "nube-segura"
    "project-name" = "over-engineered-calculator"
    "app-name"     = "calc-api"
    "repo-name"    = "over-engineered-simple-calculator-aws-ecs-version"
    "env-type"     = local.env
  }
}

generate "provider" {
  path      = "provider.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<EOF
provider "aws" {
  region = "${local.region}"

  default_tags {
    tags = {
      "team-owner"   = "${local.mandatory_tags["team-owner"]}"
      "project-name" = "${local.mandatory_tags["project-name"]}"
      "app-name"     = "${local.mandatory_tags["app-name"]}"
      "repo-name"    = "${local.mandatory_tags["repo-name"]}"
      "env-type"     = "${local.mandatory_tags["env-type"]}"
    }
  }
}
EOF
}

remote_state {
  backend = "s3"
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
  config = {
    # One state bucket per environment; the account ID keeps the global name unique.
    bucket = "bckt-${local.region_code}-tf-state-${local.env}-${local.account_id}"
    # Forward slashes on every OS: on Windows path_relative_to_include() returns backslashes, which would point a local run
    # at a different state key than the CI.
    key          = "over-engineered-simple-calculator-aws-ecs-version/${replace(path_relative_to_include(), "\\", "/")}/terraform.tfstate"
    region       = local.region
    encrypt      = true
    use_lockfile = true # Use native S3 locking

    # Terragrunt creates the bucket on first use: tag it like every other resource.
    s3_bucket_tags = local.mandatory_tags
  }
}

# Naming inputs shared by every unit (merged with the unit and common inputs).
inputs = {
  context  = local.context
  env_type = local.env
}
