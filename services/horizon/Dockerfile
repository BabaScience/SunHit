# Build context: repository root.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv PATH=/venv/bin:$PATH \
    NUMBA_CACHE_DIR=/tmp/numba-cache
# rasterio wheels bundle GDAL but need the system expat library.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY libs/geo /app/libs/geo
COPY services/horizon /app/services/horizon
RUN cd /app/services/horizon && uv sync --no-dev --no-editable
# Warm the numba JIT cache so the first job is fast.
RUN python -c "import numpy as np; from sunhit_geo import RasterLayer, build_horizon; z=np.zeros((8,8),np.float32); L=lambda a: RasterLayer(a,0,8,1); build_horizon(4,4,1,0,L(z),L(z),L(z),far_terrain=L(z))"
EXPOSE 9100
CMD ["sunhit-horizon-worker"]
