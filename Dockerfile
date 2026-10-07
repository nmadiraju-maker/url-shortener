# Runtime image for the URL shortener service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    URLSHORT_DB_PATH=/data/urlshort.db
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY urlshort ./urlshort
COPY config/uvicorn-logging.json ./config/uvicorn-logging.json
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# --no-access-log: the app already writes one structured JSON line per request.
# --log-config: uvicorn's own lines are JSON too, so every line of output is machine-readable.
CMD ["uvicorn", "urlshort.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", \
     "--log-config", "config/uvicorn-logging.json"]
