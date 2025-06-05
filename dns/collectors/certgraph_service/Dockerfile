FROM python:3.9-slim

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    git \
    golang \
    ca-certificates \
    openssl \
    curl \
    net-tools \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

# Configurar Go para certgraph
ENV PATH=$PATH:/root/go/bin
ENV GOPATH=/root/go
ENV GO111MODULE=on

# Instalar versión latest de certgraph (como en tu versión original)
RUN go install github.com/lanrat/certgraph@latest && \
    certgraph -version || echo "Certgraph instalado correctamente"

# Workdir y configuración de la aplicación
WORKDIR /app

# Copiar requirements e instalar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY . .

# El CMD se dejará vacío para que lo maneje docker-compose