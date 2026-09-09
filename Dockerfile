# Playwright's official image ships Chromium + all system deps pre-installed
# -- this avoids the single most common Playwright deployment failure
# (missing .so libraries for the headless browser).
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app
COPY requirements.txt requirements-prod.txt ./
# Both files: the runtime deps plus the PostgreSQL driver, which is
# production-only so that a local install never has to build it.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt

COPY . .

EXPOSE 8000
# Shell form (not exec form) on purpose: PaaS hosts such as Railway, Render and
# Fly inject the listen port as $PORT at runtime. A hardcoded --port 8000 makes
# the container start fine and then fail every healthcheck, because the platform
# routes traffic to a port nothing is listening on. ${PORT:-8000} keeps local
# `docker run` and docker-compose working unchanged.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
