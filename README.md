# Audio Metadata Tagger

A Python script that automatically tags audio files (MP3, FLAC, M4A) with metadata from [MusicBrainz](https://musicbrainz.org/) and album art from the [Cover Art Archive](https://coverartarchive.org/).

## Features

- Tags MP3 (ID3v2.4), FLAC (Vorbis comments), and M4A/MP4/AAC files
- Looks up verified track titles, disc numbers, genres, labels, release dates, and cover art from MusicBrainz
- Corrects misspelled artist names using MusicBrainz canonical spellings (e.g. `Rhianna` → `Rihanna`)
- Fuzzy album matching — strips common suffixes like "Deluxe Edition" or "Remastered" and retries if an exact search fails
- Multi-disc support — auto-detects `CD1/`, `CD2/`, `Disc 1/`, etc. subfolders and maps tracks to the correct disc
- Renames album folders to `[YEAR] Album Name` and track files to `NN - Title.ext` using MusicBrainz data
- Organizes loose files — fetches the full artist discography, lets you interactively pick the right album, then creates subfolders and moves files
- Dry-run mode — preview all changes before anything is modified
- Confirmation mode — full preview with a yes/no prompt before applying
- Resilient — locked or read-only files are skipped with a warning rather than stopping the run

## Requirements

- Python 3.10+
- `mutagen` and `musicbrainzngs`

```bash
pip3 install -r requirements.txt
```

Or directly:

```bash
pip3 install mutagen musicbrainzngs
```

## Expected Folder Structure

```
/music-root/
  Artist Name/
    [2024] Album Name/
      01 - Track One.mp3
      02 - Track Two.flac
    [2020] Another Album/
      01. First Song.m4a
  Another Artist/
    [1997] Their Album/
      CD1/
        01 Intro.mp3
      CD2/
        01 Bonus Track.mp3
```

**Artist folders** should be named as the artist appears on MusicBrainz. If the spelling is slightly off, the script detects the canonical name and uses it in the tags.

**Album folders** are parsed flexibly — all of the following formats are recognised and will be normalised to `[YEAR] Album Name` when using `--rename-tracks` or `--rename-folders`:

| Format | Example |
|--------|---------|
| `[YEAR] Album Name` *(preferred)* | `[1994] The Downward Spiral` |
| `(YEAR) Album Name` | `(1994) The Downward Spiral` |
| `YEAR - Album Name` | `1994 - The Downward Spiral` |
| `YEAR Album Name` | `1994 The Downward Spiral` |
| `Album Name (YEAR)` | `The Downward Spiral (1994)` |
| `Album Name [YEAR]` | `The Downward Spiral [1994]` |
| `Album Name` *(no year)* | `The Downward Spiral` |

Edition suffixes like `(Deluxe Edition)` or `(Remastered)` are preserved in folder and file names even when MusicBrainz doesn't include them.

**Track filenames** are parsed for track number using these formats:
- `01 - Track Name.mp3`
- `01. Track Name.flac`
- `01 Track Name.m4a`

**Supported audio formats:** MP3, FLAC, M4A/MP4/AAC

## Usage

### First run — preview changes

Always start with a dry run to verify the MusicBrainz matches look right before committing to any changes:

```bash
python3 tag_mp3s.py /path/to/music --tag --dry-run
python3 tag_mp3s.py /path/to/music --rename-tracks --dry-run
python3 tag_mp3s.py /path/to/music --tag --rename-tracks --dry-run
```

No files are modified. The output shows exactly what would be changed.

### Write metadata tags

```bash
python3 tag_mp3s.py /path/to/music --tag
python3 tag_mp3s.py /path/to/music --tag --dry-run
```

Looks up each album on MusicBrainz and writes the following metadata to every audio file:

- Title, artist, album, track number, disc number, year, genre, label
- Cover art downloaded from the Cover Art Archive (front cover)

Use `--tag` with any of the art/genre flags to control what gets written:

```bash
python3 tag_mp3s.py /path/to/music --tag --no-art          # skip cover art
python3 tag_mp3s.py /path/to/music --tag --keep-art        # only embed art if missing
python3 tag_mp3s.py /path/to/music --tag --genre "Rock"    # override genre
python3 tag_mp3s.py /path/to/music --tag --skip-tagged     # skip already-complete files
```

### Preview then confirm before applying

```bash
python3 tag_mp3s.py /path/to/music --tag --confirm
```

Runs a full dry-run preview, then asks `Apply these changes? [y/N]` before proceeding.

---

### Renaming

#### Rename track files (and folders)

```bash
python3 tag_mp3s.py /path/to/music --rename-tracks
python3 tag_mp3s.py /path/to/music --rename-tracks --dry-run
```

- Renames track files to `NN - Original Title.ext` — the track number comes from MusicBrainz, but the title is always kept from the original filename so it is never substituted with a different title
- Renames album folders to `[YEAR] Album Name` using the canonical title and year from MusicBrainz
- Normalises all dashes to standard hyphens — en-dashes, em-dashes, and other Unicode dash variants are replaced automatically

Combine with `--tag` to also write metadata at the same time:

```bash
python3 tag_mp3s.py /path/to/music --tag --rename-tracks
python3 tag_mp3s.py /path/to/music --tag --rename-tracks --dry-run
```

#### Rename folders only

```bash
python3 tag_mp3s.py /path/to/music --rename-folders
python3 tag_mp3s.py /path/to/music --rename-folders --dry-run
```

Renames album folders to `[YEAR] Album Name` without touching individual track filenames. Useful when you want standardised folder names but prefer to keep your original track filenames.

---

### Organizing Loose Files

If audio files are found directly in an artist folder (e.g. `Artist/Song.mp3` instead of `Artist/[YEAR] Album/Song.mp3`), use `--organize` to sort them into album subfolders.

#### Interactively organize loose files

```bash
python3 tag_mp3s.py /path/to/music --organize
python3 tag_mp3s.py /path/to/music --organize --dry-run
```

For each loose file, `--organize` will:

1. Fetch the artist's discography from MusicBrainz (Albums, Singles, and EPs by default)
2. Look up the recording to find which albums it appears on — those matches are marked with `*`
3. Present a numbered list of albums grouped by type (Albums → EPs → Singles), sorted oldest first within each group
4. Ask you to pick the correct album (press Enter for option 1, or enter `0` to skip the file)
5. Create `[YEAR] Album Name` subfolders and move the files into them
6. Continue with normal tagging on the newly organised albums

Files that can't be matched to any album on MusicBrainz are left in place and logged as skipped.

#### Include all release types

```bash
python3 tag_mp3s.py /path/to/music --organize --all-release-types
```

By default `--organize` only shows Albums, Singles, and EPs. Use `--all-release-types` to also include Compilations, Live releases, and any other type in the list.

#### Survey loose files without moving them

```bash
python3 tag_mp3s.py /path/to/music --organize-report report.txt
python3 tag_mp3s.py /path/to/music --organize-report report.txt --filter "Artist Name"
```

Non-interactive survey mode — looks up each loose file on MusicBrainz, auto-picks the first result, and writes a text report of the proposed album groupings. **No files are moved or folders created.** Useful for reviewing matches before committing to `--organize`.

Output format:

```
=== Artist Name ===

Unmatched:
  some-unknown-track.mp3

Albums:
  [2005] The Black Halo
    - When the Lights Are Down.mp3
  [2007] Ghost Opera
    - Ghost Opera.mp3
```

---

### Filtering and Skipping

#### Filter by artist or album

```bash
python3 tag_mp3s.py /path/to/music --filter "Radiohead"
python3 tag_mp3s.py /path/to/music --filter "OK Computer"
```

Only processes artists or albums whose name contains the given text (case-insensitive). Useful for large libraries when you only want to process one artist or album at a time.

#### Skip already-tagged files

```bash
python3 tag_mp3s.py /path/to/music --skip-tagged
```

Skips files that already have a complete set of tags: title, artist, album, track number, year, genre, and cover art. If every file in an album is already fully tagged, the MusicBrainz lookup is skipped entirely — no API calls are made for that album, which significantly speeds up re-runs on large libraries.

---

### Cover Art

```bash
python3 tag_mp3s.py /path/to/music --no-art
python3 tag_mp3s.py /path/to/music --keep-art
```

- `--no-art` — skips downloading and embedding cover art entirely. Useful for faster runs or if you manage art separately.
- `--keep-art` — downloads new art from MusicBrainz but only embeds it in files that don't already have cover art. Files with existing art are left untouched.

By default, cover art is always downloaded and overwrites any existing embedded art.

---

### Cleaning Up Filenames and Tags

#### Remove artist name prefix from filenames

```bash
python3 tag_mp3s.py /path/to/music --strip-artist
python3 tag_mp3s.py /path/to/music --strip-artist --dry-run
```

Removes the artist name prefix from track filenames. For example:
- `Styx - Lady.mp3` → `Lady.mp3`
- `01 - Styx - Lady.mp3` → `01 - Lady.mp3`
- `01 Styx - Lady.mp3` → `01 Lady.mp3`

**Runs even with `--skip-tagged`** — this is a filename rename, not a tag write. It is not affected by `--skip-tagged` and will always process every file it finds the artist prefix on.

When combined with `--title-from-filename`, the artist is stripped from the filename *first*, so the cleaned name becomes the title tag — not the original name with the artist still in it.

#### Remove comment tags

```bash
python3 tag_mp3s.py /path/to/music --strip-comments
```

Removes all comment (`COMM`) frames from MP3 ID3 tags. These often contain junk left by ripping software or download tools (e.g. "Ripped with EAC", encoder settings). Use with `--dry-run` to preview which files have comments before removing them.

---

### Title from Filename

```bash
python3 tag_mp3s.py /path/to/music --title-from-filename
python3 tag_mp3s.py /path/to/music --title-from-filename --dry-run
```

Copies the filename (stripped of its leading track number) to the title tag. No MusicBrainz lookup, no internet connection needed. The number-stripping uses the same logic as the rest of the script:

| Filename | Written title |
|----------|--------------|
| `01 - Song Name.mp3` | `Song Name` |
| `02. Another Song.flac` | `Another Song` |
| `3 My Track.m4a` | `My Track` |
| `Just A Title.mp3` | `Just A Title` |

Only the title tag is written — all other tags (artist, album, track number, year, cover art) are left as-is. Works on MP3, FLAC, and M4A/AAC files.

**Runs even with `--skip-tagged`** — because it only touches the title tag and needs no online lookup, it is deliberately not affected by `--skip-tagged`. Files that would otherwise be skipped will still have their title tag updated from the filename.

If you are also using `--strip-artist`, the artist name is stripped from the filename *first*, then the cleaned filename is used as the title. So `01 - Blondie - Denis.mp3` becomes title `Denis`, not `Blondie - Denis`.

#### Combining with other flags — `--title-from-filename` always wins

`--title-from-filename` always runs **last** and always takes precedence over any title set by other flags. This means you can combine it with `--tag` or `--rename-tracks` and the filename will still be what ends up in the title tag:

| Flags | What happens |
|-------|-------------|
| `--title-from-filename` | No MB lookup; title written from filename only |
| `--tag --title-from-filename` | Full MB tagging (artist, album, art, etc.), then filename overwrites the title |
| `--rename-tracks --title-from-filename` | MB used to get track numbers for renaming; filename used for the title tag |
| `--tag --rename-tracks --title-from-filename` | MB tags + renames everything; filename overwrites the title at the end |

This is useful when your filenames are already the authoritative source for track titles but you still want MusicBrainz to fill in everything else (art, year, genre, track numbers, etc.).

---

### Override Genre

```bash
python3 tag_mp3s.py /path/to/music --genre "Rock"
```

Forces a specific genre across all albums. By default, genre is pulled from MusicBrainz community tags.

---

### Output Report

```bash
python3 tag_mp3s.py /path/to/music --output report.csv
```

Writes a CSV file with one row per action. Works with all other flags — in `--dry-run` mode, statuses show as `would_tag`/`would_rename` instead of `tagged`/`renamed`.

| Column | Description |
|--------|-------------|
| `type` | `file` or `folder` |
| `status` | `tagged`, `renamed`, `skipped`, `would_tag`, `would_rename` |
| `reason` | Why a file was skipped (empty otherwise) |
| `previous_path` | Full path before any changes |
| `new_path` | Path after rename (same as previous if not renamed) |
| `artist` | Artist name applied |
| `album` | Album name applied |
| `title` | Track title applied |
| `track` | Track number (e.g. `3/12`) |
| `genre` | Genre applied |
| `year` | Release year |
| `has_cover` | Whether cover art was embedded |
| `mb_matched` | Whether MusicBrainz found a match |

---

### Common Combinations

```bash
# Tag a single artist, preview first
python3 tag_mp3s.py /path/to/music --tag --filter "Radiohead" --dry-run

# Tag everything, confirm before applying
python3 tag_mp3s.py /path/to/music --tag --confirm

# Tag and rename tracks and folders in one pass
python3 tag_mp3s.py /path/to/music --tag --rename-tracks --confirm

# Re-run on a partially tagged library — skip already-complete files
python3 tag_mp3s.py /path/to/music --tag --skip-tagged

# Rename folders only (no tagging, no track file renames)
python3 tag_mp3s.py /path/to/music --rename-folders

# Rename folders only, skip albums already done
python3 tag_mp3s.py /path/to/music --rename-folders --skip-tagged

# Tag and strip junk comments, skip already-complete files
python3 tag_mp3s.py /path/to/music --tag --strip-comments --skip-tagged

# Strip artist prefix from filenames, preview first
python3 tag_mp3s.py /path/to/music --strip-artist --dry-run

# Organize loose files, then tag and rename everything
python3 tag_mp3s.py /path/to/music --organize --tag --rename-tracks --confirm

# Organize with all release types visible (Compilations, Live, etc.)
python3 tag_mp3s.py /path/to/music --organize --all-release-types

# Survey loose files for one artist before organizing
python3 tag_mp3s.py /path/to/music --organize-report survey.txt --filter "Radiohead"

# Set title tags from filenames only (no internet connection needed)
python3 tag_mp3s.py /path/to/music --title-from-filename

# Full MB tagging (art, year, genre, etc.) but keep filename as the title
python3 tag_mp3s.py /path/to/music --tag --title-from-filename

# Strip artist prefix from filenames then use the cleaned name as the title tag
python3 tag_mp3s.py /path/to/music --strip-artist --title-from-filename

# Tag with a genre override and save a change report
python3 tag_mp3s.py /path/to/music --tag --genre "Electronic" --output report.csv

# Tag but keep existing art, skip already-complete files, save report
python3 tag_mp3s.py /path/to/music --tag --keep-art --skip-tagged --output report.csv
```

---

## All Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview all changes without modifying any files |
| `--confirm` | Show a dry-run preview, then ask before applying |
| `--filter TEXT` | Only process artists/albums matching this text (case-insensitive) |
| `--tag` | Write metadata tags to audio files (title, artist, album, track, year, genre, cover art) |
| `--rename-tracks` | Rename track files to `NN - Title.ext` using MusicBrainz track numbers (title kept from original filename); also renames album folders to `[YEAR] Album` |
| `--rename-folders` | Rename album folders to `[YEAR] Album` without renaming track files |
| `--organize` | Interactively sort loose files into album subfolders using MusicBrainz |
| `--all-release-types` | Include Compilations, Live, and all other release types in `--organize` results (default: Albums, Singles, EPs only) |
| `--organize-report FILE` | Survey loose files and write a text report without moving anything |
| `--skip-tagged` | With `--tag`: skip files that already have complete tags; skips MusicBrainz lookup if all files in an album are tagged |
| `--genre TEXT` | With `--tag`: override genre for all albums instead of using MusicBrainz community tags |
| `--no-art` | With `--tag`: skip downloading and embedding cover art |
| `--keep-art` | With `--tag`: only embed art in files that don't already have it |
| `--strip-artist` | Remove artist name prefix from track filenames (e.g. `01 - Artist - Song.mp3` → `01 - Song.mp3`); runs even with `--skip-tagged` |
| `--strip-comments` | With `--tag`: remove all comment (COMM) frames from MP3 ID3 tags |
| `--title-from-filename` | Copy filename (stripped of leading track number) to title tag — always takes precedence over any other title source; runs even with `--skip-tagged`; no online lookup when used alone |
| `--output FILE` | Write a CSV report of all changes |

---

## What Gets Tagged

Each audio file receives the following tags (in the format-appropriate field):

| Tag | MP3 (ID3v2.4) | FLAC (Vorbis) | M4A (MP4) | Source |
|-----|---------------|---------------|-----------|--------|
| Song title | `TIT2` | `title` | `©nam` | MusicBrainz recording title, falls back to filename; always overridden by `--title-from-filename` |
| Artist | `TPE1` | `artist` | `©ART` | MusicBrainz canonical artist name |
| Album artist | `TPE2` | `albumartist` | `aART` | MusicBrainz canonical artist name |
| Album | `TALB` | `album` | `©alb` | MusicBrainz release title |
| Track number | `TRCK` | `tracknumber` | `trkn` | MusicBrainz (e.g. `3/12`), falls back to filename |
| Disc number | `TPOS` | `discnumber` | `disk` | MusicBrainz (e.g. `1/2`) |
| Year | `TDRC` | `date` | `©day` | MusicBrainz release date, falls back to folder name |
| Genre | `TCON` | `genre` | `©gen` | MusicBrainz community tags, or `--genre` override |
| Label | `TPUB` | `organization` | — | MusicBrainz label info |
| Cover art | `APIC` | `PICTURE` | `covr` | Cover Art Archive (front cover) |

---

## How It Works

1. **Scan** — walks the directory tree looking for `Artist/Album/track` structure, including multi-disc subfolders
2. **Organize** *(optional)* — if `--organize` is set, fetches the artist's discography, lets you pick albums interactively, creates subfolders, and moves files before tagging
3. **Parse** — extracts artist name, album name, year, and track numbers from folder and file names
4. **Search** — queries the MusicBrainz API with fuzzy fallback (strips edition suffixes and retries if exact search fails)
5. **Correct** — uses the canonical artist name and album title from MusicBrainz, preserving any edition suffixes from the original folder name
6. **Fetch** — retrieves detailed track list, genre tags, label, and cover art from Cover Art Archive
7. **Match** — maps each file to its MusicBrainz track info in a single pass, ensuring consistent metadata for both renaming and tagging
8. **Rename** *(optional)* — renames folders and/or files to canonical format
9. **Write** — applies tags to each audio file in the appropriate format
10. **Title override** *(optional)* — if `--title-from-filename` is set, overwrites the title tag from the filename as the final step, taking precedence over any MB title written in step 9
11. **Report** — prints a summary and optionally writes a CSV report

---

## Rate Limiting

MusicBrainz allows a maximum of 1 request per second. The script enforces this automatically, so processing a large library takes time — this is expected. Transient errors (503, 429, timeouts) are retried automatically with exponential backoff.

---

## Troubleshooting

**"No MusicBrainz match found"** — The artist or album name didn't match anything. Check that the artist folder name is spelled correctly. The script tries fuzzy matching (stripping suffixes like "Deluxe Edition"), but very different spellings won't match. Use `--filter` to isolate one artist or album for debugging.

**Wrong album matched** — If MusicBrainz returns the wrong release (e.g. a remaster instead of the original), check the dry-run output. Adjusting the album folder name to be more specific (e.g. adding the year) usually resolves it.

**"No cover art found"** — Not all releases have art on the Cover Art Archive. You can add it manually using any tag editor.

**Permission denied** — If a file is locked by another process (media player, cloud sync, file explorer preview) or marked read-only, it will be skipped with a warning. Close any programs that may have the file open and re-run with `--skip-tagged` to process only the files that were missed.

**Rate limit / 503 errors** — The script retries automatically, but an extremely large library can occasionally still hit limits. Use `--skip-tagged` on re-runs to avoid re-processing already-completed files.
