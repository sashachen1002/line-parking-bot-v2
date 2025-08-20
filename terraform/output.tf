output "agent_ecr_url" { value = data.aws_ecr_repository.agent.repository_url }
output "parking_mcp_ecr_url" { value = data.aws_ecr_repository.parking_mcp.repository_url }
output "alb_dns_name" { value = aws_lb.app.dns_name }
output "project_name" { value = var.project_name }
output "aws_region" { value = var.aws_region }