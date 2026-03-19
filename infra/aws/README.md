# AWS Fargate Deployment

## Prerequisites
- AWS CLI configured
- Docker installed
- ECS cluster + service created
- Task roles:
  - execution role with ECR pull + CloudWatch logs
  - task role with SSM read access for `/polymarket-bot/*`

## Secrets in SSM Parameter Store
Create these secure string parameters:
- `/polymarket-bot/private-key`
- `/polymarket-bot/funder-address`
- `/polymarket-bot/api-key`
- `/polymarket-bot/secret`
- `/polymarket-bot/passphrase`

## Deploy
1. Fill `infra/aws/ecs-task-def.json.template` placeholders.
2. Register task definition:
   - `aws ecs register-task-definition --cli-input-json file://infra/aws/ecs-task-def.json.template --region <REGION>`
3. Build and push image:
   - `./infra/aws/deploy_fargate.sh <REGION> <ACCOUNT_ID> <CLUSTER> <SERVICE>`
4. Force service redeploy:
   - `aws ecs update-service --cluster <CLUSTER> --service <SERVICE> --force-new-deployment --region <REGION>`

## Runtime checks
- CloudWatch log group: `/ecs/polymarket-bot`
- Confirm heartbeat logs and active quote loop entries.
