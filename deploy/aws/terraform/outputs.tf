output "vpc_id" {
  description = "ID of the VPC."
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs of the two public subnets."
  value       = aws_subnet.public[*].id
}

output "alb_dns_name" {
  description = "DNS name of the application load balancer."
  value       = aws_lb.main.dns_name
}

output "instance_id" {
  description = "EC2 instance ID running the APME pod."
  value       = aws_instance.apme.id
}

output "instance_public_ip" {
  description = "Public IP of the EC2 instance."
  value       = aws_instance.apme.public_ip
}

output "ssh_command" {
  description = "Convenience SSH command for the EC2 instance (RHEL 9 default user is ec2-user)."
  value       = "ssh ec2-user@${aws_instance.apme.public_ip}"
}

output "oauth2_proxy_callback_url" {
  description = "Authorization callback URL for the GitHub OAuth App (set this when registering the app)."
  value       = "http://${aws_lb.main.dns_name}/oauth2/callback"
}

output "terraform_state_bucket_name" {
  description = "S3 bucket for Terraform state when create_state_backend is true; null otherwise."
  value       = var.create_state_backend ? aws_s3_bucket.terraform_state[0].id : null
}

output "terraform_state_lock_table_name" {
  description = "DynamoDB table for Terraform state locking when create_state_backend is true; null otherwise."
  value       = var.create_state_backend ? aws_dynamodb_table.terraform_locks[0].name : null
}
