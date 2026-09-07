FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOMESERVER_RELAY_DATA_DIR=/data

WORKDIR /srv

COPY relay/requirements.txt /srv/relay/requirements.txt
RUN pip install --no-cache-dir -r /srv/relay/requirements.txt

COPY relay /srv/relay

RUN useradd --create-home --uid 10001 relay \
    && mkdir -p /data \
    && chown -R relay:relay /data /srv

USER relay

VOLUME ["/data"]
EXPOSE 8080

CMD ["uvicorn", "relay.app:app", "--host", "0.0.0.0", "--port", "8080", "--ws-max-size", "262144", "--no-server-header"]
