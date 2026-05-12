from __future__ import annotations

from pathlib import Path

from bankai.backend import BatchMovie, build_movie_args, parse_movie_batch


def test_parse_movie_batch_supports_titles_german_titles_and_urls(tmp_path: Path) -> None:
    batch = tmp_path / "movies.txt"
    batch.write_text(
        """
        # comments and blank lines are ignored
        Inception 2010 | Inception | https://filmpalast.to/stream/inception-2010
        Finding Nemo 2003 | Findet Nemo
        Zootopia 2016
        """,
        encoding="utf-8",
    )

    movies = parse_movie_batch(batch)

    assert movies == [
        BatchMovie(
            title="Inception 2010",
            german_title="Inception",
            url="https://filmpalast.to/stream/inception-2010",
        ),
        BatchMovie(title="Finding Nemo 2003", german_title="Findet Nemo"),
        BatchMovie(title="Zootopia 2016"),
    ]


def test_build_movie_args_force_background_jobs_to_auto_pick() -> None:
    args = build_movie_args(BatchMovie(title="Zootopia 2016", german_title="Zoomania"))

    assert args == [
        "run",
        "Zootopia 2016",
        "--de",
        "Zoomania",
        "--site",
        "filmpalast",
        "--auto",
    ]
