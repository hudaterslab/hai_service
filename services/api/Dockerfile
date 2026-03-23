FROM python:3.12-slim

WORKDIR /app
ENV PROJECT_ROOT=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    build-essential \
    cmake \
    ninja-build \
    git \
    pkg-config \
  && rm -rf /var/lib/apt/lists/*

COPY services/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY services/api/app /app/app
COPY services/delivery/app /app/delivery_app
COPY services/api/entrypoint.sh /app/entrypoint.sh
COPY scripts/linux/vmsctl.py /usr/local/bin/vmsctl
COPY config /app/config

RUN chmod +x /app/entrypoint.sh /usr/local/bin/vmsctl

EXPOSE 8080
CMD ["/app/entrypoint.sh"]
