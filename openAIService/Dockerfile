FROM python:3.10-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app
RUN pip install -r requirements.txt

COPY langchainService.py /app
COPY clients_map.json /app
COPY modules/ /app/modules/
COPY prompts/ /app/prompts/

EXPOSE 8000

ENV FLASK_APP=langchainService.py
ENV FLASK_ENV=development
CMD ["python", "langchainService.py"]
