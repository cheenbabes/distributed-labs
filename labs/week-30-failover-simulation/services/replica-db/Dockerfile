FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Default port, can be overridden by environment variable
ENV PORT=8002

CMD uvicorn main:app --host 0.0.0.0 --port $PORT
