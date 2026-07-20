FROM python:3.12-slim
# git for the self-hosting workspace clones; openssh-client for deploy-key
# (ssh) push auth — git pulls it only as a recommend, so name it explicitly.
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
COPY agentplatform ./agentplatform
RUN pip install --no-cache-dir .
USER nobody
CMD ["python", "-m", "agentplatform.api_main"]
