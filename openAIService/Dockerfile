FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt /app
RUN pip install -r requirements.txt

COPY langchainService.py db.py prospector.py panel.html /app/
COPY midi/ /app/midi/

# La base de datos vive en /data: en Railway o Docker montá un volumen ahí.
ENV DB_PATH=/data/prospector.db

EXPOSE 8000

CMD ["python", "langchainService.py"]
