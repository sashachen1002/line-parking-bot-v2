variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "line-parking-agent"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-1"
}

# ECS Task settings
variable "agent_cpu" {
  description = "CPU units for agent task"
  type        = number
  default     = 256
}

variable "agent_memory" {
  description = "Memory for agent task"
  type        = number
  default     = 512
}

variable "parking_mcp_cpu" {
  description = "CPU units for parking MCP task"
  type        = number
  default     = 256
}

variable "parking_mcp_memory" {
  description = "Memory for parking MCP task"
  type        = number
  default     = 512
}

# ECR existing repository names (use data sources)
variable "agent_ecr_repo_name" {
  description = "Agent ECR repository name"
  type        = string
  default     = "line-agent-agent"
}

variable "parking_mcp_ecr_repo_name" {
  description = "Parking MCP ECR repository name"
  type        = string
  default     = "line-agent-parking-search-mcp"
}

# Sensitive secrets (will be written to AWS Secrets Manager via Terraform)
variable "tdx_app_id" {
  description = "TDX APP ID"
  type        = string
  sensitive   = true
}

variable "tdx_app_key" {
  description = "TDX APP KEY"
  type        = string
  sensitive   = true
}

variable "langsmith_api_key" {
  description = "LangSmith API Key"
  type        = string
  sensitive   = true
}