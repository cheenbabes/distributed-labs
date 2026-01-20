FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# PORT is set via environment variable
CMD ["sh", "-c", "python main.py"]
