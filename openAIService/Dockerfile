FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt /app
RUN pip install -r requirements.txt

COPY langchainService.py /app
COPY modules/ /app/modules/
COPY prompts/ /app/prompts/

EXPOSE 8000

CMD ["python", "langchainService.py"]
