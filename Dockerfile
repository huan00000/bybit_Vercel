FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data
WORKDIR /app
RUN groupadd --gid 10001 bot && useradd --uid 10001 --gid bot --no-create-home bot \
    && mkdir /app/data && chown bot:bot /app/data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py polling.py ./
USER bot
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,time; from pathlib import Path; assert time.time()-float((Path(os.environ['DATA_DIR'])/'heartbeat').read_text()) < 120"
CMD ["python", "polling.py"]
