# Built from the REPO ROOT: docker buildx build -f apps/running/Dockerfile .
# The frontend is prebuilt on the host (npm -w running-frontend run build)
# like services/web — no node toolchain in the image.
FROM python:3.12-slim
WORKDIR /app
COPY apps/running/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/running/backend/runningapp ./runningapp
COPY apps/running/frontend/dist ./static
USER 10001
CMD ["uvicorn", "runningapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
