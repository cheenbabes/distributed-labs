FROM python:3.11-slim

WORKDIR /lab

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple/

# Create directories for mounted volumes
RUN mkdir -p /lab/notebooks /lab/solutions

EXPOSE 8888

CMD ["jupyter", "lab", \
     "--ip=0.0.0.0", \
     "--port=8888", \
     "--no-browser", \
     "--allow-root", \
     "--IdentityProvider.token=''", \
     "--ServerApp.root_dir=/lab"]
