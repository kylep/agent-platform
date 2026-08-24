FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY facade.py ./
RUN useradd -m -u 1001 facade
USER 1001
EXPOSE 8000
ENTRYPOINT ["python", "facade.py"]
