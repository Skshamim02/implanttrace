# --- build: install dependencies into a virtualenv ------------------------------
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install -r requirements.txt

# --- runtime: small image, non-root user ----------------------------------------
FROM python:3.12-slim
ENV PATH="/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production LOG_FORMAT=json
RUN useradd --create-home --uid 10001 app
WORKDIR /srv
COPY --from=build /venv /venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && mkdir -p /srv/inbox && chown app:app /srv/inbox
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status == 200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["web"]
