#!/bin/bash
# Deploy Stock Screener Website to Amazon ECS Express Mode

set -e

APP_NAME="stock-screener-web"
AWS_REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${APP_NAME}"

echo "======================================================"
echo " Deploying Website to Amazon ECR (for ECS Express) "
echo "======================================================"

# 1. Create ECR Repository if it doesn't exist
echo "[1/4] Checking ECR Repository..."
if ! aws ecr describe-repositories --repository-names "${APP_NAME}" --region "${AWS_REGION}" &>/dev/null; then
    echo "Creating ECR Repository: ${APP_NAME}..."
    aws ecr create-repository --repository-name "${APP_NAME}" --region "${AWS_REGION}"
fi

# 2. Login to ECR
echo "[2/4] Logging into Amazon ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# 3. Build and Push Docker Image
echo "[3/4] Building and pushing Docker image (this may take a few minutes)..."
docker build --platform linux/amd64 -t "${APP_NAME}:latest" .
docker tag "${APP_NAME}:latest" "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"

echo "[4/4] Image successfully pushed to ECR: ${ECR_URI}:latest"
echo ""
echo "Next step: Go to the Amazon ECS Console and use the new 'Express Mode' to deploy this image."
