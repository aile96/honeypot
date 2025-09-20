FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl jq \
    coreutils nano \
    && rm -rf /var/lib/apt/lists/*

# Evita bytecode e buffer per log immediati
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copiamo il controller e gli script già presenti nel repo
COPY controller.py /app/controller.py
COPY scripts/ /app/scripts/

# Garantiamo il bit di esecuzione agli script (se vuoi)
RUN chmod -R a+rx /app/scripts

CMD ["python", "/app/controller.py"]
