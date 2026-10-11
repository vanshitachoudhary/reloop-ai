#!/usr/bin/env bash
# One-command deploy: builds, deploys the SAM stack and writes the API URL into frontend/config.js
set -euo pipefail
REGION="${AWS_REGION:-ap-south-1}"; STACK="${STACK:-reloop-ai}"; ORIGIN="${ORIGIN:-*}"
sam build
sam deploy --stack-name "$STACK" --region "$REGION" --resolve-s3 --capabilities CAPABILITY_IAM \
  --no-confirm-changeset --no-fail-on-empty-changeset --parameter-overrides "AllowedOrigin=$ORIGIN"
URL=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
echo "window.RELOOP_API = \"$URL\";" > frontend/config.js
echo "Deployed. API: $URL"
echo "Now commit frontend/config.js and push so GitHub Pages picks it up."
