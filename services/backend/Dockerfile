FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
COPY agentplatform ./agentplatform
RUN pip install --no-cache-dir .
USER nobody
CMD ["python", "-m", "agentplatform.api_main"]
