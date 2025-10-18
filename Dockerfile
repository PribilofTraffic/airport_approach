FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Add-on: trend_status_service.py is included for trend/duration analysis (auto-run via run_services.py)

RUN mkdir -p /app/data/PASN /app/data/PAPB && \
    mkdir -p /app/Approaches/PASN_APPROACHES /app/Approaches/PAPB_APPROACHES && \
    chmod +x approach_service.py taf_decoder.py run_services.py approach_status.py

ENV PYTHONUNBUFFERED=1
ENV TZ=America/Anchorage

CMD ["python", "run_services.py"]
