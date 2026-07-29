FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY broker.py .
RUN useradd -m -u 1001 broker
USER 1001
EXPOSE 8000
ENTRYPOINT ["python", "broker.py"]
