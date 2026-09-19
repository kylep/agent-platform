# Built from the REPO ROOT: docker buildx build -f apps/tcms/Dockerfile .
# The frontend is prebuilt on the host (npm -w tcms-frontend run build) like
# services/web — no node toolchain in the image.
FROM python:3.12-slim
WORKDIR /app
COPY apps/tcms/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/tcms/backend/tcmsapp ./tcmsapp
COPY apps/tcms/frontend/dist ./static
USER 10001
CMD ["uvicorn", "tcmsapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
