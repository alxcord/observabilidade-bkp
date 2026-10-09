# Build context é a raiz do repositório (buildContext em azure-pipelines.yml
# deste projeto), não esta pasta — por isso todo COPY abaixo é um caminho
# completo a partir da raiz. É o que permite copiar
# src/shared/event_resolution.py, que vive fora daqui e é compartilhado com
# o publicador (src/hub-publisher/), sem duplicar o arquivo dentro
# de cada projeto — a mesma exceção que sync-expectations já usa para
# config/executions/manual-expectations.yaml.

FROM python:3.11-slim

# Driver ODBC 18: necessário para o pyodbc falar com o Azure SQL.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg apt-transport-https \
 && curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && apt-get purge -y curl gnupg apt-transport-https \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/observability-collector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/observability-collector/collector/ ./collector/

# Único ponto de resolução de incidente, compartilhado com o publicador — nunca
# duplicado no texto. Pousa dentro de collector/ porque __main__.py já
# coloca esse diretório em sys.path, e os módulos daqui usam import flat
# (import event_resolution, não from . import event_resolution).
COPY src/shared/event_resolution.py ./collector/event_resolution.py

CMD ["python", "-m", "collector"]
