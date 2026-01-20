FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY protos/ ./protos/

# Generate gRPC code
RUN python -m grpc_tools.protoc -I./protos --python_out=. --grpc_python_out=. ./protos/ratelimiter.proto

# Expose REST (8000) and gRPC (50051) ports
EXPOSE 8000 50051

CMD ["python", "main.py"]
