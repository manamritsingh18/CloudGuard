FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 cloudguard && mkdir /data && chown cloudguard:cloudguard /data
COPY main.py storage.py audit.py config.py slo.py targets.yaml ./
USER cloudguard
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
