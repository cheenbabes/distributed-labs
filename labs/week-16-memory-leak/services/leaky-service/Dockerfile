FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Set Python to not buffer stdout/stderr for better logging
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
