FROM python:3.12-slim
WORKDIR /workspace
COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY tests/requirements.txt /tmp/test-requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend-requirements.txt -r /tmp/test-requirements.txt
COPY backend ./backend
COPY monitor ./monitor
COPY tests ./tests
CMD ["pytest", "-q", "tests"]
