FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY shared /opt/purchase-storage
COPY backend/requirements.txt .
RUN pip install --no-cache-dir /opt/purchase-storage -r requirements.txt
COPY backend/app ./app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
