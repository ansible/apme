locals {
  terraform_state_bucket_name = trimspace(var.state_backend_bucket_name) != "" ? trimspace(var.state_backend_bucket_name) : "${var.project_name}-tfstate-${data.aws_caller_identity.current.account_id}"
  terraform_lock_table_name   = "${var.project_name}-terraform-locks"
}

resource "aws_s3_bucket" "terraform_state" {
  count         = var.create_state_backend ? 1 : 0
  bucket        = local.terraform_state_bucket_name
  force_destroy = true

  tags = {
    Name = "${var.project_name}-terraform-state"
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  count  = var.create_state_backend ? 1 : 0
  bucket = aws_s3_bucket.terraform_state[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  count  = var.create_state_backend ? 1 : 0
  bucket = aws_s3_bucket.terraform_state[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  count  = var.create_state_backend ? 1 : 0
  bucket = aws_s3_bucket.terraform_state[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "terraform_locks" {
  count = var.create_state_backend ? 1 : 0

  name         = local.terraform_lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name = "${var.project_name}-terraform-locks"
  }
}
