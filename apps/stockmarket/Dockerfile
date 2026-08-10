# Built from the REPO ROOT: docker buildx build -f apps/stockmarket/Dockerfile .
# The frontend is prebuilt on the host (npm -w stockmarket-frontend run build)
# like services/web — no node toolchain in the image.
FROM python:3.12-slim
WORKDIR /app
COPY apps/stockmarket/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/stockmarket/backend/stockmarketapp ./stockmarketapp
COPY apps/stockmarket/frontend/dist ./static
USER 10001
CMD ["uvicorn", "stockmarketapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
