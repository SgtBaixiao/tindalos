# syntax=docker/dockerfile:1
# Tindalos production image: single multi-stage build bundling the frontend
# (Vite/React) and the Python backend (FastAPI) into one runnable container.
# Build context = repository root:
#   docker build -f deploy/Dockerfile -t tindalos:deploy .
#
# The final image serves the SPA and the /api/* backend from one process
# (`tindalos web`), so the frontend dist is copied into the backend stage and
# served as static files via TINDALOS_FRONTEND_DIST.

# ---------------------------------------------------------------------------
# Stage 1: build the frontend static assets (Node 22 LTS, same image family as
# the CI reference .sandbox/web.Dockerfile).
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build

# Dependency layer first (cache-friendly: only reinstalls when the lockfile
# changes).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# Source layer, then a strict production build (tsc + vite build). Output is
# emitted to /build/dist (vite default outDir).
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python backend (FastAPI/uvicorn) + frontend dist + style guide.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS backend

WORKDIR /app

# Project metadata + source. README.md is required by pyproject.toml's readme
# field during the build. Src-first ordering keeps the pip layer cached.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the package itself (non-editable) with the web and llm extras.
# All heavy dependencies (chromadb, numpy, Pillow, pypdfium2) ship binary
# manylinux wheels, so no C compiler is needed on the slim base.
RUN pip install --no-cache-dir ".[web,llm]"

# LLM style-injection guide (the only references/ file allowed in the image;
# references/sources copyrighted material is excluded by .dockerignore).
COPY references/style-guide.md ./references/style-guide.md

# Frontend build output, served by the backend as static files.
COPY --from=frontend /build/dist ./frontend/dist

ENV TINDALOS_FRONTEND_DIST=frontend/dist

EXPOSE 8347

CMD ["tindalos", "web", "--host", "0.0.0.0", "--port", "8347"]
