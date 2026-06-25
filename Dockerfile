FROM python:3.12-slim

RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

RUN mkdir -p /app/data /app/app/static/uploads

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers ${WORKERS:-2} \
    --timeout 30 \
    --access-logfile - \
    --error-logfile -"]
