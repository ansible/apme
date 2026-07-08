data "aws_ami" "rhel9" {
  most_recent = true
  owners      = ["309956199498"] # Red Hat

  filter {
    name   = "name"
    values = ["RHEL-9.*_HVM-*-x86_64-*-Hourly*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_key_pair" "apme" {
  key_name   = "${var.project_name}-key"
  public_key = var.ssh_public_key

  tags = {
    Name = "${var.project_name}-key"
  }
}

resource "aws_security_group" "ec2" {
  name        = "${var.project_name}-ec2"
  description = "SSH and ALB-to-instance traffic for the APME pod host."
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH access."
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_cidr_blocks
  }

  ingress {
    description     = "UI from ALB."
    from_port       = 8081
    to_port         = 8081
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Gateway REST from ALB."
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "oauth2-proxy from ALB (when enabled)."
    from_port       = 4180
    to_port         = 4180
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-ec2-sg"
  }
}

resource "aws_instance" "apme" {
  ami                    = data.aws_ami.rhel9.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.apme.key_name
  vpc_security_group_ids = [aws_security_group.ec2.id]
  subnet_id              = aws_subnet.public[0].id

  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    ghcr_prefix          = var.ghcr_image_prefix
    ghcr_tag             = var.ghcr_image_tag
    oauth2_enabled       = local.oauth2_enabled
    oauth2_client_id     = var.oauth2_proxy_client_id
    oauth2_client_secret = var.oauth2_proxy_client_secret
    oauth2_github_org    = var.oauth2_proxy_github_org
    oauth2_cookie_secret = var.oauth2_proxy_cookie_secret
    alb_dns_name         = "" # Not known at plan time; oauth2-proxy uses --redirect-url with the instance's own knowledge
  })

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  tags = {
    Name = "${var.project_name}-pod"
  }

  lifecycle {
    ignore_changes = [ami]
  }
}
