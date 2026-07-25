# Build context = raíz del repo (ver docker-compose.yml) para poder incluir el
# paquete compartido `common/`, las migraciones y los scripts.
FROM python:3.10-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY openAIService/requirements.txt /app/
RUN pip install -r requirements.txt

COPY common/ /app/common/
COPY alembic.ini /app/
COPY migrations/ /app/migrations/
COPY scripts/ /app/scripts/

COPY openAIService/langchainService.py /app/
COPY openAIService/clients_map.json /app/
COPY openAIService/modules/ /app/modules/
COPY openAIService/prompts/ /app/prompts/
COPY openAIService/entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENV FLASK_APP=langchainService.py
ENV FLASK_ENV=development
# El entrypoint aplica las migraciones antes de arrancar (deploy sin pasos manuales).
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "langchainService.py"]
