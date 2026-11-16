FROM python:3.11-slim

# Processed image stacks (cache) and uploaded datasets live under /data; mount a volume there
# to keep them across restarts (docker-compose.yml does).
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CACHE_DIR=/data/cache UPLOAD_DIR=/data/uploads
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
