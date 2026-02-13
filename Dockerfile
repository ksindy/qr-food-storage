FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories (volume mount will overlay /data in production)
RUN mkdir -p /data/uploads

# Make entrypoint executable
RUN chmod +x fly-entrypoint.sh

EXPOSE 8000

CMD ["./fly-entrypoint.sh"]
