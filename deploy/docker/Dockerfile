FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/hashirama21/GIK-IceChain"
LABEL org.opencontainers.image.description="GIK-IceChain flood risk pipeline"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libhdf5-dev libnetcdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[cloud]"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HDF5_USE_FILE_LOCKING=FALSE

ENTRYPOINT ["python", "-m", "gik_icechain"]
CMD ["--help"]
