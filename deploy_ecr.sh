export AWS_REGION=ap-northeast-1

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

aws ecr get-login-password --region "$AWS_REGION" \
| docker login --username AWS --password-stdin "$REGISTRY"

docker buildx build --platform linux/amd64 -t line-agent-agent:latest --load -f agent/Dockerfile .
docker tag line-agent-agent "$REGISTRY"/line-agent-agent:latest