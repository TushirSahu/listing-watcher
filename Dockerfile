# Runs the agent in an isolated container: the agent's model-written code can't see your files.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
WORKDIR /app
COPY pyproject.toml README.md ./
COPY watcher ./watcher
RUN pip install --no-cache-dir '.[agent]'

USER app
ENV ANONYMIZED_TELEMETRY=false PYTHONUNBUFFERED=1 HOME=/home/app
EXPOSE 8000
# 0.0.0.0 inside the container only; docker-compose publishes it to 127.0.0.1 on your machine
CMD ["thrift-scout", "web", "--host", "0.0.0.0"]
