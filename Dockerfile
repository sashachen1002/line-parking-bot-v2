# 1) 拿 Lambda Web Adapter 二進位
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 AS adapter

# 2) Lambda Python base image
FROM public.ecr.aws/lambda/python:3.12
WORKDIR /var/task

# 3) 安裝相依
RUN pip install --no-cache-dir \
    Flask \
    line-bot-sdk==3.* \
    python-dotenv \
    requests \
    gunicorn

# 4) 複製程式碼（確保 .dockerignore 已排除 .env）
COPY . .

# 5) 啟用 Web Adapter（做為 Lambda Extension）
COPY --from=adapter /lambda-adapter /opt/extensions/lambda-adapter

# 6) 讓你的 HTTP 伺服器聽這個埠
ENV PORT=8000

# 7) 覆寫 ENTRYPOINT，避免要求 handler 參數
ENTRYPOINT ["/usr/bin/env"]

# 8) 直接啟動 gunicorn；假設 Flask 實例在 main.py 裡叫 app
CMD ["bash", "-lc", "gunicorn -w 2 -b 0.0.0.0:${PORT} main:app"]