FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY shared /opt/purchase-storage
COPY mcp-server/requirements.txt .
RUN pip install --no-cache-dir /opt/purchase-storage -r requirements.txt
COPY mcp-server/purchase_mcp ./purchase_mcp
EXPOSE 8001
CMD ["python", "-m", "purchase_mcp.server"]
