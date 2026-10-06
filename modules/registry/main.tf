data "aws_region" "current" {}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  name_mid = "${replace(data.aws_region.current.region, "-", "")}-${var.context}"
}

# One repository per container image (the migration task reuses the history image).
resource "aws_ecr_repository" "service" {
  for_each = toset(var.services)

  name                 = "ecr-${local.name_mid}-${each.key}-${var.env_type}"
  image_tag_mutability = "IMMUTABLE" # ECR-02
  force_delete         = var.force_delete

  image_scanning_configuration {
    scan_on_push = true # ECR-01
  }

  encryption_configuration { # ECR-04
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }
}

# Keep the newest images only (ECR-03).
resource "aws_ecr_lifecycle_policy" "service" {
  for_each = aws_ecr_repository.service

  repository = each.value.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last ${var.images_to_keep} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.images_to_keep
      }
      action = { type = "expire" }
    }]
  })
}

# No aws_ecr_repository_policy and no public repository (ECR-05, ECR-06): pulls and
# pushes are granted by IAM (deployment role, execution roles).
