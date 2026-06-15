import os
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routers import scraper, empresas, relatorios, jobs, cidades, diagnostico

app = FastAPI(
    title="TAC PCD Agent API",
    description="Agente de identificação de TACs relacionados a PCDs - MPT-SP",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scraper.router,     prefix="/api/scraper",    tags=["Scraper"])
app.include_router(empresas.router,    prefix="/api/empresas",   tags=["Empresas"])
app.include_router(relatorios.router,  prefix="/api/relatorios", tags=["Relatórios"])
app.include_router(jobs.router,        prefix="/api/jobs",       tags=["Jobs"])
app.include_router(cidades.router,     prefix="/api",            tags=["Cidades"])
app.include_router(diagnostico.router, prefix="/api/diagnostico", tags=["Diagnóstico"])

@app.get("/health")
def health():
    return {"status": "ok", "service": "tac-pcd-agent"}

# --- ADICIONE ESTA PARTE FINAL PARA O CLOUD RUN ---
if __name__ == "__main__":
    # O Google Cloud injeta a porta na variável de ambiente PORT. 
    # Se não existir, usamos a 8080 como padrão.
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port)