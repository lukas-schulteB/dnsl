FROM python:3.9-slim

# Install only necessary system dependencies
RUN apt-get update && apt-get install -y \
    lynx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY . .

# Create temp directory
RUN mkdir -p /tmp/collectors

CMD celery -A tasks worker --loglevel=info & \
    celery -A tasks call tasks.worker_loop