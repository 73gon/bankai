"""Manga and light novel release names as nyaa has them, read into series, volumes and chapters."""

from __future__ import annotations

import pytest

from bankai.web import manga_names as names


@pytest.mark.parametrize(
    ("title", "series", "volumes", "chapters", "group"),
    [
        (
            "Sousou no Frieren | Frieren - Beyond Journey's End v01-14 + 138-147 (2021-2026) (Digital) (1r0n)",
            ("Sousou no Frieren", "Frieren - Beyond Journey's End"), (1, 14), (138, 147), "1r0n",
        ),
        (
            "Sousou no Frieren / Frieren - Beyond Journey's End v01-10,098-127 (2021-2024) (Digital) (LuCaZ)",
            ("Sousou no Frieren", "Frieren - Beyond Journey's End"), (1, 10), (98, 127), "LuCaZ",
        ),
        ("Sousou no Frieren CH001-140 Mangadex-RIP", ("Sousou no Frieren",), None, (1, 140), None),
        ("Frieren - (Chapters 141-147)", ("Frieren",), None, (141, 147), None),
        ("Sousou no Frieren | Frieren - Beyond Journey's End Vol.10 - Vol.11", ("Sousou no Frieren", "Frieren - Beyond Journey's End"), (10, 11), None, None),
        ("Sousou No Frieren Chapter 84-104", ("Sousou No Frieren",), None, (84, 104), None),
        ("Frieren Beyond Journey's End v01-09 and Chapters 88-99 (Ongoing)", ("Frieren Beyond Journey's End",), (1, 9), (88, 99), None),
        ("[Oshi No Ko] v13 (2026) (Digital) (ShelfLife)", ("Oshi No Ko",), (13, 13), None, "ShelfLife"),
        ("【OSHI NO KO】 001-166 (2022-2024) (Digital) (Antrill) [Completed]", ("OSHI NO KO",), None, (1, 166), "Antrill"),
        ("【OSHI NO KO】 001-130 as v01-13 (Digital-Compilation) (Oak)", ("OSHI NO KO",), (1, 13), (1, 130), "Oak"),
        ("[1r0n] Chainsaw Man v01-11 (Digital)", ("Chainsaw Man",), (1, 11), None, "1r0n"),
        ("Mushoku Tensei: Redundant Reincarnation - Volume 02 [Seven Seas]", ("Mushoku Tensei: Redundant Reincarnation",), (2, 2), None, "Seven Seas"),
        ("Mushoku Tensei - Redundant Reincarnation v01-03 [Seven Seas] [Stick]", ("Mushoku Tensei - Redundant Reincarnation",), (1, 3), None, "Stick"),
        ("Frieren - Beyond Journey's End -Prelude- [Yen Press] [Stick]", ("Frieren - Beyond Journey's End -Prelude",), None, None, "Stick"),
        ("Kaiju No. 8 v01-12 (2021-2024) (Digital) (Oak)", ("Kaiju No. 8",), (1, 12), None, "Oak"),
        # Years in braces are no chapters; nor are they in a name cut off inside its last tag.
        ("Solo Leveling {2021-2023} {Digital} {Tapas} {4str0}", ("Solo Leveling",), None, None, "4str0"),
        ("The Promised Neverland v09-12 (2018-2019) [Viz] [LuCaZ", ("The Promised Neverland",), (9, 12), None, "LuCaZ"),
    ],
)
def test_a_release_name_is_read_into_its_parts(title, series, volumes, chapters, group):
    release = names.parse(title)

    assert release.names == series
    assert release.volumes == volumes
    assert release.chapters == chapters
    assert release.group == group


def test_a_guidebook_in_the_series_brackets_is_not_the_series():
    release = names.parse("[Oshi No Ko] TV Anime 1st Season Official Guidebook - First Report (2026) (Digital) (1r0n)")

    assert release.names == ("TV Anime 1st Season Official Guidebook - First Report",)


def test_years_completeness_and_audiobooks_are_noticed():
    assert names.parse("Frieren v01-14 (2021-2026) (Digital) (1r0n)").years == (2021, 2026)
    assert names.parse("Solo Leveling {2021-2023} {Digital} {Tapas} {4str0}").years == (2021, 2023)


def test_a_tag_that_says_novel_is_noticed_but_not_a_title_with_the_word_in_it():
    assert names.parse("Solo Leveling [WN ∼ Complete ch.1-270]").tagged_novel
    assert names.parse("Solo Leveling | I Alone Level-Up (Novel) (Full) (Scanlated) [01-270]").tagged_novel
    assert not names.parse("The Novel's Extra v01-03 (Digital) (1r0n)").tagged_novel
    assert names.parse("【OSHI NO KO】 001-166 (Digital) (Oak) [Completed]").complete
    assert names.parse("Mushoku Tensei - Redundant Reincarnation v03 [Audiobook] [Troglodyte]").audiobook


@pytest.mark.parametrize(
    ("title", "size", "novel"),
    [
        ("[BennoSubs] Oshi no Ko - Futari no Etude (Novel)", 2 * 2**20, True),
        ("Overlord LN v01-16 (epub)", 200 * 2**20, True),
        ("Chainsaw Man v01-11 (Digital) (1r0n)", 2 * 2**30, False),
        ("Sousou no Frieren CH001-140 Mangadex-RIP", 4 * 2**30, False),
        # Says nothing: a few MB a volume is text, a few hundred pictures.
        ("Mushoku Tensei - Redundant Reincarnation v01-03 [Seven Seas] [Stick]", 34 * 2**20, True),
        ("Mushoku Tensei - Jobless Reincarnation v01-23 [Seven Seas]", 7 * 2**30, False),
        ("Some Series v01 [Yen Press]", 30 * 2**20, None),
    ],
)
def test_a_light_novel_is_told_from_manga(title, size, novel):
    assert names.novel(names.parse(title), size) is novel


@pytest.mark.parametrize(
    ("filename", "number"),
    [
        ("Frieren - Beyond Journey's End v01 (2021) (Digital) (1r0n).cbz", ("volume", 1.0)),
        ("Frieren - Beyond Journey's End - c138 (2026) (Digital) (1r0n).cbz", ("chapter", 138.0)),
        ("Chainsaw Man - c001 (v01) - p000 [Digital] [danke-Empire].cbz", ("chapter", 1.0)),
        ("Vol 01.cbz", ("volume", 1.0)),
        ("Chapter 0216.cbz", ("chapter", 216.0)),
        ("Overlord Vol. 1.epub", ("volume", 1.0)),
        ("One Piece v100 (2021) (Digital).cbz", ("volume", 100.0)),
        ("Frieren - c138.5 (2026).cbz", ("chapter", 138.5)),
        # A weekly chapter's bare number; a title's own short one is none.
        ("Jujutsu Kaisen 191 (2022) (Digital) (1r0n).cbz", ("chapter", 191.0)),
        ("Jujutsu Kaisen 168.1 (2021) (Digital) (1r0n).cbz", ("chapter", 168.1)),
        ("Chainsaw Man 097 (2022) (Digital) (1r0n).cbz", ("chapter", 97.0)),
        ("Kaiju No. 8 (2021) (Digital).cbz", None),
        ("Jujutsu Kaisen 191-242 (2022).cbz", None),
        # Two volumes in one file, or a part whose volumes start again: left as they are.
        ("Frieren v01-02.cbz", None),
        ("Ascendance of a Bookworm Part 2 Volume 1.epub", None),
        ("Omake.cbz", None),
    ],
)
def test_a_file_s_volume_or_chapter_is_read_from_its_name(filename, number):
    assert names.file_number(filename) == number


def test_files_are_named_the_way_the_library_names_them():
    assert names.target_name("volume", 1, ".cbz") == "Vol 01.cbz"
    assert names.target_name("volume", 100, ".CBZ") == "Vol 100.cbz"
    assert names.target_name("chapter", 138, ".zip") == "Chapter 0138.cbz"
    assert names.target_name("chapter", 138.5, ".cbz") == "Chapter 0138.5.cbz"
    assert names.target_name("volume", 3, ".epub") == "Vol 03.epub"


def test_what_a_series_holds_is_described_briefly():
    assert names.describe({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14}, {138, 139, 140}) == "Vol 1–12, 14 · Ch 138–140"
    assert names.describe(set(), {10.5}) == "Ch 10.5"


def test_a_comic_archive_is_named_by_what_it_holds_not_as_it_came(tmp_path):
    """Tanya the Evil's Vol 29-34 came as .cbz holding RAR and 7-Zip; OpenPanel opened none."""
    from bankai.web import manga_names

    rar, seven, zipped, epub = (tmp_path / name for name in ("a.cbz", "b.cbz", "c.cbr", "d.epub"))
    rar.write_bytes(b"Rar!\x1a\x07\x01\x00rest")
    seven.write_bytes(b"7z\xbc\xaf\x27\x1c\x00\x04rest")
    zipped.write_bytes(b"PK\x03\x04rest")
    epub.write_bytes(b"PK\x03\x04rest")
    assert manga_names.archive_extension(rar, ".cbz") == ".cbr"
    assert manga_names.archive_extension(seven, ".cbz") == ".cb7"
    assert manga_names.archive_extension(zipped, ".cbr") == ".cbz"
    # An EPUB is a ZIP too, and stays an EPUB.
    assert manga_names.archive_extension(epub, ".epub") == ".epub"
    assert ".cb7" in manga_names.BOOK_EXTENSIONS
