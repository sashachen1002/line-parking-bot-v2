# Secrets Manager (IaC): create secrets and set current values from TF vars

resource "aws_secretsmanager_secret" "tdx_app_id" {
  name = "${var.project_name}/TDX_APP_ID"
}

resource "aws_secretsmanager_secret" "tdx_app_key" {
  name = "${var.project_name}/TDX_APP_KEY"
}

resource "aws_secretsmanager_secret" "langsmith_api_key" {
  name = "${var.project_name}/LANGSMITH_API_KEY"
}

resource "aws_secretsmanager_secret_version" "tdx_app_id_v" {
  secret_id     = aws_secretsmanager_secret.tdx_app_id.id
  secret_string = var.tdx_app_id
}

resource "aws_secretsmanager_secret_version" "tdx_app_key_v" {
  secret_id     = aws_secretsmanager_secret.tdx_app_key.id
  secret_string = var.tdx_app_key
}

resource "aws_secretsmanager_secret_version" "langsmith_api_key_v" {
  secret_id     = aws_secretsmanager_secret.langsmith_api_key.id
  secret_string = var.langsmith_api_key
}