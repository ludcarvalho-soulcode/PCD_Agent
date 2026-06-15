import sys
import os

print("--- ONDE O PYTHON ESTÁ A TRABALHAR ---")
print(f"Diretório atual: {os.getcwd()}")
try:
    import services.vertex_service
    print(f"vertex_service encontrado em: {services.vertex_service.__file__}")
except Exception as e:
    print(f"Erro ao importar vertex_service: {e}")