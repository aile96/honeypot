FROM alpine:3.22

RUN apk add --no-cache curl jq coreutils nano bash kubectl python3 tini

# Evita bytecode e buffer per log immediati
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCRIPT_NAME="synthetic-log.sh" \
    RUN_EVERY=0

WORKDIR /app

# Copiamo il controller e gli script già presenti nel repo
COPY controller.py /app/controller.py
COPY scripts/ /app/scripts/

# Garantiamo il bit di esecuzione agli script (se vuoi)
RUN chmod -R a+rx /app/scripts

ENTRYPOINT ["/sbin/tini","--"]
CMD ["python3","/app/controller.py"]
