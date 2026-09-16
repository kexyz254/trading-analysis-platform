FROM python:3.12-slim
WORKDIR /app
COPY services/nexuss_bridge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/nexuss_bridge /app/services/nexuss_bridge
USER 65532:65532
CMD ["python", "-m", "services.nexuss_bridge.serve"]
