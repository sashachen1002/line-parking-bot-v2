#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$ROOT_DIR/terraform"

echo "[info] Loading Terraform outputs..."
AGENT_ECR_URL=$(cd "$TF_DIR" && terraform output -raw agent_ecr_url)
WEATHER_ECR_URL=$(cd "$TF_DIR" && terraform output -raw weather_mcp_ecr_url)
PROJECT_NAME=$(cd "$TF_DIR" && terraform output -raw project_name)
AWS_REGION=$(cd "$TF_DIR" && terraform output -raw aws_region)
ALB_DNS=$(cd "$TF_DIR" && terraform output -raw alb_dns_name || true)

REGISTRY_HOST="${AGENT_ECR_URL%%/*}"

echo "[info] AGENT_ECR_URL      = $AGENT_ECR_URL"
echo "[info] WEATHER_MCP_ECR_URL= $WEATHER_ECR_URL"
echo "[info] PROJECT_NAME       = $PROJECT_NAME"
echo "[info] AWS_REGION         = $AWS_REGION"

echo "[info] Logging in to ECR: $REGISTRY_HOST"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY_HOST"

echo "[info] Building agent image..."
docker build -f "$ROOT_DIR/agent/Dockerfile" -t "${PROJECT_NAME}-agent:latest" "$ROOT_DIR"
docker tag "${PROJECT_NAME}-agent:latest" "${AGENT_ECR_URL}:latest"
echo "[info] Pushing agent image..."
docker push "${AGENT_ECR_URL}:latest"

echo "[info] Building weather-mcp image..."
docker build -f "$ROOT_DIR/mcp_server/Dockerfile" -t "${PROJECT_NAME}-weather-mcp:latest" "$ROOT_DIR"
docker tag "${PROJECT_NAME}-weather-mcp:latest" "${WEATHER_ECR_URL}:latest"
echo "[info] Pushing weather-mcp image..."
docker push "${WEATHER_ECR_URL}:latest"

CLUSTER="${PROJECT_NAME}-cluster"
AGENT_SVC="${PROJECT_NAME}-agent-svc"
WEATHER_SVC="${PROJECT_NAME}-weather-mcp-svc"

echo "[info] Forcing ECS new deployment..."
aws ecs update-service --cluster "$CLUSTER" --service "$WEATHER_SVC" --force-new-deployment --region "$AWS_REGION" >/dev/null
aws ecs update-service --cluster "$CLUSTER" --service "$AGENT_SVC"    --force-new-deployment --region "$AWS_REGION" >/dev/null
echo "[info] Update triggered. It may take ~1-3 minutes to stabilize."

if [[ -n "${ALB_DNS:-}" ]]; then
  echo "[info] Checking ALB health endpoint..."
  if curl -sf "http://${ALB_DNS}/health" >/dev/null; then
    echo "[ok] Health check passed."
  else
    echo "[warn] Health check failed or ALB not ready."
  fi
  printf '[info] Test chat endpoint example:\n'
  printf 'curl "%s"\n' "http://${ALB_DNS}/chat?query=台北現在天氣如何"
fi

echo "[done] Images pushed and ECS deployments triggered."