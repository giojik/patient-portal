FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    firebird3.0-utils \
    fonts-dejavu-core \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app
COPY sync_worker.py .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]