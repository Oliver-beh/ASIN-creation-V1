#!/usr/bin/env bash
# Deploy bdf-vc-upload to Cloud Run. Run from the project root.
#
#   ./deploy.sh                          # regression gate, then deploy
#   BDF_FIXTURES=~/bdf_fixtures ./deploy.sh
#   ./deploy.sh --skip-regress           # only when you know why
#
# The regression gate is plan §8: 64 products, cell-by-cell against the verified
# human uploads. A rule change that breaks it must not reach the shared URL.
set -euo pipefail

PROJECT="${PROJECT:-}"
SERVICE="${SERVICE:-bdf-vc-upload}"
REGION="${REGION:-europe-west3}"
MIN_INSTANCES="${MIN_INSTANCES:-0}"

SKIP_REGRESS=0
[[ "${1:-}" == "--skip-regress" ]] && SKIP_REGRESS=1

if [[ -z "$PROJECT" ]]; then
  PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "No project set. Run: gcloud config set project <PROJECT_ID>   (or PROJECT=... ./deploy.sh)" >&2
  exit 1
fi

# ---------------------------------------------------------------- pre-deploy
if [[ "$SKIP_REGRESS" -eq 0 ]]; then
  echo "==> Regression check (cell-by-cell against the verified uploads)"
  if ! python3 tools/regress.py; then
    echo
    echo "Regression check failed or fixtures are missing. Fix it, or re-run with" >&2
    echo "  ./deploy.sh --skip-regress" >&2
    exit 1
  fi
  echo
  echo "Read the OVERALL line above. If the match rate dropped, stop and investigate."
  read -r -p "Proceed with deploy? [y/N] " ok
  [[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "Aborted."; exit 1; }
else
  echo "==> Skipping the regression check (--skip-regress)"
fi

# The footer version stamp, so anyone can confirm what is live.
BUILD_ID="$(date -u +%Y%m%d-%H%M)"
if git rev-parse --short HEAD >/dev/null 2>&1; then
  BUILD_ID="${BUILD_ID}-$(git rev-parse --short HEAD)"
  if [[ -n "$(git status --porcelain)" ]]; then
    BUILD_ID="${BUILD_ID}-dirty"
  fi
fi
BUILD_DATE="$(date -u +%Y-%m-%d)"

echo "==> Deploying $SERVICE to $REGION in $PROJECT (build $BUILD_ID)"

# --session-affinity: Streamlit talks over a websocket and must stay on one instance.
# --no-allow-unauthenticated: IAP is the only way in.
# --ingress all: required for IAP's direct Cloud Run integration.
# BUILD_ID/BUILD_DATE go in as runtime env vars — `gcloud run deploy --source`
# has no --build-arg, and app.py reads them from the environment anyway.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT" \
  --source . \
  --region "$REGION" \
  --memory 1Gi --cpu 1 \
  --min-instances "$MIN_INSTANCES" \
  --timeout 300 \
  --session-affinity \
  --ingress all \
  --no-allow-unauthenticated \
  --set-env-vars "BUILD_ID=${BUILD_ID},BUILD_DATE=${BUILD_DATE}"

echo
echo "Deployed. URL:"
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --format 'value(status.url)'
echo
echo "Opening that URL directly gives 403 until IAP is enabled — see DEPLOY_CLOUDRUN.md §3."
