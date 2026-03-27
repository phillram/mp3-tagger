# MP3 Metadata Tagger

A Python script that automatically tags MP3 files with metadata from [MusicBrainz](https://musicbrainz.org/) and album art from the [Cover Art Archive](https://coverartarchive.org/).

## Features

- **Automatic metadata lookup** — searches MusicBrainz by artist name and album to find verified track titles, disc numbers, genres, labels, and release dates
- **Album cover art** — downloads front cover art from the Cover Art Archive and embeds it in each MP3
- **Folder renaming** — optionally renames album folders to a consistent `[YEAR] Album Name` format and track files to `NN - Title.mp3`
- **Hyphen normalization** — automatically replaces en-dashes, em-dashes, and other Unicode dash characters with standard hyphens (`-`) in all renamed folders and filenames
- **Strip comments** — optionally remove all comment (COMM) frames from ID3 tags, useful for cleaning out ripping software notes, encoder info, or other junk text
- **Output report** — generate a CSV report of all changes: previous paths, new paths, tagged files, and any skipped files
- **Dry-run mode** — preview all changes before anything is modified
- **Genre override** — force a specific genre across all albums
- **ID3v2.4 tags** — writes modern ID3v2.4 tags compatible with all major music players

## Requirements

- Python 3.10+
- Two Python packages: `mutagen` and `musicbrainzngs`

Install via the requirements file:

```bash
pip3 install -r requirements.txt
```

Or if that doesn't work, install them directly:

```bash
pip3 install mutagen musicbrainzngs
```

## Expected Folder Structure

The script expects your music to be organised as:

```
/music-root/
  Artist Name/
    [2024] Album Name/
      01 - Track One.mp3
      02 - Track Two.mp3
    [2020] Another Album/
      01. First Song.mp3
      02. Second Song.mp3
  Another Artist/
    [1997] Their Album/
      01 Intro.mp3
```

**Artist folders** should be named exactly as the artist appears on MusicBrainz (e.g. `Radiohead`, `Kendrick Lamar`).

**Album folders** are parsed flexibly — all of the following formats are understood and will be correctly renamed to `[YEAR] Album Name` when using `--rename`:

Year at the start:
- `[2024] Album Name` (preferred/target format)
- `(2024) Album Name`
- `2024 - Album Name`
- `2024 Album Name`

Year at the end:
- `Album Name (2024)`
- `Album Name [2024]`
- `Album Name - 2024`

No year:
- `Album Name` (year will be looked up from MusicBrainz)

**Track filenames** are parsed for the track number. These formats work:
- `01 - Track Name.mp3`
- `01. Track Name.mp3`
- `01 Track Name.mp3`

## Usage

### Preview changes (recommended first step)

```bash
python3 tag_mp3s.py /path/to/music --dry-run
```

This shows exactly what would be changed without modifying any files. Always run this first to verify the MusicBrainz matches are correct.

### Apply metadata tags

```bash
python3 tag_mp3s.py /path/to/music
```

### Rename folders and files to standard format

```bash
python3 tag_mp3s.py /path/to/music --rename
```

This will:
- Rename album folders to `[YEAR] Album Name` format using the canonical album title and year from MusicBrainz
- Rename track files to `NN - Track Title.mp3` format using the verified track titles from MusicBrainz
- Normalize all dashes to standard hyphens (`-`) — en-dashes (`–`), em-dashes (`—`), and other Unicode dash variants are replaced automatically
- Also apply all metadata tags

Use with `--dry-run` to preview renames first:

```bash
python3 tag_mp3s.py /path/to/music --rename --dry-run
```

### Override genre

```bash
python3 tag_mp3s.py /path/to/music --genre "Rock"
```

By default, genre is pulled from MusicBrainz community tags. Use `--genre` to force a specific genre across all albums.

### Skip album art

```bash
python3 tag_mp3s.py /path/to/music --no-art
```

Skips downloading and embedding cover art. Useful for faster runs or if you manage album art separately.

### Strip comments

```bash
python3 tag_mp3s.py /path/to/music --strip-comments
```

Removes all comment (COMM) frames from the ID3 tags on each MP3. These often contain junk text left by ripping software, encoders, or download tools (e.g. "Ripped with EAC", "Downloaded from...", encoder settings). Works with `--dry-run` to preview which files have comments before removing them.

### Generate an output report

```bash
python3 tag_mp3s.py /path/to/music --output report.csv
```

Writes a CSV file with one row per action. Columns include:

| Column | Description |
|--------|-------------|
| `type` | `file` or `folder` |
| `status` | `tagged`, `renamed`, `skipped`, `would_tag`, `would_rename` |
| `reason` | Why a file was skipped (empty if not skipped) |
| `previous_path` | Original full path before any changes |
| `new_path` | Path after rename (same as previous if not renamed) |
| `artist` | Artist name applied |
| `album` | Album name applied |
| `title` | Track title applied |
| `track` | Track number (e.g. `3/12`) |
| `genre` | Genre applied |
| `year` | Release year |
| `has_cover` | Whether cover art was embedded |
| `mb_matched` | Whether MusicBrainz found a match |

Works with all other flags including `--dry-run` (statuses will show `would_tag`/`would_rename` instead).

### Combine options

```bash
python3 tag_mp3s.py /path/to/music --rename --genre "Electronic" --dry-run
python3 tag_mp3s.py /path/to/music --rename --output report.csv
python3 tag_mp3s.py /path/to/music --rename --strip-comments --output report.csv
```

## What Gets Tagged

Each MP3 file receives the following ID3v2.4 tags:

| Tag | ID3 Frame | Source |
|-----|-----------|--------|
| Song title | TIT2 | MusicBrainz recording title, falls back to filename |
| Artist | TPE1 | Folder name |
| Album artist | TPE2 | Folder name |
| Album | TALB | MusicBrainz release title |
| Track number | TRCK | MusicBrainz (e.g. `3/12`), falls back to filename |
| Disc number | TPOS | MusicBrainz (e.g. `1/2`) |
| Year/date | TDRC | MusicBrainz release date, falls back to folder name |
| Genre | TCON | MusicBrainz community tags, or `--genre` override |
| Label/publisher | TPUB | MusicBrainz label info |
| Cover art | APIC | Cover Art Archive (front cover, 500px) |

## How It Works

1. **Scan** — walks the directory tree looking for `Artist/Album/track.mp3` structure
2. **Parse** — extracts artist name, album name, year, and track numbers from folder/file names
3. **Search** — queries the MusicBrainz API to find the matching release
4. **Fetch** — pulls detailed track info, genre tags, label, and cover art
5. **Write** — applies ID3v2.4 tags to each MP3 file
6. **Rename** (optional) — renames folders and files to the canonical format

## Rate Limiting

MusicBrainz requires a maximum of 1 request per second. The script automatically rate-limits itself, so processing a large library will take some time. This is expected and unavoidable.

## Troubleshooting

**"No MusicBrainz match found"** — The artist or album name didn't match anything. Check that the artist folder is spelled correctly. The script uses the folder name as-is for the search query.

**Wrong album matched** — If MusicBrainz returns the wrong release (e.g. a remaster instead of the original), check the dry-run output. You may need to adjust the album folder name to be more specific.

**"No cover art found"** — Not all releases have cover art on the Cover Art Archive. You can add art manually using any ID3 tag editor.

**Rate limit errors** — If you see HTTP 503 errors, MusicBrainz is throttling you. The script handles this with built-in delays, but an extremely large library might occasionally hit limits. Just re-run to continue where it left off (already-tagged files will be overwritten with the same data).
