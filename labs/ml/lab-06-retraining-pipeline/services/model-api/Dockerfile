FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY train_model.py .
COPY start.sh .
RUN chmod +x start.sh

# Create directories for model and data
RUN mkdir -p /app/model /app/data

EXPOSE 8000

CMD ["./start.sh"]
