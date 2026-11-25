# ATTa API image (Stage 01). Hash-locked dependencies, non-root user, no build tools at runtime.
FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/atta
COPY configuration/locks/backend-core.lock.txt /tmp/lock.txt
RUN pip install --require-hashes --no-deps -r /tmp/lock.txt && rm /tmp/lock.txt \
 && useradd --system --uid 10001 --home /opt/atta atta
COPY backend ./backend
COPY configuration/base.yaml ./configuration/base.yaml
COPY configuration/environments ./configuration/environments
COPY capabilities ./capabilities
COPY database ./database
RUN mkdir -p runtime && chown -R atta /opt/atta/runtime
USER atta
EXPOSE 8800
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/v1/health/live',timeout=2).status==200 else 1)"
CMD ["sh", "-c", "python -m uvicorn backend.api.app:create_app --factory --host ${ATTA__API__HOST:-127.0.0.1} --port 8800"]
