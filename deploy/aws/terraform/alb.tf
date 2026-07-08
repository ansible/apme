resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb"
  description = "HTTP ingress for the APME application load balancer."
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from the internet."
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-alb-sg"
  }
}

resource "aws_lb" "main" {
  name               = "${var.project_name}-alb"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  tags = {
    Name = "${var.project_name}-alb"
  }
}

# --- Target groups ---

locals {
  oauth2_enabled = trimspace(var.oauth2_proxy_client_id) != ""
}

resource "aws_lb_target_group" "ui" {
  name     = "${var.project_name}-tg-ui"
  port     = local.oauth2_enabled ? 4180 : 8081
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 15
    matcher             = "200-399"
    path                = local.oauth2_enabled ? "/ping" : "/"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 3
  }

  tags = {
    Name = "${var.project_name}-tg-ui"
  }
}

resource "aws_lb_target_group" "gateway" {
  name     = "${var.project_name}-tg-gw"
  port     = 8080
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 15
    matcher             = "200-399"
    path                = "/api/v1/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 3
  }

  tags = {
    Name = "${var.project_name}-tg-gw"
  }
}

resource "aws_lb_target_group_attachment" "ui" {
  target_group_arn = aws_lb_target_group.ui.arn
  target_id        = aws_instance.apme.id
  port             = local.oauth2_enabled ? 4180 : 8081
}

resource "aws_lb_target_group_attachment" "gateway" {
  count            = local.oauth2_enabled ? 0 : 1
  target_group_arn = aws_lb_target_group.gateway.arn
  target_id        = aws_instance.apme.id
  port             = 8080
}

# --- Listener + rules ---

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ui.arn
  }
}

resource "aws_lb_listener_rule" "gateway_api" {
  count        = local.oauth2_enabled ? 0 : 1
  listener_arn = aws_lb_listener.http.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }

  condition {
    path_pattern {
      values = ["/api/*"]
    }
  }
}
