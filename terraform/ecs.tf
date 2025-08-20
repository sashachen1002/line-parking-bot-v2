resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = "${var.project_name}-cluster"
  }
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name        = "${var.project_name}.local"
  vpc         = aws_vpc.main.id
  description = "Service discovery namespace for ${var.project_name}"

  tags = {
    Name = "${var.project_name}-namespace"
  }
}

# CloudWatch Log Groups
resource "aws_cloudwatch_log_group" "agent" {
  name              = "/ecs/${var.project_name}-agent"
  retention_in_days = 7

  tags = {
    Name = "${var.project_name}-agent-log-group"
  }
}

resource "aws_cloudwatch_log_group" "parking_mcp" {
  name              = "/ecs/${var.project_name}-parking-mcp"
  retention_in_days = 7

  tags = {
    Name = "${var.project_name}-parking-mcp-log-group"
  }
}

# Agent Task Definition
resource "aws_ecs_task_definition" "agent" {
  family                   = "${var.project_name}-agent"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.agent_cpu
  memory                   = var.agent_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "agent"
      image     = "${data.aws_ecr_repository.agent.repository_url}:latest"
      essential = true

      portMappings = [
        { 
            containerPort = 8000, 
            protocol = "tcp",
            name = "agent-8000",
            appProtocol = "http"   
        }
      ]

      environment = [
        {
          name  = "PARKING_MCP_URL"
          value = "http://parking-mcp:9001/mcp"
        },
        { name = "LANGSMITH_TRACING", value = "true" },
        { name = "LANGSMITH_PROJECT", value = var.project_name }
      ]

      secrets = [
        { name = "LANGSMITH_API_KEY", valueFrom = aws_secretsmanager_secret.langsmith_api_key.arn }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.agent.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }

      healthCheck = {
        command      = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval     = 30
        timeout      = 10
        retries      = 3
      }
    }
  ])

  tags = {
    Name = "${var.project_name}-agent-task-definition"
  }

  depends_on = [
    aws_cloudwatch_log_group.agent
  ]
}

# Parking MCP Task Definition
resource "aws_ecs_task_definition" "parking_mcp" {
  family                   = "${var.project_name}-parking-mcp"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.parking_mcp_cpu
  memory                   = var.parking_mcp_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "parking-mcp"
      image     = "${data.aws_ecr_repository.parking_mcp.repository_url}:latest"
      essential = true

      portMappings = [
        { 
            containerPort = 9001, 
            protocol = "tcp",
            name = "parking-9001",
            appProtocol = "http"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.parking_mcp.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }

      secrets = [
        { name = "TDX_APP_ID",  valueFrom = aws_secretsmanager_secret.tdx_app_id.arn },
        { name = "TDX_APP_KEY", valueFrom = aws_secretsmanager_secret.tdx_app_key.arn }
      ]
    }
  ])

  tags = {
    Name = "${var.project_name}-parking-mcp-task-definition"
  }

  depends_on = [
    aws_cloudwatch_log_group.parking_mcp
  ]
}

resource "aws_ecs_service" "parking_mcp" {
    name = "${var.project_name}-parking-mcp-svc"
    cluster = aws_ecs_cluster.main.id
    task_definition = aws_ecs_task_definition.parking_mcp.arn
    desired_count = 1
    launch_type = "FARGATE"

    network_configuration {
        subnets = [for s in aws_subnet.public : s.id]
        security_groups = [aws_security_group.ecs_tasks.id]
        assign_public_ip = true
    }

    service_connect_configuration {
        enabled = true
        namespace = aws_service_discovery_private_dns_namespace.main.arn

        service {
            port_name = "parking-9001"
            discovery_name = "parking-mcp"
            client_alias {
                dns_name = "parking-mcp"
                port = 9001
            }
        }
    }

    enable_execute_command = true

    tags = {
        Name = "${var.project_name}-parking-mcp-service"
    }
}

# Agent Service
resource "aws_ecs_service" "agent" {
    name = "${var.project_name}-agent-svc"
    cluster = aws_ecs_cluster.main.id
    task_definition = aws_ecs_task_definition.agent.arn
    desired_count = 1
    launch_type = "FARGATE"

    network_configuration {
        subnets = [for s in aws_subnet.public : s.id]
        security_groups = [aws_security_group.ecs_tasks.id]
        assign_public_ip = true
    }

    service_connect_configuration {
        enabled = true
        namespace = aws_service_discovery_private_dns_namespace.main.arn
    }

    enable_execute_command = true

    health_check_grace_period_seconds = 300

    load_balancer {
        target_group_arn = aws_lb_target_group.agent.arn
        container_name = "agent"
        container_port = 8000
    }

    tags = {
        Name = "${var.project_name}-agent-svc"
    }

    depends_on = [
        aws_lb_listener.http
    ]
}