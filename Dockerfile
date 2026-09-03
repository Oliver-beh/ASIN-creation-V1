# BDF VC Upload — Streamlit front end for the Vendor Central template filler.
#
# Pure-Python image: openpyxl + PyYAML + streamlit. No Excel, no system libs,
# no state. The engine (bdfvc/) and the rules (config/) are baked in, which is
# the whole point — one deployed image is the single source of truth.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Deps first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The engine, the rules, the entry point, the UI.
# bdfvc/ MUST be lowercase here — the code imports `bdfvc` and Linux is
# case-sensitive even though macOS is not.
COPY bdfvc/ ./bdfvc/
COPY config/ ./config/
COPY fill_bdf.py app.py ./
COPY .streamlit/ ./.streamlit/

# Version stamp shown in the app footer, so anyone can confirm what is live.
ARG BUILD_ID=dev
ARG BUILD_DATE=unbuilt
ENV BUILD_ID=$BUILD_ID \
    BUILD_DATE=$BUILD_DATE

# Cloud Run injects $PORT (8080 by default); bind to it, not a hardcoded port.
ENV PORT=8080
EXPOSE 8080

# Non-root. Nothing is written to disk except tempfiles under /tmp.
RUN useradd --create-home --uid 1001 appuser
USER appuser

# Shell form so $PORT expands at container start.
CMD streamlit run app.py \
      --server.port=$PORT \
      --server.address=0.0.0.0 \
      --server.headless=true \
      --browser.gatherUsageStats=false
