#!/usr/bin/env bash
set -euo pipefail

# Usage:
# ./infra/aws/deploy_fargate.sh <aws-region> <aws-account-id> <cluster-name> <service-name>

REGION="${1:-}"
ACCOUNT_ID="${2:-}"
CLUSTER="${3:-polymarket-bot-cluster}"
SERVICE="${4:-polymarket-bot-service}"
REPO="polymarket-bot"
IMAGE_TAG="latest"

if [[ -z "${REGION}" || -z "${ACCOUNT_ID}" ]]; then
  echo "Usage: $0 <aws-region> <aws-account-id> <cluster-name> <service-name>"
  exit 1
fi

IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO}:${IMAGE_TAG}"

aws ecr describe-repositories --repository-names "${REPO}" --region "${REGION}" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "${REPO}" --region "${REGION}" >/dev/null

aws ecr get-login-password --region "${REGION}" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

docker build -t "${REPO}:${IMAGE_TAG}" .
docker tag "${REPO}:${IMAGE_TAG}" "${IMAGE_URI}"
docker push "${IMAGE_URI}"

echo "Register a task definition from infra/aws/ecs-task-def.json.template after filling placeholders."
echo "Then run:"
echo "aws ecs update-service --cluster ${CLUSTER} --service ${SERVICE} --force-new-deployment --region ${REGION}"
