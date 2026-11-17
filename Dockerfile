FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8

WORKDIR /app

COPY docker/requirements-docker.txt /app/requirements-docker.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements-docker.txt

COPY factors_v2.py /app/factors_v2.py
COPY factor_registry.py /app/factor_registry.py
COPY run_pipeline_v2.py /app/run_pipeline_v2.py
COPY quant_api.py /app/quant_api.py
COPY update_lake.sh /app/update_lake.sh
COPY docker/lake_trade_date.py /app/docker/lake_trade_date.py

COPY docker/ashare-lake.toml /app/config/ashare-lake.toml

RUN chmod +x /app/update_lake.sh \
    && mkdir -p /app/results

EXPOSE 8000

CMD ["uvicorn", "quant_api:app", "--host", "0.0.0.0", "--port", "8000"]