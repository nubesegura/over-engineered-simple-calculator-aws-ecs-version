data "aws_region" "current" {}

data "aws_availability_zones" "available" {
  state = "available"

  # Only standard zones of the region (no opt-in or local zones).
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

locals {
  # Team naming convention: <acronym>-<region>-<context>[-<descriptor>]-<env-type>
  region_code = replace(data.aws_region.current.region, "-", "")
  name_mid    = "${local.region_code}-${var.context}"

  # Two AZs; subnets are /24 slices of the VPC block.
  azs = {
    a = data.aws_availability_zones.available.names[0]
    b = data.aws_availability_zones.available.names[1]
  }
  public_cidrs  = { a = cidrsubnet(var.vpc_cidr, 8, 1), b = cidrsubnet(var.vpc_cidr, 8, 2) }
  private_cidrs = { a = cidrsubnet(var.vpc_cidr, 8, 11), b = cidrsubnet(var.vpc_cidr, 8, 12) }
}

# ---------------------------------------------------------
# VPC, subnets, internet gateway
# ---------------------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "vpc-${local.name_mid}-${var.env_type}"
  }
}

resource "aws_subnet" "public" {
  for_each = local.azs

  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.value
  cidr_block              = local.public_cidrs[each.key]
  map_public_ip_on_launch = false

  tags = {
    Name = "snet-${local.name_mid}-public-${each.key}-${var.env_type}"
  }
}

resource "aws_subnet" "private" {
  for_each = local.azs

  vpc_id                  = aws_vpc.main.id
  availability_zone       = each.value
  cidr_block              = local.private_cidrs[each.key]
  map_public_ip_on_launch = false

  tags = {
    Name = "snet-${local.name_mid}-private-${each.key}-${var.env_type}"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "igw-${local.name_mid}-${var.env_type}"
  }
}

# ---------------------------------------------------------
# Route tables
# ---------------------------------------------------------
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "rtb-${local.name_mid}-public-${var.env_type}"
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# The private table has NO default route here: the NAT scheduler function creates and
# deletes it together with the NAT gateway.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "rtb-${local.name_mid}-private-${var.env_type}"
  }

  lifecycle {
    # The scheduler adds and removes the default route outside Terraform.
    ignore_changes = [route]
  }
}

resource "aws_route_table_association" "private" {
  for_each = aws_subnet.private

  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

# Free gateway endpoint: S3 traffic (image layers, ingestion) does not use the NAT.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name = "vpce-${local.name_mid}-s3-${var.env_type}"
  }
}

# Fixed address of the NAT gateway; the scheduler function attaches it when it creates
# the gateway each morning.
resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "eip-${local.name_mid}-nat-${var.env_type}"
  }

  depends_on = [aws_internet_gateway.main]
}

# ---------------------------------------------------------
# Security groups (rules by reference, one description each)
# ---------------------------------------------------------
# VPC-02: the default group has no rules and is never used.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "sg-${local.name_mid}-default-${var.env_type}"
  }
}

resource "aws_security_group" "alb" {
  name        = "sg-${local.name_mid}-alb-${var.env_type}"
  description = "Load balancer: HTTPS and HTTP from the internet, egress to the tasks only"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "sg-${local.name_mid}-alb-${var.env_type}"
  }
}

resource "aws_security_group" "svc" {
  name        = "sg-${local.name_mid}-svc-${var.env_type}"
  description = "HTTP services: task port from the load balancer, egress 443 and database"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "sg-${local.name_mid}-svc-${var.env_type}"
  }
}

resource "aws_security_group" "job" {
  name        = "sg-${local.name_mid}-job-${var.env_type}"
  description = "Jobs (ingest, migrate, rotation): no ingress, egress 443 and database"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "sg-${local.name_mid}-job-${var.env_type}"
  }
}

resource "aws_security_group" "db" {
  name        = "sg-${local.name_mid}-db-${var.env_type}"
  description = "PostgreSQL: database port only from the services and jobs groups"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "sg-${local.name_mid}-db-${var.env_type}"
  }
}

# Load balancer: the only group open to the internet (expected for an internet-facing
# balancer; ports 443 and 80 only).
resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from the internet to the load balancer"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from the internet to the load balancer (only redirects to HTTPS)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_svc" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Load balancer to the services on the task port"
  ip_protocol                  = "tcp"
  from_port                    = var.task_port
  to_port                      = var.task_port
  referenced_security_group_id = aws_security_group.svc.id
}

# Services
resource "aws_vpc_security_group_ingress_rule" "svc_from_alb" {
  security_group_id            = aws_security_group.svc.id
  description                  = "Task port from the load balancer"
  ip_protocol                  = "tcp"
  from_port                    = var.task_port
  to_port                      = var.task_port
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "svc_https" {
  security_group_id = aws_security_group.svc.id
  description       = "HTTPS egress (ECR, logs, secrets, SNS through the NAT; S3 through the endpoint)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "svc_to_db" {
  security_group_id            = aws_security_group.svc.id
  description                  = "Services to the database"
  ip_protocol                  = "tcp"
  from_port                    = var.database_port
  to_port                      = var.database_port
  referenced_security_group_id = aws_security_group.db.id
}

# Jobs (no ingress)
resource "aws_vpc_security_group_egress_rule" "job_https" {
  security_group_id = aws_security_group.job.id
  description       = "HTTPS egress (ECR, logs, secrets, SNS through the NAT; S3 through the endpoint)"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "job_to_db" {
  security_group_id            = aws_security_group.job.id
  description                  = "Jobs to the database"
  ip_protocol                  = "tcp"
  from_port                    = var.database_port
  to_port                      = var.database_port
  referenced_security_group_id = aws_security_group.db.id
}

# Database (no egress)
resource "aws_vpc_security_group_ingress_rule" "db_from_svc" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from the services"
  ip_protocol                  = "tcp"
  from_port                    = var.database_port
  to_port                      = var.database_port
  referenced_security_group_id = aws_security_group.svc.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_job" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from the jobs"
  ip_protocol                  = "tcp"
  from_port                    = var.database_port
  to_port                      = var.database_port
  referenced_security_group_id = aws_security_group.job.id
}

# ---------------------------------------------------------
# VPC flow logs (prod only, VPC-01)
# ---------------------------------------------------------
resource "aws_cloudwatch_log_group" "flow" {
  count = var.enable_flow_logs ? 1 : 0

  name              = "/${var.context}/${var.env_type}/vpc-flow"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_iam_role" "flow" {
  count = var.enable_flow_logs ? 1 : 0

  name = "iamr-${local.name_mid}-flowlog-${var.env_type}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "flow" {
  count = var.enable_flow_logs ? 1 : 0

  name = "iamp-${local.name_mid}-flowlog-${var.env_type}"
  role = aws_iam_role.flow[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = [
        aws_cloudwatch_log_group.flow[0].arn,
        "${aws_cloudwatch_log_group.flow[0].arn}:*",
      ]
    }]
  })
}

resource "aws_flow_log" "main" {
  count = var.enable_flow_logs ? 1 : 0

  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow[0].arn
  iam_role_arn         = aws_iam_role.flow[0].arn

  tags = {
    Name = "flog-${local.name_mid}-${var.env_type}"
  }
}

data "aws_caller_identity" "current" {}
