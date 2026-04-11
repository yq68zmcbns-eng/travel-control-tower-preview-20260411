FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRAVEL_WEB_HOST=0.0.0.0 \
    TRAVEL_WEB_PORT=8770 \
    TRAVEL_WEB_DATA_DIR=/app/.runtime-data \
    FLYAI_CMD=flyai

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @fly-ai/flyai-cli \
    && npm cache clean --force \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY travel_control_tower/requirements-web.txt /tmp/requirements-web.txt
RUN pip install --no-cache-dir -r /tmp/requirements-web.txt

COPY . /app

EXPOSE 8770

CMD ["python", "-m", "travel_control_tower.run_web"]
