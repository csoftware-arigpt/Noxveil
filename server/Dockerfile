FROM python:3.11-slim

# Install cloudflared
RUN apt-get update && apt-get install -y curl && \
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared && \
    chmod +x /usr/local/bin/cloudflared && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY server/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY server /app/server
COPY web-ui /app/web-ui
COPY agent /app/agent

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 1324

# Set Python path
ENV PYTHONPATH=/app

# Default command
CMD ["python", "-m", "server.main"]
