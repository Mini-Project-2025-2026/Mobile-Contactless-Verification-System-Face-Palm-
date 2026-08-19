# A reproducible image for the attendance backend.
#
# Heroku builds this from requirements.txt with its own buildpack; this exists
# for everywhere that is not Heroku — a campus that wants it on their own
# hardware, and anyone who needs to reproduce a deployment exactly.
FROM python:3.12-slim AS base

# Bytecode files and pip's cache are dead weight in a layer; unbuffered output
# is what makes the logs show up in `docker logs` while the process is running
# rather than in a burst when it exits.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first, and on their own layer: application code changes on every
# commit, this file does not, so the install is cached across almost all builds.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app

# Not root. A container that is compromised through the web process should not
# also be able to rewrite the code it is running.
RUN useradd --system --uid 10001 attendance && chown -R attendance /srv
USER attendance

EXPOSE 8000

# Liveness only — /health does no work on purpose, so a database blip cannot
# make the orchestrator kill a process that is otherwise fine. Readiness
# (/health/ready) is for a human or a dashboard to read, not for a restart loop.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
