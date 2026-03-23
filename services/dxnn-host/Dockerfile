FROM python:3.11-slim

WORKDIR /app

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

COPY scripts/linux/dxnn_host_infer_service.py /app/dxnn_host_infer_service.py
COPY services/dxnn-host/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

EXPOSE 18081
CMD ["/app/entrypoint.sh"]
