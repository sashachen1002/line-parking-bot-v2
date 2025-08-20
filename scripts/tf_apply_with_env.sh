#!/usr/bin/env bash
set -euo pipefail

# Purpose: Load .env and pass secrets to Terraform via TF_VAR_*, then init/plan/apply

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform"

# Load .env into current shell
set -a
source "$ROOT_DIR/.env"
set +a

# Map to Terraform variables
export TF_VAR_tdx_app_id="${TDX_APP_ID:-}"
export TF_VAR_tdx_app_key="${TDX_APP_KEY:-}"
export TF_VAR_langsmith_api_key="${LANGSMITH_API_KEY:-}"

if [[ -z "${TF_VAR_tdx_app_id}" || -z "${TF_VAR_tdx_app_key}" || -z "${TF_VAR_langsmith_api_key}" ]]; then
  echo "[error] Missing one or more required envs: TDX_APP_ID / TDX_APP_KEY / LANGSMITH_API_KEY" >&2
  exit 1
fi

cd "$TF_DIR"
terraform init
terraform plan
terraform apply


