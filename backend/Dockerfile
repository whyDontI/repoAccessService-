FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY scripts scripts

ENV PYTHONPATH=/app

# Seeding is idempotent (skips if data already exists -- see
# generate_fixture.py), so this is safe to run on every container start,
# including against a volume that already has data from a previous run.
CMD python scripts/generate_fixture.py && \
    uvicorn app.main:app --host 0.0.0.0 --port 8000
