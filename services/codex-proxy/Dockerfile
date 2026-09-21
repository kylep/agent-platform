FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY codex_proxy.py .
RUN pip install --no-cache-dir .
USER 1001:1001
CMD ["python", "codex_proxy.py"]
