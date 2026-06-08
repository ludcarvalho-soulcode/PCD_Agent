FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Dependências extras para lxml e reportlab
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
ENV GCP_PROJECT_ID=devsprojects-af12e
ENV GCP_LOCATION=us-central1
ENV GCS_BUCKET_NAME=devsprojects-af12e-tac-pcd
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
