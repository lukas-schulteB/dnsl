FROM python:3.10-slim

WORKDIR /app

# 1) dependencias OS
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl wget unzip ca-certificates git golang-go \
    && rm -rf /var/lib/apt/lists/*

# 2) Go env
ENV GOPATH=/root/go
ENV PATH=$GOPATH/bin:$PATH

# 3) subfinder
RUN wget -q https://github.com/projectdiscovery/subfinder/releases/download/v2.7.0/subfinder_2.7.0_linux_amd64.zip \
  && unzip subfinder_2.7.0_linux_amd64.zip \
  && mv subfinder /usr/local/bin/ \
  && chmod +x /usr/local/bin/subfinder \
  && rm subfinder_2.7.0_linux_amd64.zip

# 4) assetfinder
RUN go install github.com/tomnomnom/assetfinder@latest

# 5) deps Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6) copiar código
COPY . .

# 7) directorio tmp para subprocesos
RUN mkdir -p /tmp

# CMD lo pone docker-compose, así que no hace falta aquí
CMD celery -A tasks worker --loglevel=info & \
    celery -A tasks call tasks.worker_loop
