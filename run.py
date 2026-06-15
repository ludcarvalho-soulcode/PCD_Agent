from dotenv import load_dotenv
load_dotenv()
import uvicorn
import sys
import asyncio

if __name__ == "__main__":
    # Garante a política ANTES de qualquer coisa para evitar o NotImplementedError
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    # Inicia o servidor apontando para o app dentro do main.py
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)