# Data Quality Accelerator -- API + engine
#
# WeasyPrint renders the PDF reports and needs Pango and Cairo as system
# libraries. They are LGPL, consumed as unmodified OS packages exactly as the
# distribution ships them, which carries no obligation for our source code.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DQA_DATA_ROOT=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dqa/ ./dqa/
COPY rules/ ./rules/
COPY tools/ ./tools/

# Non-root: this container handles a prospect's production data and will be
# looked at in a client security review.
RUN useradd --create-home --uid 10001 dqa \
    && mkdir -p /data \
    && chown -R dqa:dqa /data /app
USER dqa

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)"

CMD ["uvicorn", "dqa.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
