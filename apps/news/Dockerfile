# Built from the REPO ROOT: docker buildx build -f apps/news/Dockerfile .
# The frontend is prebuilt on the host (npm -w news-frontend run build) like
# services/web — no node toolchain in the image.
FROM python:3.12-slim
WORKDIR /app
COPY apps/news/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/news/backend/newsapp ./newsapp
COPY apps/news/frontend/dist ./static
USER 10001
CMD ["uvicorn", "newsapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
