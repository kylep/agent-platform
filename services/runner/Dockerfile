FROM node:22-slim
RUN npm install -g @anthropic-ai/claude-code@2.1.214 \
 && apt-get update && apt-get install -y --no-install-recommends python3 python3-pip git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt
COPY runner.py .
RUN useradd -m runner && mkdir /workspace && chown runner /workspace
USER runner
WORKDIR /workspace
ENTRYPOINT ["python3", "/app/runner.py"]
