FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MNEME_DB=/data/mneme.db

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN mkdir -p /data
EXPOSE 8080
CMD ["mneme", "--db", "/data/mneme.db", "serve", "--host", "0.0.0.0", "--port", "8080"]
