FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# gunicorn is installed here rather than in requirements.txt: it cannot run on
# Windows, and the team develops there with `python run.py`.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn==26.0.0

COPY . ./

# SQLite file (Maps jobs + users) lives here; mount a volume to keep it.
RUN mkdir -p /app/data

EXPOSE 8080

# One worker on purpose — scan state and Maps pollers are per-process,
# see the note at the top of wsgi.py. Scale with --threads instead.
# The Wayback endpoint legitimately runs for minutes, hence the long timeout.
CMD ["gunicorn", "-w", "1", "--threads", "8", "--timeout", "600", "-b", "0.0.0.0:8080", "wsgi:app"]
