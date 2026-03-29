# Audio Metadata Tagger

A Python script that automatically tags audio files (MP3, FLAC, M4A) with metadata from [MusicBrainz](https://musicbrainz.org/) and album art from the [Cover Art Archive](https://coverartarchive.org/).

## Features

- **Multi-format support** — tags MP3 (ID3v2.4), FLAC (Vorbis comments), and M4A/MP4/AAC files
- **Automatic metadata lookup** — searches MusicBrainz by artist name and album to find verified track titles, disc numbers, genres, labels, and release dates
- **Fuzzy matching** — if an exact album name search fails, automatically strips common suffixes like "Deluxe Edition", "Remastered", etc. and retries
- **Artist name correction** — detects and corrects misspelled artist names using the canonical spelling from MusicBrainz (e.g. `Rhianna` -> `Rihanna`)
- **Album cover art** — downloads front cover art from the Cover Art Archive and embeds it in each file
- **Preserve existing art** — `--keep-art` prevents overwriting cover art that's already embedded
- **Multi-disc support** — automatically detects `CD1/`, `CD2/`, `Disc 1/`, etc. subfolders within album directories and maps tracks to the correct disc
- **Folder renaming** — optionally renames album folders to a consistent `[YEAR] Album Name` format and track files to `NN - Title.ext`. Edition suffixes like "(Deluxe Edition)" in folder names are preserved
- **Organize loose files** — `--organize` interactively looks up loose audio files on MusicBrainz, lets you pick the correct album from a list (studio albums shown first, compilations last), then creates subfolders and moves files
- **Organize report** — `--organize-report FILE` surveys loose files via MusicBrainz and writes a text report of album groupings and unmatched files without moving anything
- **Strip artist from filenames** — `--strip-artist` removes the artist name prefix from track filenames (e.g. `Styx - Lady.mp3` becomes `Lady.mp3`)
- **Hyphen normalization** — automatically replaces en-dashes, em-dashes, and other Unicode dash characters with standard hyphens (`-`) in all renamed folders and filenames
- **Strip comments** — optionally remove all comment (COMM) frames from ID3 tags, useful for cleaning out ripping software notes, encoder info, or other junk text
- **Skip already-tagged** — `--skip-tagged` skips files that already have complete tags (including genre and cover art), saving time on re-runs
- **Filter by artist/album** — `--filter` processes only matching artists or albums in a large library
- **Confirmation mode** — `--confirm` shows a full preview and asks for approval before making changes
- **Output report** — generate a CSV report of all changes: previous paths, new paths, tagged files, and any skipped files
- **Summary stats** — prints a summary at the end showing counts for artists, albums, files tagged, MusicBrainz matches/misses, renames, and skips
- **Error resilience** — files that can't be written (e.g. locked or read-only) are skipped with a warning instead of stopping the entire run
- **Retry on errors** — automatically retries MusicBrainz API calls on transient network errors (503, 429, timeouts) with exponential backoff
- **Dry-run mode** — preview all changes before anything is modified
- **Genre override** — force a specific genre across all albums

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
      02 - Track Two.flac
    [2020] Another Album/
      01. First Song.m4a
      02. Second Song.m4a
  Another Artist/
    [1997] Their Album/
      CD1/
        01 Intro.mp3
        02 Track Two.mp3
      CD2/
        01 Bonus Track.mp3
```

If some of your artists have loose files without album subfolders (e.g. `Artist/01 - Song.mp3`), use `--organize` to interactively sort them into album folders using MusicBrainz lookups, or `--organize-report FILE` to survey the matches first without moving anything.

**Artist folders** should be named as the artist appears on MusicBrainz (e.g. `Radiohead`, `Kendrick Lamar`). If the name is slightly wrong, the script will detect the canonical spelling from MusicBrainz and use it in the tags (with a note in the output).

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

**Multi-disc albums** are supported via subfolders named `CD1`, `CD2`, `Disc 1`, `Disc 2`, `Disk1`, etc. (case-insensitive). Tracks inside these subfolders are automatically mapped to the correct disc number from MusicBrainz.

**Track filenames** are parsed for the track number. These formats work:
- `01 - Track Name.mp3`
- `01. Track Name.flac`
- `01 Track Name.m4a`

**Supported audio formats:** MP3 (.mp3), FLAC (.flac), M4A/MP4/AAC (.m4a, .mp4, .aac)

## Usage

### Preview changes (recommended first step)

```bash
python3 tag_mp3s.py /path/to/music --dry-run
```

This shows exactly what would be changed without modifying any files. Always run this first to verify the MusicBrainz matches are correct.

### Confirm before applying

```bash
python3 tag_mp3s.py /path/to/music --confirm
```

Runs a full dry-run preview, then asks `Apply these changes? [y/N]` before proceeding. A good middle ground between `--dry-run` and applying immediately.

### Apply metadata tags

```bash
python3 tag_mp3s.py /path/to/music
```

### Filter by artist or album

```bash
python3 tag_mp3s.py /path/to/music --filter "Radiohead"
python3 tag_mp3s.py /path/to/music --filter "OK Computer"
```

Only processes artists or albums whose name contains the given text (case-insensitive). Useful for large libraries when you only want to tag one artist or album.

### Rename folders and files to standard format

```bash
python3 tag_mp3s.py /path/to/music --rename
```

This will:
- Rename album folders to `[YEAR] Album Name` format using the canonical album title and year from MusicBrainz
- Rename track files to `NN - Track Title.ext` format using the verified track titles from MusicBrainz
- Normalize all dashes to standard hyphens (`-`) — en-dashes, em-dashes, and other Unicode dash variants are replaced automatically
- Also apply all metadata tags

Use with `--dry-run` to preview renames first:

```bash
python3 tag_mp3s.py /path/to/music --rename --dry-run
```

### Organize loose files into album folders

```bash
python3 tag_mp3s.py /path/to/music --organize
```

If audio files are found directly in an artist folder (e.g. `Radiohead/Karma Police.mp3` instead of `Radiohead/[1997] OK Computer/Karma Police.mp3`), `--organize` will:

1. Look up each track on MusicBrainz by artist name + song title
2. If multiple albums are found, present a numbered list and let you pick the correct one (default is 1, enter 0 to skip)
3. If only one album is found, auto-select it
4. Group the files by your selected albums
5. Create `[YEAR] Album Name` subfolders
6. Move the files into the correct album folders
7. Then continue with normal tagging on the newly created albums

Use with `--dry-run` to preview what would happen:

```bash
python3 tag_mp3s.py /path/to/music --organize --dry-run
```

Files that can't be matched to any album on MusicBrainz are left in place and logged as skipped.

### Survey loose files without moving them

```bash
python3 tag_mp3s.py /path/to/music --organize-report report.txt
```

If you want to review the album groupings before actually organizing, `--organize-report` will:

1. Look up each loose file on MusicBrainz (non-interactive, auto-picks the first result)
2. Write a text report listing each artist, their matched albums in `[YEAR] Album Name` format, and any unmatched files
3. **No files are moved or folders created** — this is a read-only survey

The output file looks like:

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

Use `--filter` to limit the survey to specific artists:

```bash
python3 tag_mp3s.py /path/to/music --organize-report report.txt --filter "Radiohead"
```

### Skip already-tagged files

```bash
python3 tag_mp3s.py /path/to/music --skip-tagged
```

Skips files that already have complete tags (title, artist, album, track number, and year). Useful for re-running the script on a library that's partially tagged — only untagged or incomplete files will be processed.

### Override genre

```bash
python3 tag_mp3s.py /path/to/music --genre "Rock"
```

By default, genre is pulled from MusicBrainz community tags. Use `--genre` to force a specific genre across all albums.

### Skip album art / preserve existing art

```bash
python3 tag_mp3s.py /path/to/music --no-art
python3 tag_mp3s.py /path/to/music --keep-art
```

- `--no-art` skips downloading and embedding cover art entirely. Useful for faster runs or if you manage album art separately.
- `--keep-art` downloads new art from MusicBrainz but only embeds it in files that don't already have cover art. Files with existing art are left untouched.

### Strip comments

```bash
python3 tag_mp3s.py /path/to/music --strip-comments
```

Removes all comment (COMM) frames from the ID3 tags on each MP3. These often contain junk text left by ripping software, encoders, or download tools (e.g. "Ripped with EAC", "Downloaded from...", encoder settings). Works with `--dry-run` to preview which files have comments before removing them.

### Strip artist name from filenames

```bash
python3 tag_mp3s.py /path/to/music --strip-artist
python3 tag_mp3s.py /path/to/music --strip-artist --dry-run
```

Removes the artist name prefix from track filenames. Useful when files are named `Artist - Song Title.mp3` and you want just `Song Title.mp3` (or `01 Song Title.mp3` if a track number is present). Works with `--dry-run` to preview renames first.

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
python3 tag_mp3s.py /path/to/music --rename --strip-comments --skip-tagged
python3 tag_mp3s.py /path/to/music --filter "Radiohead" --rename --confirm
python3 tag_mp3s.py /path/to/music --keep-art --skip-tagged --output report.csv
python3 tag_mp3s.py /path/to/music --strip-artist --rename --dry-run
python3 tag_mp3s.py /path/to/music --organize --rename --confirm
python3 tag_mp3s.py /path/to/music --organize-report survey.txt --filter "Radiohead"
```

## All Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview changes without modifying files |
| `--confirm` | Preview changes, then ask before applying |
| `--rename` | Rename folders to `[YEAR] Album` and files to `NN - Title.ext` |
| `--genre TEXT` | Override genre for all albums |
| `--no-art` | Skip fetching album cover art |
| `--keep-art` | Don't overwrite existing embedded cover art |
| `--skip-tagged` | Skip files with complete tags (including genre and cover art) |
| `--filter TEXT` | Only process matching artists/albums |
| `--strip-comments` | Remove ID3 comment frames |
| `--strip-artist` | Remove artist name prefix from track filenames |
| `--organize` | Interactively sort loose files into album subfolders |
| `--organize-report FILE` | Survey loose files and write a text report (no files moved) |
| `--output FILE` | Write a CSV report of all changes |

## What Gets Tagged

Each audio file receives the following tags (format-appropriate):

| Tag | MP3 (ID3) | FLAC (Vorbis) | M4A (MP4) | Source |
|-----|-----------|---------------|-----------|--------|
| Song title | TIT2 | title | \xa9nam | MusicBrainz recording title, falls back to filename |
| Artist | TPE1 | artist | \xa9ART | MusicBrainz canonical artist name |
| Album artist | TPE2 | albumartist | aART | MusicBrainz canonical artist name |
| Album | TALB | album | \xa9alb | MusicBrainz release title |
| Track number | TRCK | tracknumber | trkn | MusicBrainz (e.g. `3/12`), falls back to filename |
| Disc number | TPOS | discnumber | disk | MusicBrainz (e.g. `1/2`) |
| Year/date | TDRC | date | \xa9day | MusicBrainz release date, falls back to folder name |
| Genre | TCON | genre | \xa9gen | MusicBrainz community tags, or `--genre` override |
| Label/publisher | TPUB | organization | — | MusicBrainz label info |
| Cover art | APIC | PICTURE | covr | Cover Art Archive (front cover, 500px) |

## How It Works

1. **Scan** — walks the directory tree looking for `Artist/Album/track` structure (including multi-disc subfolders)
2. **Organize** (optional) — if `--organize` is set, looks up loose files via MusicBrainz recordings, creates album folders, and moves files in
3. **Parse** — extracts artist name, album name, year, and track numbers from folder/file names
4. **Search** — queries the MusicBrainz API to find the matching release, with fuzzy fallback
5. **Correct** — uses canonical artist name and album title from MusicBrainz (preserving edition suffixes like "Deluxe Edition" in folder names)
6. **Fetch** — pulls detailed track info, genre tags, label, and cover art
7. **Match** — maps each file to its MusicBrainz track info in a single pass, ensuring consistent metadata for both renames and tags
8. **Write** — applies tags to each audio file in the appropriate format
9. **Rename** (optional) — renames folders and files to the canonical format
10. **Report** — prints summary stats and optionally writes a CSV report

## Rate Limiting

MusicBrainz requires a maximum of 1 request per second. The script automatically rate-limits itself, so processing a large library will take some time. This is expected and unavoidable. Transient errors (503, 429, timeouts) are automatically retried with exponential backoff.

## Troubleshooting

**"No MusicBrainz match found"** — The artist or album name didn't match anything. Check that the artist folder is spelled correctly. The script tries fuzzy matching by stripping common suffixes like "Deluxe Edition", but very different spellings won't match. Use `--filter` to isolate specific albums for debugging.

**Wrong album matched** — If MusicBrainz returns the wrong release (e.g. a remaster instead of the original), check the dry-run output. You may need to adjust the album folder name to be more specific.

**"No cover art found"** — Not all releases have cover art on the Cover Art Archive. You can add art manually using any tag editor.

**Permission denied errors** — If a file is locked by another process (media player, file explorer preview, cloud sync) or marked read-only, the script will skip it with an error message and continue to the next file. Close any programs that might have the file open and re-run with `--skip-tagged` to process only the files that were missed.

**Rate limit errors** — If you see HTTP 503 errors, MusicBrainz is throttling you. The script handles this with automatic retries and built-in delays, but an extremely large library might occasionally hit limits. Use `--skip-tagged` on re-runs to avoid re-processing already-completed files.
