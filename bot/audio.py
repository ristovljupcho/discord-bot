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
        query = parse_qs(parsed.query, keep_blank_values=True)
        playlist_id = None
        if "list" in query:
            if len(query["list"]) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", query["list"][0]):
                raise ValueError
            playlist_id = query["list"][0]
            if playlist_id.startswith("RD"):
                raise PlaybackError("YouTube Mixes are not supported. Send a regular playlist link.")
        if parsed.hostname == "youtu.be":
            video_id = parsed.path.removeprefix("/")
        elif parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if parsed.path == "/playlist" and playlist_id:
                return f"https://www.youtube.com/playlist?list={playlist_id}"
            elif parsed.path == "/watch" and len(query.get("v", [])) == 1:
                video_id = query["v"][0]
            elif parsed.path.startswith(("/shorts/", "/embed/")):
                video_id = parsed.path.split("/")[-1]
            else:
                raise ValueError
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
        if playlist_id:
            return f"https://www.youtube.com/playlist?list={playlist_id}"
        return f"https://www.youtube.com/watch?v={video_id}"
    except ValueError:
        raise PlaybackError("Send an HTTPS YouTube video or playlist link.") from None


@dataclass(frozen=True)
class Track:
    title: str
    stream_url: str


async def extract_json(url: str, *options: str) -> dict:
    # A subprocess lets timeout, skip, and shutdown actually stop extraction.
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-cache-dir",
        "--skip-download", "--dump-single-json", "--no-warnings",
        "--socket-timeout", "15", "--retries", "1", "--extractor-retries", "1",
        "--js-runtimes", "deno", *options, "--", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            raise PlaybackError("YouTube took too long to respond. Try again later.") from None
        if process.returncode:
            raise PlaybackError(
                "YouTube could not provide this video or playlist. It may be unavailable, restricted, "
                "or blocked on this network. Try another link."
            )
        try:
            data = json.loads(output)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, TypeError):
            raise PlaybackError("YouTube returned an unsupported response.") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()


async def resolve_track(url: str) -> Track:
    canonical = youtube_url(url)
    if urlsplit(canonical).path == "/playlist":
        raise PlaybackError("A playlist must be expanded into videos before playback.")
    data = await extract_json(canonical, "--no-playlist", "--format", "bestaudio/best")
    if data.get("is_live") or data.get("live_status") in {"is_live", "is_upcoming"}:
        raise PlaybackError("Live streams and upcoming videos are not supported yet.")
    try:
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


@dataclass(frozen=True)
class Playlist:
    title: str
    urls: tuple[str, ...]
    skipped: int


async def resolve_playlist(url: str, *, limit: int) -> Playlist:
    canonical = youtube_url(url)
    if urlsplit(canonical).path != "/playlist":
        raise PlaybackError("Send a YouTube playlist link.")
    data = await extract_json(
        canonical, "--flat-playlist", "--yes-playlist", "--ignore-errors",
        "--playlist-items", f"1:{limit + 1}",
    )
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise PlaybackError("YouTube returned an unsupported playlist response.")
    if len(entries) > limit:
        raise PlaybackError(f"Playlists may contain at most {limit:,} entries. Nothing was added.")
    urls = []
    skipped = 0
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or entry.get("availability") not in (None, "public", "unlisted")
            or entry.get("title") in ("[Private video]", "[Deleted video]")
            or entry.get("is_live")
            or entry.get("live_status") in ("is_live", "is_upcoming")
        ):
            skipped += 1
            continue
        video_id = entry.get("id")
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            skipped += 1
            continue
        urls.append(f"https://www.youtube.com/watch?v={video_id}")
    if not urls:
        raise PlaybackError("This playlist is empty or has no accessible videos.")
    return Playlist(str(data.get("title") or "YouTube playlist")[:200], tuple(urls), skipped)
