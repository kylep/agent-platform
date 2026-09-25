FROM python:3.12-slim
# git for the self-hosting workspace clones; openssh-client for deploy-key
# (ssh) push auth — git pulls it only as a recommend, so name it explicitly.
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
RUN python -c 'import tomllib; from pathlib import Path; print("\n".join(tomllib.loads(Path("pyproject.toml").read_text())["project"]["dependencies"]))' \
      > /tmp/runtime-requirements.txt \
    && pip install --no-cache-dir -r /tmp/runtime-requirements.txt
COPY agentplatform ./agentplatform
RUN pip install --no-cache-dir --no-deps .
USER nobody
CMD ["python", "-m", "agentplatform.api_main"]
