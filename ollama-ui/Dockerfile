FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 libpq-dev build-essential && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    apt-get purge -y build-essential && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY app.py /app/app.py

ENV PORT=7861

CMD ["python", "app.py"]
