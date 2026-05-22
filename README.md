# PCD_Agent
Agente IA que identifica empresas com TAC PCD nos portais do MPT e gera leads qualificados para prospecção de candidatos com deficiência.


Agente interno da SoulCode para identificação automática de empresas com 
Termo de Ajuste de Conduta (TAC) relacionado à Lei de Cotas para Pessoas 
com Deficiência (Lei 8.213/91).

## O que faz
- Raspa portais do Ministério Público do Trabalho (MPT)
- Identifica empresas com déficit de PCDs via Vertex AI (Gemini 1.5 Pro)
- Gera score de oportunidade de prospecção (0-10)
- Emite fichas em PDF  e Excel por empresa
- Executa automaticamente toda segunda-feira às 7h

## Stack
- Backend: Python · FastAPI · Vertex AI · Firestore · Cloud Storage
- Frontend: HTML · CSS · JS · nginx
- Infra: Google Cloud Run · Cloud Scheduler · Docker

## Deploy
Hospedado no Google Cloud Run — projeto devsprojects-af12e
