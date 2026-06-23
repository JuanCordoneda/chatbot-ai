FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8502

ENV FLASK_APP=app.py
ENV FLASK_ENV=development
CMD ["flask", "run", "--host=0.0.0.0", "--port=8502", "--reload"]
