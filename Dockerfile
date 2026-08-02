FROM python:3.11-slim

# print()-ის output-მა Docker log stream-მდე დაუყოვნებლივ რომ მიაღწიოს
# (buffering-ის გარეშე) — ეს განსაკუთრებით მნიშვნელოვანია onec_sync_worker.py-სთვის
# და sync_worker.py-სთვის, რომლებიც plain print()-ს იყენებენ ლოგირებისთვის.
ENV PYTHONUNBUFFERED=1

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
COPY onec_sync_worker.py .
COPY create_admin.py .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]