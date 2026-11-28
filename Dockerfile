# Playwright's official image ships Chromium + all system deps pre-installed
# -- this avoids the single most common Playwright deployment failure
# (missing .so libraries for the headless browser).
FROM mcr.microsoft.com/playwright/python:v1.65.0-jammy

WORKDIR /app
COPY requirements.txt requirements-prod.txt ./
# Both files: the runtime deps plus the PostgreSQL driver, which is
# production-only so that a local install never has to build it.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt

COPY . .

# Run as the image's unprivileged user, never root: this process drives a
# browser through untrusted websites, and a Chromium escape should land in an
# account that owns nothing but the app. /app (SQLite in development, default
# evidence folder) and /data (the evidence volume in docker-compose) are made
# writable for it.
RUN mkdir -p /data/evidence && chown -R pwuser:pwuser /app /data
USER pwuser

EXPOSE 8000
# Lets Docker / docker compose see when the app stops answering. (Railway
# uses its own healthcheck setting; point it at /healthz.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4)" || exit 1
# Shell form (not exec form) on purpose: PaaS hosts such as Railway, Render and
# Fly inject the listen port as $PORT at runtime. A hardcoded --port 8000 makes
# the container start fine and then fail every healthcheck, because the platform
# routes traffic to a port nothing is listening on. ${PORT:-8000} keeps local
# `docker run` and docker-compose working unchanged.
#
# --proxy-headers / --forwarded-allow-ips: on Railway every request arrives
# through Railway's HTTPS proxy, which says so in X-Forwarded-Proto. Without
# trusting it, the app believes it is being served over plain http and builds
# redirects that way -- /demo answered with "Location: http://.../demo/",
# downgrading a visitor to http on their way to the page. The container is
# only reachable through that proxy (there is no public TCP port), so the
# header cannot come from anyone else.
#
# ...but ONLY when TRUST_PROXY_HEADERS=true. With "--forwarded-allow-ips=*"
# uvicorn replaces the client address with the LEFTMOST X-Forwarded-For entry,
# which the client writes itself; with TRUST_PROXY_HEADERS=false the rate
# limiter then keyed on a spoofable value. Off, the real peer address is kept.
# (FORWARDED_ALLOW_IPS is uvicorn's own setting, read from the environment.)
CMD if [ "$TRUST_PROXY_HEADERS" = "true" ]; then export FORWARDED_ALLOW_IPS='*'; PROXY=--proxy-headers; \
    else PROXY=--no-proxy-headers; fi; \
    exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} $PROXY
