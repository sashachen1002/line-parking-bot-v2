# general lifecycle policy
locals {
    ecr_lifecycle_policy = jsonencode({
        rules = [
            {
            "rulePriority": 1,
            "description": "Keep latest tag",
            "selection": {
                "tagStatus": "tagged",
                "tagPrefixList": ["latest"],
                "countType": "imageCountMoreThan",
                "countNumber": 999
            },
            "action": {
                "type": "expire"
            }
        },
        {
            "rulePriority": 2,
            "description": "Keep last 10 versioned images",
            "selection": {
                "tagStatus": "any",
                "countType": "imageCountMoreThan",
                "countNumber": 10
            },
            "action": {
                "type": "expire"
            }
        }
        ]
    })
}

# Use existing ECR repositories (data sources)
data "aws_ecr_repository" "agent" {
  name = var.agent_ecr_repo_name
}

data "aws_ecr_repository" "parking_mcp" {
  name = var.parking_mcp_ecr_repo_name
}

resource "aws_ecr_lifecycle_policy" "agent_policy" {
  repository = data.aws_ecr_repository.agent.name
  policy     = local.ecr_lifecycle_policy
}

resource "aws_ecr_lifecycle_policy" "parking_mcp_policy" {
  repository = data.aws_ecr_repository.parking_mcp.name
  policy     = local.ecr_lifecycle_policy
}