-- dubbed state schema. Migrations are concatenated SQL files in order.
-- Version 1: initial schema.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);

-- Logical media item: a movie or a single episode.
CREATE TABLE IF NOT EXISTS media (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL CHECK (kind IN ('movie', 'episode')),
    title         TEXT    NOT NULL,
    year          INTEGER,
    season        INTEGER,
    episode       INTEGER,
    episode_title TEXT,
    imdb_id       TEXT,
    tmdb_id       INTEGER,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_media_lookup ON media (kind, title, year, season, episode);

-- A specific source URL/site for a media item (e.g. filmpalast page).
CREATE TABLE IF NOT EXISTS sources (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id  INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    site      TEXT    NOT NULL,
    url       TEXT    NOT NULL,
    language  TEXT,
    quality   TEXT,
    UNIQUE (site, url)
);
CREATE INDEX IF NOT EXISTS idx_sources_media ON sources (media_id);

-- Generic job row. payload is JSON; specific kinds define their own schema.
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id    INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    media_id     INTEGER REFERENCES media(id) ON DELETE SET NULL,
    kind         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued','running','done','failed','cancelled')),
    payload      TEXT    NOT NULL DEFAULT '{}',  -- JSON
    result       TEXT,                            -- JSON, set on success
    error        TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    priority     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_kind ON jobs (status, kind, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_parent ON jobs (parent_id);

-- Output artifacts produced by a job (audio, video, final mux, etc).
CREATE TABLE IF NOT EXISTS artifacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL CHECK (kind IN ('audio','video','final','intermediate')),
    path        TEXT    NOT NULL,
    codec       TEXT,
    duration_ms INTEGER,
    size_bytes  INTEGER,
    metadata    TEXT,  -- JSON
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts (job_id);

-- Auto-update updated_at on jobs.
CREATE TRIGGER IF NOT EXISTS trg_jobs_updated_at
AFTER UPDATE ON jobs FOR EACH ROW
BEGIN
    UPDATE jobs SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = OLD.id;
END;
