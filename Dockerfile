FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only the code: data is mounted at run time
COPY *.py ./

# The default airport list. Mount your own file over it to follow other airports.
COPY airports.json /app/config/airports.json

RUN mkdir -p /app/data && \
    chmod +x approach_service.py taf_decoder.py run_services.py approach_status.py

ENV PYTHONUNBUFFERED=1
ENV TZ=America/Anchorage

# Healthy = both loops are still rewriting their output files
HEALTHCHECK --interval=2m --timeout=10s --start-period=1m --retries=2 \
  CMD ["python", "healthcheck.py"]

CMD ["python", "run_services.py"]
