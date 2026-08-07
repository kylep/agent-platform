# Build FROM REPO ROOT: the image bakes the union of tools/*/requirements.txt
# so a tool's deps exist without runtime pip (docs/design/12). Adding a new
# dependency = rebuild this image; adding/editing a tool with existing deps is
# live on the next checkout sync (tools/ is read from the mounted volume, not
# from this image).
FROM python:3.12-slim
WORKDIR /app
COPY services/tool-executor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Union of tool dependencies (globs to nothing gracefully via the loop).
COPY tools/ /build-tools/
RUN find /build-tools -name requirements.txt -exec pip install --no-cache-dir -r {} \; \
    && rm -rf /build-tools
COPY services/tool-executor/executor.py .
RUN useradd -m -u 1001 executor
USER 1001
EXPOSE 8000
ENTRYPOINT ["python", "executor.py"]
