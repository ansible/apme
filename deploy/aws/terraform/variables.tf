variable "aws_region" {
  type        = string
  description = "AWS region for all resources in this root module."
  default     = "us-east-1"
}

variable "project_name" {
  type        = string
  description = "Short name prefix for Name tags and value of tag Project= (e.g. Resource Groups)."
  default     = "apme-dev"
}

variable "vpc_cidr" {
  type        = string
  description = "IPv4 CIDR for the VPC."
  default     = "10.42.0.0/16"
}

# --- EC2 ---

variable "instance_type" {
  type        = string
  description = "EC2 instance type for the APME pod host."
  default     = "t3.large"
}

variable "ssh_public_key" {
  type        = string
  description = "SSH public key material (ssh-ed25519 or ssh-rsa ...) for the EC2 key pair. Required."
}

variable "ssh_cidr_blocks" {
  type        = list(string)
  description = "CIDR blocks allowed to SSH into the EC2 instance."
  default     = ["0.0.0.0/0"]
}

variable "ghcr_image_prefix" {
  type        = string
  description = "GHCR image prefix for APME service images (without trailing slash)."
  default     = "ghcr.io/ansible"
}

variable "ghcr_image_tag" {
  type        = string
  description = "Tag to pull for APME service images."
  default     = "latest"
}

# --- OAuth2 Proxy (optional) ---

variable "oauth2_proxy_client_id" {
  type        = string
  description = "GitHub OAuth App client ID. When non-empty, oauth2-proxy is added to the pod and the ALB routes through it."
  default     = ""
}

variable "oauth2_proxy_client_secret" {
  type        = string
  sensitive   = true
  description = "GitHub OAuth App client secret (sensitive)."
  default     = ""
}

variable "oauth2_proxy_github_org" {
  type        = string
  description = "GitHub organization whose members are allowed access via oauth2-proxy."
  default     = "ansible-employees"
}

variable "oauth2_proxy_cookie_secret" {
  type        = string
  sensitive   = true
  description = "32-byte base64 cookie secret for oauth2-proxy. If empty, one is generated from the client_secret."
  default     = ""
}

# --- Remote state backend (optional) ---

variable "create_state_backend" {
  type        = bool
  description = "When true, create an S3 bucket and DynamoDB table for Terraform remote state."
  default     = false
}

variable "state_backend_bucket_name" {
  type        = string
  description = "Override S3 bucket name for Terraform state (if empty, derived from project_name and AWS account ID)."
  default     = ""
}
