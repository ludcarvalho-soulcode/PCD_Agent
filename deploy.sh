#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  SoulCode — Agente TAC PCD · Deploy                        ║
# ║  Projeto: devsprojects-af12e                                ║
# ║  Serviços: Cloud Run (backend + frontend) + GCP             ║
# ╚══════════════════════════════════════════════════════════════╝
set -e

PROJECT_ID="tutores-lms"
REGION="us-central1"
BACKEND_SVC="tac-pcd-backend"
FRONTEND_SVC="tac-pcd-frontend"
SA_NAME="tac-pcd-agent-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET="${PROJECT_ID}-tac-pcd"
BACKEND_IMG="gcr.io/${PROJECT_ID}/${BACKEND_SVC}:latest"
FRONTEND_IMG="gcr.io/${PROJECT_ID}/${FRONTEND_SVC}:latest"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

step() { echo -e "\n${BOLD}${CYAN}── $1${NC}"; }
ok()   { echo -e "  ${GREEN}✓ $1${NC}"; }
warn() { echo -e "  ${YELLOW}⚠ $1${NC}"; }
info() { echo -e "    $1"; }
err()  { echo -e "  ${RED}✗ ERRO: $1${NC}"; exit 1; }

echo -e "\n${BOLD}SoulCode · Agente TAC PCD — Deploy${NC}"
echo -e "Projeto: ${CYAN}${PROJECT_ID}${NC}  Região: ${CYAN}${REGION}${NC}\n"
read -p "Iniciar deploy? [s/N] " -n 1 -r; echo
[[ $REPLY =~ ^[Ss]$ ]] || exit 0

# ──────────────────────────────────────────────────────────────
step "1/7  Pré-requisitos"
command -v gcloud &>/dev/null || err "gcloud não instalado → https://cloud.google.com/sdk/docs/install"
ok "gcloud $(gcloud version --format='value(Google Cloud SDK)' 2>/dev/null)"
command -v docker &>/dev/null || err "Docker não instalado → https://docs.docker.com/get-docker/"
ok "$(docker --version)"
ACCOUNT=$(gcloud config get-value account 2>/dev/null)
[[ -z "$ACCOUNT" ]] && err "Execute: gcloud auth login"
ok "Autenticado: ${ACCOUNT}"

# ──────────────────────────────────────────────────────────────
step "2/7  Projeto & APIs"
gcloud config set project "${PROJECT_ID}" --quiet
gcloud projects describe "${PROJECT_ID}" --quiet &>/dev/null \
  || err "Projeto '${PROJECT_ID}' não encontrado ou sem permissão"
ok "Projeto: ${PROJECT_ID}"

info "Habilitando APIs (pode levar ~1 min na primeira vez)..."
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  cloudscheduler.googleapis.com \
  aiplatform.googleapis.com \
  containerregistry.googleapis.com \
  iam.googleapis.com \
  --project="${PROJECT_ID}" --quiet
ok "APIs habilitadas"

# ──────────────────────────────────────────────────────────────
step "3/7  Service Account"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --quiet &>/dev/null; then
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="TAC PCD Agent" \
    --project="${PROJECT_ID}" --quiet
  ok "Service Account criada"
else
  ok "Service Account já existe"
fi

for ROLE in roles/datastore.user roles/storage.objectAdmin roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" --role="${ROLE}" --quiet &>/dev/null
  ok "${ROLE}"
done

# ──────────────────────────────────────────────────────────────
step "4/7  Cloud Storage"
if ! gsutil ls -b "gs://${BUCKET}" &>/dev/null; then
  gsutil mb -p "${PROJECT_ID}" -l US "gs://${BUCKET}"
  printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":90}}]}' > /tmp/lc.json
  gsutil lifecycle set /tmp/lc.json "gs://${BUCKET}"
  ok "Bucket criado: gs://${BUCKET} (PDFs expiram em 90 dias)"
else
  ok "Bucket já existe: gs://${BUCKET}"
fi

# ──────────────────────────────────────────────────────────────
step "5/7  Firestore"
FSDB=$(gcloud firestore databases list --project="${PROJECT_ID}" --format="value(name)" 2>/dev/null | head -1)
if [[ -z "$FSDB" ]]; then
  gcloud firestore databases create \
    --project="${PROJECT_ID}" --location=nam5 --type=firestore-native --quiet 2>/dev/null \
    && ok "Firestore criado" || warn "Firestore em criação — verifique o Console se der erro"
else
  ok "Firestore ativo"
fi

# ──────────────────────────────────────────────────────────────
step "6/7  Backend — build & deploy"
gcloud auth configure-docker --quiet

info "docker build backend..."
docker build -t "${BACKEND_IMG}" ./backend
ok "Build concluído"

info "docker push..."
docker push "${BACKEND_IMG}"
ok "Push concluído"

info "gcloud run deploy ${BACKEND_SVC}..."
gcloud run deploy "${BACKEND_SVC}" \
  --image="${BACKEND_IMG}" \
  --region="${REGION}" \
  --platform=managed \
  --memory=2Gi --cpu=2 \
  --timeout=900 \
  --min-instances=0 --max-instances=5 \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GCP_LOCATION=${REGION},GCS_BUCKET_NAME=${BUCKET}" \
  --service-account="${SA_EMAIL}" \
  --allow-unauthenticated \
  --quiet

BACKEND_URL=$(gcloud run services describe "${BACKEND_SVC}" \
  --region="${REGION}" --format="value(status.url)" 2>/dev/null)
ok "Backend: ${BACKEND_URL}"

# Health check
sleep 4
HC=$(curl -s -o /dev/null -w "%{http_code}" "${BACKEND_URL}/health" --max-time 15 || echo "000")
[[ "$HC" == "200" ]] && ok "Health check OK (/health → 200)" \
  || warn "Health check retornou ${HC} — aguarde o cold start"

# Scheduler semanal (toda segunda, 7h Brasília)
if ! gcloud scheduler jobs describe "tac-pcd-semanal" --location="${REGION}" --quiet &>/dev/null; then
  gcloud scheduler jobs create http "tac-pcd-semanal" \
    --location="${REGION}" \
    --schedule="0 7 * * 1" \
    --time-zone="America/Sao_Paulo" \
    --uri="${BACKEND_URL}/api/scraper/scheduler-trigger" \
    --http-method=POST \
    --oidc-service-account-email="${SA_EMAIL}" \
    --quiet 2>/dev/null && ok "Cloud Scheduler criado (toda 2ª, 7h)" \
    || warn "Scheduler não criado — ative App Engine primeiro se necessário"
fi

# ──────────────────────────────────────────────────────────────
step "7/7  Frontend — build & deploy"

# Injeta a URL real do backend no HTML
info "Injetando API URL no frontend..."
sed -i "s|https://SUA_API_CLOUD_RUN|${BACKEND_URL}|g" ./frontend/index.html
# Adiciona variável de config no head do HTML
sed -i "s|</head>|<script>window.API_URL='${BACKEND_URL}';</script></head>|" ./frontend/index.html
ok "API URL injetada: ${BACKEND_URL}"

info "docker build frontend..."
docker build -t "${FRONTEND_IMG}" ./frontend
ok "Build concluído"

info "docker push..."
docker push "${FRONTEND_IMG}"
ok "Push concluído"

info "gcloud run deploy ${FRONTEND_SVC}..."
gcloud run deploy "${FRONTEND_SVC}" \
  --image="${FRONTEND_IMG}" \
  --region="${REGION}" \
  --platform=managed \
  --memory=256Mi --cpu=1 \
  --min-instances=0 --max-instances=3 \
  --allow-unauthenticated \
  --quiet

FRONTEND_URL=$(gcloud run services describe "${FRONTEND_SVC}" \
  --region="${REGION}" --format="value(status.url)" 2>/dev/null)
ok "Frontend: ${FRONTEND_URL}"

# ──────────────────────────────────────────────────────────────
echo -e "\n${BOLD}${GREEN}✓ Deploy concluído!${NC}\n"
echo -e "  ${BOLD}Agente TAC PCD${NC}"
echo -e "  Frontend  →  ${CYAN}${FRONTEND_URL}${NC}"
echo -e "  Backend   →  ${CYAN}${BACKEND_URL}${NC}"
echo -e "  Swagger   →  ${CYAN}${BACKEND_URL}/docs${NC}"
echo -e "\n  ${BOLD}GCP Console${NC}"
echo -e "  Cloud Run     →  https://console.cloud.google.com/run?project=${PROJECT_ID}"
echo -e "  Firestore     →  https://console.cloud.google.com/firestore?project=${PROJECT_ID}"
echo -e "  Cloud Storage →  https://console.cloud.google.com/storage/browser/${BUCKET}"
echo -e "  Logs          →  https://console.cloud.google.com/logs?project=${PROJECT_ID}"
echo
