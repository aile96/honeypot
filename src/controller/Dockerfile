FROM alpine:3.22@sha256:55ae5d250caebc548793f321534bc6a8ef1d116f334f18f4ada1b2daad3251b2

RUN apk add --no-cache curl=8.14.1-r2 jq=1.8.1-r0 coreutils=9.7-r1 nano=8.4-r0 bash=5.2.37-r0 kubectl=1.33.1-r5 python3=3.12.12-r0 tini=0.19.0-r3

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
