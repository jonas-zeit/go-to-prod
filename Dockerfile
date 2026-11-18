FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY refresh_service refresh_service
ENV PYTHONUNBUFFERED=1
USER nobody
