# Untitled Project client image: the engine, the two LLM roles and the web player.
#   docker compose up --build        (see compose.yaml)
#   docker run --rm --env-file .env -p 127.0.0.1:8765:8765 untitled-project
# Keys are passed at run time and never baked into the image. The LTX-2.5
# server is a separate image for a GPU machine (server/Dockerfile).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg and ffprobe: clip lengths, the window sent after a server restart,
# and the film concatenated at the end of a video run.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Unprivileged user; logs/ is the only directory the app writes to
# (transcripts, world models, timing logs and clips).
RUN useradd --create-home --uid 10001 film \
    && mkdir -p logs \
    && chown film:film logs
USER film

# Listen on all interfaces inside the container so the published port works.
# Publish it on 127.0.0.1 only: the player has no login.
EXPOSE 8765
CMD ["python", "main.py", "play", "--config", "configs/director.yaml", "--player", "web", "--host", "0.0.0.0"]
