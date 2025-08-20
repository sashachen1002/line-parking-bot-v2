# FROM python:3.12-slim

# ENV PYTHONDONTWRITEBYTECODE=1 \
#     PYTHONUNBUFFERED=1 \
#     PIP_NO_CACHE_DIR=1 \
#     PIP_DISABLE_PIP_VERSION_CHECK=1

# WORKDIR /app

# COPY pyproject.toml .
# RUN pip install --no-cache-dir --upgrade pip && \
#     pip install . --no-cache-dir

# COPY agent/ .

# EXPOSE 8000

# HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
#     CMD curl -f http://localhost:8000/health || exit 1

# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

# 系統層工具（curl 給 HEALTHCHECK 用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先裝依賴（加速層快取）
COPY pyproject.toml .
RUN pip install --upgrade pip \
 && pip install . --no-cache-dir \
 && pip install --no-cache-dir "uvicorn[standard]" gunicorn

# 放應用程式
COPY agent/ .

EXPOSE 8000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GUNICORN_WORKERS=1 \
    GUNICORN_TIMEOUT=120 \
    GUNICORN_KEEPALIVE=120

# 健康檢查：/health 要回 200
HEALTHCHECK --interval=60s --timeout=60s --start-period=5s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# 用 Gunicorn 托管 Uvicorn worker（可調整 workers / timeout）
CMD gunicorn main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --workers ${GUNICORN_WORKERS} \
  --timeout ${GUNICORN_TIMEOUT} \
  --graceful-timeout 30 \
  --keep-alive ${GUNICORN_KEEPALIVE} \
  --log-level info \
  --access-logfile - \
  --error-logfile -