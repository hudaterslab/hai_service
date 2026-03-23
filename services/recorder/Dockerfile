FROM python:3.12-slim

WORKDIR /app
ENV PROJECT_ROOT=/app
ENV EVENT_PACKS_DIR=/app/config/event_packs

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    cmake \
    ninja-build \
    git \
    pkg-config \
  && rm -rf /var/lib/apt/lists/*

COPY services/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY services/recorder /app/recorder
COPY services/recorder/entrypoint.sh /app/entrypoint.sh
COPY config /app/config

RUN chmod +x /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]
