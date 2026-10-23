import asyncio
from dataclasses import dataclass
import json
import re
import sys
from urllib.parse import parse_qs, urlsplit


class PlaybackError(Exception):
    """A playback problem whose message is safe to show to a member."""


def youtube_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError
        query = parse_qs(parsed.query)
        if "list" in query:
            raise PlaybackError("Playlists are not supported yet. Send a single video link.")
        if parsed.hostname == "youtu.be":
            video_id = parsed.path.removeprefix("/")
        elif parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/watch" and len(query.get("v", [])) == 1:
                video_id = query["v"][0]
            elif parsed.path.startswith(("/shorts/", "/embed/")):
                video_id = parsed.path.split("/")[-1]
            else:
                raise ValueError
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
        return f"https://www.youtube.com/watch?v={video_id}"
    except ValueError:
        raise PlaybackError("Send an HTTPS YouTube video link, such as https://youtu.be/VIDEO_ID.") from None


@dataclass(frozen=True)
class Track:
    title: str
    stream_url: str


async def resolve_track(url: str) -> Track:
    # A subprocess lets timeout, skip, and shutdown actually stop extraction.
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-cache-dir",
        "--no-playlist", "--skip-download", "--dump-single-json", "--no-warnings",
        "--socket-timeout", "15", "--retries", "1", "--extractor-retries", "1",
        "--js-runtimes", "deno", "--format", "bestaudio/best", "--", youtube_url(url),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            raise PlaybackError("YouTube took too long to respond. Try again later.") from None
        if process.returncode:
            raise PlaybackError(
                "YouTube could not provide this video. It may be unavailable, restricted, "
                "or blocked on this network. Try another video."
            )
        try:
            data = json.loads(output)
            if data.get("is_live") or data.get("live_status") in {"is_live", "is_upcoming"}:
                raise PlaybackError("Live streams and upcoming videos are not supported yet.")
            stream = data["url"]
            parsed = urlsplit(stream)
            host = parsed.hostname or ""
            if (
                parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or not (host == "googlevideo.com" or host.endswith(".googlevideo.com"))
            ):
                raise ValueError
            return Track(title=str(data.get("title") or "YouTube video")[:200], stream_url=stream)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise PlaybackError("YouTube returned an unsupported audio response.") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
