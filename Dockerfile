FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 bot

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -c "import discord, nacl, davey, yt_dlp; assert discord.opus.load_opus('libopus.so.0') is None" \
    && ffmpeg -version \
    && deno --version

COPY --chown=bot:bot bot ./bot
USER bot
CMD ["python", "-m", "bot"]
