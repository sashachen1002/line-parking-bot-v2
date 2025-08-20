FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml .
RUN pip install --upgrade pip && pip install fastmcp fastapi uvicorn

COPY mcp_server/ .
EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:9000/health || exit 1

CMD ["python", "weather_mcp.py"]