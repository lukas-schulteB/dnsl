FROM python:3.9-slim

# Install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    celery \
    pymongo \
    dnspython \
    redis

# Copy the application code
COPY . /app/

# Set Python path
ENV PYTHONPATH=/app

# Usar import_domains.py
CMD ["python", "import_domains.py", "/app/RISP_OTROS.csv"]