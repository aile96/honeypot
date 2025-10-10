FROM alpine:3.22

RUN apk add --no-cache curl jq coreutils nano bash kubectl python3 tini

# Avoid bytecode and buffer for immediate log
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCRIPT_NAME="synthetic-log.sh" \
    RUN_EVERY=0

WORKDIR /app

COPY controller.py /app/controller.py
COPY scripts/ /app/scripts/
RUN chmod -R a+rx /app/scripts

ENTRYPOINT ["/sbin/tini","--"]
CMD ["python3","/app/controller.py"]
