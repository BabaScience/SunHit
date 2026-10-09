# Build context: repository root.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv PATH=/venv/bin:$PATH
# rasterio wheels bundle GDAL but need the system expat library.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY libs/geo /app/libs/geo
COPY services/dsm-builder /app/services/dsm-builder
RUN cd /app/services/dsm-builder && uv sync --no-dev --no-editable
COPY infra/regions /app/infra/regions
CMD ["sunhit-dsm-build", "--help"]
