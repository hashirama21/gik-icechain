FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/hashirama21/gik-icechain"
LABEL org.opencontainers.image.description="GIK-IceChain flood risk pipeline"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev libhdf5-dev libnetcdf-dev \
    libeccodes-dev eccodes \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY configs/ ./configs/
COPY scripts/ ./scripts/

RUN pip install --no-cache-dir ".[cloud]"

RUN useradd --no-create-home --shell /bin/false gik
USER gik

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    HDF5_USE_FILE_LOCKING=FALSE

ENTRYPOINT ["gik-icechain"]
CMD ["--help"]
