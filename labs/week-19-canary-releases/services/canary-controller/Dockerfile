FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Create nginx config directory (shared via volume)
RUN mkdir -p /etc/nginx/conf.d

CMD ["python", "main.py"]
