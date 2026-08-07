# One image: Python serves the API and the built React bundle as static files.
# No nginx, no second container — single user on a NAS.

FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FORGE_LIBRARY_PATH=/library \
    FORGE_DATA_PATH=/data

# libgomp is needed by Pillow's SIMD paths on some bases; curl is for HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
COPY backend/app ./app
# The bundle is copied in before the install so it ships inside the package —
# one installed copy, rather than a source tree shadowing an installed one.
COPY --from=frontend /build/dist ./app/static
RUN pip install --no-cache-dir . && rm -rf /app/app /app/pyproject.toml

# Run from a directory with no `app/` in it, so the import is unambiguous.
WORKDIR /srv

# The library is the product; /data is a rebuildable cache.
VOLUME ["/library", "/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
