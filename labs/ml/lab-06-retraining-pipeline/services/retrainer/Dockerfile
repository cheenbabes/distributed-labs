FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy retraining script
COPY retrain.py .

# Create directories for model and data
RUN mkdir -p /app/model /app/data

CMD ["python", "retrain.py"]
