# Build context = raíz del repo (ver docker-compose.yml) para poder incluir el
# paquete compartido `common/`.
FROM python:3.10-slim

WORKDIR /app

COPY webService/requirements.txt .
RUN pip install -r requirements.txt

COPY common/ /app/common/
COPY webService/ /app/

EXPOSE 8502

ENV FLASK_APP=app.py
ENV FLASK_ENV=development
CMD ["flask", "run", "--host=0.0.0.0", "--port=8502", "--reload"]
