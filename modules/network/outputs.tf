output "vpc_id" {
  value       = aws_vpc.main.id
  description = "ID of the VPC"
}

output "vpc_cidr" {
  value       = aws_vpc.main.cidr_block
  description = "CIDR block of the VPC"
}

output "public_subnet_ids" {
  value       = [aws_subnet.public["a"].id, aws_subnet.public["b"].id]
  description = "Public subnet IDs (AZ a first): load balancer nodes and the NAT gateway"
}

output "private_subnet_ids" {
  value       = [aws_subnet.private["a"].id, aws_subnet.private["b"].id]
  description = "Private subnet IDs (AZ a first): tasks and database"
}

output "nat_public_subnet_id" {
  value       = aws_subnet.public["a"].id
  description = "Public subnet where the scheduler creates the NAT gateway (AZ a)"
}

output "public_route_table_id" {
  value       = aws_route_table.public.id
  description = "ID of the public route table"
}

output "private_route_table_id" {
  value       = aws_route_table.private.id
  description = "ID of the private route table (the scheduler owns its default route)"
}

output "alb_security_group_id" {
  value       = aws_security_group.alb.id
  description = "ID of the load balancer security group"
}

output "svc_security_group_id" {
  value       = aws_security_group.svc.id
  description = "ID of the services security group"
}

output "job_security_group_id" {
  value       = aws_security_group.job.id
  description = "ID of the jobs (ingest, migrate, rotation) security group"
}

output "db_security_group_id" {
  value       = aws_security_group.db.id
  description = "ID of the database security group"
}

output "nat_eip_allocation_id" {
  value       = aws_eip.nat.id
  description = "Allocation ID of the Elastic IP for the NAT gateway"
}
