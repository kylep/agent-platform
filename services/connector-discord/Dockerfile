FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY connector.py .
RUN useradd -m -u 1001 connector
USER 1001
ENTRYPOINT ["python", "/app/connector.py"]
