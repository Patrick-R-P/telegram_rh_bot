FROM python:3.11-slim

# Variáveis de ambiente para o Python
# PYTHONDONTWRITEBYTECODE: Não gerar arquivos .pyc
# PYTHONUNBUFFERED: Não bufferizar o stdout/stderr (melhor para logs no Docker)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Define o diretório de trabalho na imagem
WORKDIR /app

# Copia e instala as dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do código da aplicação
COPY . .

# Comando padrão para rodar o bot
CMD ["python", "bot.py"]

