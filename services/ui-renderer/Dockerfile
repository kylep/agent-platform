# Build from the repo root:
#   docker build -f services/ui-renderer/Dockerfile -t agent-platform-ui-renderer:dev .
# Only the ui_designer modules that need no database, plus the bundled fonts.
FROM node:22-bookworm-slim AS motion-node
FROM python:3.12-slim
COPY --from=motion-node /usr/local/bin/node /usr/local/bin/node
COPY --from=motion-node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
WORKDIR /app/motion_encoder
COPY services/ui-renderer/motion_encoder/package.json services/ui-renderer/motion_encoder/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY services/ui-renderer/motion_encoder/encode.mjs services/ui-renderer/motion_encoder/LICENSE-Remotion.md services/ui-renderer/motion_encoder/LICENSE-Node.txt services/ui-renderer/motion_encoder/PROVENANCE.md ./
RUN apt-get update && apt-get install -y --no-install-recommends libegl1 libgl1 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/ui-renderer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/backend/agentplatform/__init__.py agentplatform/__init__.py
COPY services/backend/agentplatform/appdata/__init__.py services/backend/agentplatform/appdata/errors.py agentplatform/appdata/
COPY services/backend/agentplatform/ui_designer/__init__.py \
     services/backend/agentplatform/ui_designer/errors.py \
     services/backend/agentplatform/ui_designer/schema.py \
     services/backend/agentplatform/ui_designer/components.py \
     services/backend/agentplatform/ui_designer/constraints.py \
     services/backend/agentplatform/ui_designer/export_formats.py \
     services/backend/agentplatform/ui_designer/visual_diff.py \
     services/backend/agentplatform/ui_designer/lint.py \
     services/backend/agentplatform/ui_designer/libraries.py \
     services/backend/agentplatform/ui_designer/vector_geometry.py \
     services/backend/agentplatform/ui_designer/layout.py \
     services/backend/agentplatform/ui_designer/text.py \
     services/backend/agentplatform/ui_designer/fonts.py \
     services/backend/agentplatform/ui_designer/font_validation.py \
     services/backend/agentplatform/ui_designer/svg.py \
     services/backend/agentplatform/ui_designer/raster_effects.py \
     services/backend/agentplatform/ui_designer/motion.py \
     agentplatform/ui_designer/
COPY services/backend/agentplatform/ui_designer/fonts agentplatform/ui_designer/fonts
COPY services/backend/agentplatform/ui_designer/licenses agentplatform/ui_designer/licenses
COPY services/ui-renderer/app.py .
# resvg reads only the asset cache; the cache dir is a pod emptyDir.
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UI_RENDERER_ASSET_DIR=/var/cache/ui-renderer/assets
USER 1001:1001
CMD ["python", "app.py"]
