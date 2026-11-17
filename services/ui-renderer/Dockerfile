# Build from the repo root:
#   docker build -f services/ui-renderer/Dockerfile -t agent-platform-ui-renderer:dev .
# Only the ui_designer modules that need no database, plus the bundled fonts.
FROM python:3.12-slim
WORKDIR /app
COPY services/ui-renderer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/backend/agentplatform/__init__.py agentplatform/__init__.py
COPY services/backend/agentplatform/appdata/__init__.py services/backend/agentplatform/appdata/errors.py agentplatform/appdata/
COPY services/backend/agentplatform/ui_designer/__init__.py \
     services/backend/agentplatform/ui_designer/errors.py \
     services/backend/agentplatform/ui_designer/schema.py \
     services/backend/agentplatform/ui_designer/components.py \
     services/backend/agentplatform/ui_designer/export_formats.py \
     services/backend/agentplatform/ui_designer/visual_diff.py \
     services/backend/agentplatform/ui_designer/lint.py \
     services/backend/agentplatform/ui_designer/layout.py \
     services/backend/agentplatform/ui_designer/text.py \
     services/backend/agentplatform/ui_designer/fonts.py \
     services/backend/agentplatform/ui_designer/svg.py \
     agentplatform/ui_designer/
COPY services/backend/agentplatform/ui_designer/fonts agentplatform/ui_designer/fonts
COPY services/ui-renderer/app.py .
# resvg reads only the asset cache; the cache dir is a pod emptyDir.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UI_RENDERER_ASSET_DIR=/var/cache/ui-renderer/assets
USER 1001:1001
CMD ["python", "app.py"]
