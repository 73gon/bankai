"""Configuration loader.

Sources, in order of precedence (highest wins):
1. CLI flags (handled by Typer; passed in as overrides)
2. Environment variables prefixed ``BANKAI_`` with ``__`` as nesting separator
3. ``config.toml`` in the working directory (or path given via ``BANKAI_CONFIG``)
4. Built-in defaults

Use :func:`get_settings` for a memoized singleton, or :func:`load_settings` to
read fresh from disk (e.g. in tests).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class OutputSettings(BaseModel):
    directory: Path = Path("/library")
    # Default layout (Plex/Jellyfin compatible):
    #   <directory>/Movies/<movie_folder_template>/<filename_template>
    #   <directory>/Shows/<show name>/<season_folder_template>/<series_filename_template>
    filename_template: str = "{title} ({year}).mkv"
    series_filename_template: str = "{title} - S{season:02d}E{episode:02d}.mkv"
    movie_folder_template: str = "{title} ({year})"
    season_folder_template: str = "Season {season:02d}"
    # When true, the pipeline skips work that would overwrite an
    # existing target file and posts a Discord notice (if configured).
    skip_existing: bool = True


class AudioSettings(BaseModel):
    codec: Literal["copy", "eac3", "ac3"] = "copy"
    language_tag: str = "ger"
    track_name: str = "German (Web-DL)"
    default_track: bool = True


class SyncSettings(BaseModel):
    mode: Literal["auto", "manual", "skip"] = "auto"
    threshold_seconds: float = 0.5
    alass_binary: str = "alass"


class ScraperSettings(BaseModel):
    backend: Literal["ytdlp", "playwright", "auto"] = "auto"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
    request_timeout_seconds: float = 30.0
    # When true, the CLI prompts you to pick from search results before
    # running the pipeline. When false, it auto-picks the top match.
    interactive_pick: bool = False


class MetadataSettings(BaseModel):
    tvdb_enabled: bool = True
    tvdb_api_key: str = ""
    tvdb_pin: str = ""
    tvdb_languages: list[str] = Field(default_factory=lambda: ["deu", "eng"])


class TransferSettings(BaseModel):
    root: Path = Path("/mnt/media12")
    movies_dir: Path = Path("/mnt/media12/movies")
    shows_dir: Path = Path("/mnt/media12/shows")
    rsync_binary: str = "rsync"


class QueueSettings(BaseModel):
    search_workers: int = 2
    extract_workers: int = 3
    torrent_workers: int = 5
    sync_workers: int = 2
    remux_workers: int = 1


class PathsSettings(BaseModel):
    state_db: Path = Path("/config/bankai.sqlite3")
    work_dir: Path = Path("/work")
    downloads_dir: Path = Path("/downloads")
    # When true, the pipeline deletes intermediate artifacts (extracted
    # dub audio, synced audio, work-dir job folder) AND removes the
    # source torrent + downloaded video from qBittorrent once the final
    # MKV has been written. Set to false to keep everything for debug.
    cleanup_after_success: bool = True


class ProwlarrSettings(BaseModel):
    url: str = "http://prowlarr:9696"
    api_key: str = ""
    indexer_ids: list[int] = Field(default_factory=list)


class QBittorrentSettings(BaseModel):
    url: str = "http://qbittorrent:8080"
    username: str = "admin"
    password: str = "adminadmin"
    category: str = "bankai"
    poll_interval_seconds: float = 3.0
    # Map qBittorrent-reported (container) paths to local host paths.
    # Example: {"/downloads": "/mnt/media/downloads"}
    path_map: dict[str, str] = Field(default_factory=dict)
    # Optional explicit save_path passed to qBittorrent on add (container path).
    save_path: str | None = None


class SelectorSettings(BaseModel):
    preferred_resolutions: list[str] = Field(default_factory=lambda: ["2160p", "1080p"])
    preferred_codecs: list[str] = Field(default_factory=lambda: ["x265", "h265", "x264", "h264"])
    preferred_sources: list[str] = Field(default_factory=lambda: ["BluRay", "WEB-DL", "WEBRip"])
    preferred_groups: list[str] = Field(default_factory=list)
    # Audio-language tokens to prefer in release titles (case-insensitive
    # substring match against the title). Releases lacking any of these
    # tokens may still be picked, but matching ones get a bonus. Use an
    # empty list to disable the bonus entirely.
    preferred_audio_languages: list[str] = Field(default_factory=lambda: ["english"])
    min_seeders: int = 1
    max_size_gib: float = 80.0
    min_size_gib: float = 0.5


class NotificationsSettings(BaseModel):
    webhook_url: HttpUrl | None = None
    on_success: bool = True
    on_failure: bool = True


class WebSettings(BaseModel):
    """Settings for the bankai web UI / HTTP API server."""

    host: str = "0.0.0.0"
    port: int = 9988
    # Maximum number of pipeline jobs allowed to run concurrently when
    # launched from the web UI. Extra requests queue and start as slots
    # free up.
    max_concurrent_jobs: int = 2
    # When true, files the browser cannot play natively (e.g. 4K HEVC)
    # are transcoded on the fly with ffmpeg for the in-browser preview.
    transcode_fallback: bool = True
    # Directories on the media server scanned by the "Server" page to
    # show which titles already exist there.
    server_movie_dirs: list[Path] = Field(
        default_factory=lambda: [Path("/mnt/media12/movies"), Path("/mnt/remote_media/movies")]
    )
    server_show_dirs: list[Path] = Field(
        default_factory=lambda: [Path("/mnt/media12/shows"), Path("/mnt/remote_media/shows")]
    )
    # Seconds to cache TVDB discover/trending responses and ffprobe data.
    cache_ttl_seconds: int = 900


class Settings(BaseSettings):
    """Top-level settings, populated from TOML + env vars."""

    model_config = SettingsConfigDict(
        env_prefix="BANKAI_",
        env_nested_delimiter="__",
        extra="ignore",
        toml_file=None,  # set per-instance via classmethod below
    )

    output: OutputSettings = Field(default_factory=OutputSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)
    scraper: ScraperSettings = Field(default_factory=ScraperSettings)
    queue: QueueSettings = Field(default_factory=QueueSettings)
    metadata: MetadataSettings = Field(default_factory=MetadataSettings)
    transfer: TransferSettings = Field(default_factory=TransferSettings)
    paths: PathsSettings = Field(default_factory=PathsSettings)
    prowlarr: ProwlarrSettings = Field(default_factory=ProwlarrSettings)
    qbittorrent: QBittorrentSettings = Field(default_factory=QBittorrentSettings)
    selector: SelectorSettings = Field(default_factory=SelectorSettings)
    notifications: NotificationsSettings = Field(default_factory=NotificationsSettings)
    web: WebSettings = Field(default_factory=WebSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence (highest first): init kwargs > env > TOML file > defaults.
        toml_path = cls.model_config.get("toml_file")
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        if toml_path:
            sources.append(TomlConfigSettingsSource(settings_cls))
        sources.append(file_secret_settings)
        return tuple(sources)


def _config_path() -> Path | None:
    explicit = os.environ.get("BANKAI_CONFIG")
    if explicit:
        return Path(explicit)
    user = user_config_path()
    if user.exists():
        return user
    candidate = Path.cwd() / "config.toml"
    return candidate if candidate.exists() else None


def user_config_path() -> Path:
    """Default user config location: ``$XDG_CONFIG_HOME/bankai/config.toml``."""
    explicit = os.environ.get("BANKAI_CONFIG")
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "bankai" / "config.toml"
    return Path.home() / ".config" / "bankai" / "config.toml"


def load_settings(config_path: Path | None = None) -> Settings:
    """Read settings fresh from disk + environment. Not memoized."""
    path = config_path or _config_path()
    # Mutate class-level config so the customised source picks up the path.
    Settings.model_config["toml_file"] = str(path) if path else None
    return Settings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Memoized settings singleton for normal application use."""
    return load_settings()


def reset_settings_cache() -> None:
    """Clear the memoized singleton (useful in tests)."""
    get_settings.cache_clear()
