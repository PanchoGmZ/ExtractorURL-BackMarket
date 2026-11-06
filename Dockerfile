FROM apify/actor-python:3.13
USER root
WORKDIR /usr/src/app
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    PYTHON_DOTENV_DISABLED=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
    LITELLM_TELEMETRY=False CRAWL4AI_NO_CLOUD_NOTICE=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium
COPY . ./
RUN useradd --create-home labactor \
    && mkdir -p .cache .tmp storage output reports captures \
    && chown -R labactor:labactor . /opt/ms-playwright
USER labactor
RUN python -m src.main --help
CMD ["python", "-m", "src.main"]
