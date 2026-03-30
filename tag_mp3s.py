#!/usr/bin/env python3
"""
Audio Metadata Tagger
=====================
Scans a folder structure of Artist/[YEAR] Album/track files,
looks up metadata from MusicBrainz + Cover Art Archive, and writes
tags to MP3, FLAC, and M4A files.

Usage:
    python3 tag_mp3s.py /path/to/music
    python3 tag_mp3s.py /path/to/music --dry-run
    python3 tag_mp3s.py /path/to/music --confirm
    python3 tag_mp3s.py /path/to/music --filter "Radiohead"
    python3 tag_mp3s.py /path/to/music --rename --skip-tagged
    python3 tag_mp3s.py /path/to/music --output report.csv

Requirements:
    pip3 install mutagen musicbrainzngs
"""

import argparse
import csv
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

try:
    import mutagen
    from mutagen.id3 import (
        ID3, TIT2, TPE1, TPE2, TALB, TRCK, TPOS, TDRC, TCON, TPUB,
        APIC, COMM, ID3NoHeaderError
    )
except ImportError:
    print("ERROR: 'mutagen' is not installed. Run: pip3 install mutagen")
    sys.exit(1)

try:
    import musicbrainzngs as mb
except ImportError:
    print("ERROR: 'musicbrainzngs' is not installed. Run: pip3 install musicbrainzngs")
    sys.exit(1)

# MusicBrainz requires a user-agent
mb.set_useragent("MP3Tagger", "1.0", "https://github.com/example/mp3tagger")

# Rate limiting: MusicBrainz allows 1 request/sec
_last_mb_request = 0.0

# Common album name suffixes to strip for fuzzy matching
_ALBUM_SUFFIXES = re.compile(
    r'\s*[\(\[](deluxe|special|remaster(ed)?|expanded|anniversary|bonus tracks?|'
    r'limited|collector.s?|standard|explicit|clean|mono|stereo|'
    r'\d+th anniversary|re-?issue|redux|super deluxe|platinum|gold)'
    r'(\s+edition)?[\)\]]\s*$',
    re.IGNORECASE
)


def _rate_limit():
    global _last_mb_request
    now = time.time()
    elapsed = now - _last_mb_request
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_mb_request = time.time()


def _mb_api_call(func, *args, retries: int = 2, **kwargs):
    """Call a MusicBrainz API function with rate limiting and retry on transient errors."""
    for attempt in range(retries + 1):
        _rate_limit()
        try:
            return func(*args, **kwargs)
        except mb.WebServiceError as e:
            if attempt < retries and _is_transient_error(e):
                wait = 2 ** attempt
                print(f"  Retrying MusicBrainz request in {wait}s (attempt {attempt + 1})...")
                time.sleep(wait)
                continue
            raise


def _is_transient_error(e: Exception) -> bool:
    """Check if a MusicBrainz error is transient (worth retrying)."""
    msg = str(e).lower()
    return any(hint in msg for hint in ['503', '429', 'timeout', 'timed out', 'rate limit',
                                         'service unavailable', 'connection'])


def normalize_hyphens(text: str) -> str:
    """Replace en-dashes, em-dashes, and other dash-like Unicode characters with a basic hyphen."""
    # Covers: en-dash (–), em-dash (—), figure dash, horizontal bar, minus sign, etc.
    return re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]', '-', text)


def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are unsafe for filesystems, and normalize hyphens."""
    name = normalize_hyphens(name)
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name


def strip_artist_from_filename(filename: str, artist_name: str) -> str | None:
    """If a filename starts with 'Artist - Title', return 'Title' (preserving track number
    prefix and extension). Returns None if the artist name is not found in the filename."""
    stem = Path(filename).stem
    ext = Path(filename).suffix
    normalized = normalize_hyphens(stem)

    # Try with leading track number: "01 Artist - Title" or "01. Artist - Title"
    m = re.match(r'^(\d{1,3}[\s.]*)', normalized)
    prefix = m.group(1) if m else ''
    rest = normalized[len(prefix):]

    # Check if the remainder starts with "Artist - " (case-insensitive)
    pattern = re.escape(normalize_hyphens(artist_name)) + r'\s*-\s*'
    m = re.match(pattern, rest, re.IGNORECASE)
    if m:
        title_part = rest[m.end():].strip()
        if title_part:
            return prefix + title_part + ext

    return None


def parse_album_folder(folder_name: str) -> tuple[str | None, str | None]:
    """Parse album folder name in various formats. Returns (year, album_name).

    Supported formats:
        [2024] Album Name
        (2024) Album Name
        2024 - Album Name
        2024 Album Name
        Album Name (2024)
        Album Name [2024]
        Album Name - 2024
        Album Name
    """
    # Year at start in brackets: [2024] Album Name or (2024) Album Name
    m = re.match(r'[\[\(](\d{4})[\]\)]\s+(.+)', folder_name)
    if m:
        return m.group(1), m.group(2).strip()

    # Year at start with dash: 2024 - Album Name (any dash type)
    m = re.match(r'(\d{4})\s*[-\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]+\s*(.+)', folder_name)
    if m:
        return m.group(1), m.group(2).strip()

    # Year at start with space: 2024 Album Name
    m = re.match(r'(\d{4})\s+(.+)', folder_name)
    if m:
        return m.group(1), m.group(2).strip()

    # Year at end in brackets: Album Name (2024) or Album Name [2024]
    m = re.match(r'(.+?)\s*[\[\(](\d{4})[\]\)]\s*$', folder_name)
    if m:
        return m.group(2), m.group(1).strip()

    # Year at end with dash: Album Name - 2024 (any dash type)
    m = re.match(r'(.+?)\s*[-\u2010-\u2015\u2212\uFE58\uFE63\uFF0D]+\s*(\d{4})\s*$', folder_name)
    if m:
        return m.group(2), m.group(1).strip()

    # No year found — return the folder name as the album name
    return None, folder_name


def is_correct_album_format(folder_name: str) -> bool:
    """Check if folder name already matches [YEAR] Album Name format."""
    return bool(re.match(r'^\[\d{4}\]\s+.+', folder_name))


def build_album_folder_name(year: str, album_name: str) -> str:
    """Build the canonical [YEAR] Album Name folder name with normalized hyphens."""
    safe_name = sanitize_filename(album_name)
    return f"[{year}] {safe_name}"


def _preserve_album_suffix(original_album: str, mb_album: str) -> str:
    """Preserve trailing parenthesized/bracketed suffixes from the original album name
    when the MusicBrainz title doesn't include them.

    Example: original="Album (Deluxe Edition)", mb="Album" -> "Album (Deluxe Edition)"
    """
    suffix_match = re.search(r'(\s*(?:[\(\[][^)\]]+[\)\]]\s*)+)$', original_album)
    if not suffix_match:
        return mb_album

    suffix = suffix_match.group(1).rstrip()

    # If MB title already contains this suffix text, no change needed
    if suffix.strip().lower() in mb_album.lower():
        return mb_album

    # If MB title already has its own trailing parenthesized/bracketed text, don't double up
    if re.search(r'\s*[\(\[][^)\]]+[\)\]]\s*$', mb_album):
        return mb_album

    return mb_album.rstrip() + suffix


def rename_album_folder(album_dir: Path, year: str, album_name: str,
                        dry_run: bool, log: list) -> Path:
    """Rename an album folder to [YEAR] Album Name format. Returns the new path."""
    correct_name = build_album_folder_name(year, album_name)
    if album_dir.name == correct_name:
        return album_dir

    new_path = album_dir.parent / correct_name

    # Handle collision
    if new_path.exists() and new_path != album_dir:
        print(f"  WARNING: Cannot rename '{album_dir.name}' -> '{correct_name}' (target already exists)")
        log.append({
            'type': 'folder',
            'status': 'skipped',
            'reason': 'Target folder already exists',
            'previous_path': str(album_dir),
            'new_path': str(new_path),
        })
        return album_dir

    if dry_run:
        print(f"  WOULD RENAME: '{album_dir.name}' -> '{correct_name}'")
        log.append({
            'type': 'folder',
            'status': 'would_rename',
            'previous_path': str(album_dir),
            'new_path': str(new_path),
        })
        return album_dir
    else:
        album_dir.rename(new_path)
        print(f"  RENAMED: '{album_dir.name}' -> '{correct_name}'")
        log.append({
            'type': 'folder',
            'status': 'renamed',
            'previous_path': str(album_dir),
            'new_path': str(new_path),
        })
        return new_path


def rename_track_files(file_to_track: dict[str, dict | None], dry_run: bool,
                       log: list) -> dict[str, str]:
    """Rename track files to 'NN - Track Title.ext' using pre-matched track info.

    Returns a mapping of old_path -> new_path for files that were actually renamed.
    """
    path_updates: dict[str, str] = {}
    for filepath_str, track_info in file_to_track.items():
        if not track_info:
            continue

        mp3_path = Path(filepath_str)
        mb_title = track_info['title']
        track_num = track_info['track_num']

        # Preserve trailing parenthesized/bracketed suffixes from the original
        # filename that aren't in the MusicBrainz title (e.g. "(2002 Remaster)")
        _, original_title = parse_track_filename(mp3_path.name)
        title_with_suffix = _preserve_album_suffix(original_title, mb_title)

        safe_title = sanitize_filename(title_with_suffix)
        ext = mp3_path.suffix
        new_name = f"{track_num:02d} - {safe_title}{ext}"

        if mp3_path.name == new_name:
            continue

        # Rename in the file's own directory (may be a disc subfolder)
        new_path = mp3_path.parent / new_name
        if new_path.exists() and new_path != mp3_path:
            print(f"  WARNING: Cannot rename '{mp3_path.name}' -> '{new_name}' (target exists)")
            log.append({
                'type': 'file',
                'status': 'skipped',
                'reason': 'Target file already exists',
                'previous_path': filepath_str,
                'new_path': str(new_path),
            })
            continue

        if dry_run:
            print(f"  WOULD RENAME FILE: '{mp3_path.name}' -> '{new_name}'")
            log.append({
                'type': 'file',
                'status': 'would_rename',
                'previous_path': filepath_str,
                'new_path': str(new_path),
            })
        else:
            mp3_path.rename(new_path)
            print(f"  RENAMED FILE: '{mp3_path.name}' -> '{new_name}'")
            log.append({
                'type': 'file',
                'status': 'renamed',
                'previous_path': filepath_str,
                'new_path': str(new_path),
            })
            path_updates[filepath_str] = str(new_path)

    return path_updates


def _file_has_cover_art(filepath: str) -> bool:
    """Check if a file already has embedded cover art."""
    ext = Path(filepath).suffix.lower()
    try:
        if ext == '.mp3':
            tags = ID3(filepath)
            return bool(tags.getall('APIC'))
        elif ext == '.flac':
            from mutagen.flac import FLAC
            audio = FLAC(filepath)
            return bool(audio.pictures)
        elif ext in ('.m4a', '.mp4', '.aac'):
            from mutagen.mp4 import MP4
            audio = MP4(filepath)
            return bool(audio.tags and audio.tags.get('covr'))
    except Exception:
        return False
    return False


def has_complete_tags(filepath: str) -> bool:
    """Check if a file already has a complete set of tags including genre and cover art."""
    ext = Path(filepath).suffix.lower()
    try:
        if ext == '.mp3':
            tags = ID3(filepath)
            required = ['TIT2', 'TPE1', 'TALB', 'TRCK', 'TDRC', 'TCON']
            if not all(tags.getall(frame) for frame in required):
                return False
            return bool(tags.getall('APIC'))
        elif ext == '.flac':
            from mutagen.flac import FLAC
            audio = FLAC(filepath)
            required = ['title', 'artist', 'album', 'tracknumber', 'date', 'genre']
            if not all(audio.get(tag) for tag in required):
                return False
            return bool(audio.pictures)
        elif ext in ('.m4a', '.mp4', '.aac'):
            from mutagen.mp4 import MP4
            audio = MP4(filepath)
            required = ['\xa9nam', '\xa9ART', '\xa9alb', 'trkn', '\xa9day', '\xa9gen']
            if not (audio.tags and all(audio.tags.get(tag) for tag in required)):
                return False
            return bool(audio.tags.get('covr'))
    except Exception:
        return False
    return False


def parse_track_filename(filename: str) -> tuple[int | None, str]:
    """Extract track number and title from filename. Returns (track_num, title)."""
    name = Path(filename).stem

    # Try: 01 - Track Name, 01. Track Name, 01 Track Name (any dash type)
    m = re.match(r'^(\d{1,3})\s*[-\u2010-\u2015\u2212\uFE58\uFE63\uFF0D.]\s*(.+)', name)
    if m:
        return int(m.group(1)), m.group(2).strip()

    m = re.match(r'^(\d{1,3})\s+(.+)', name)
    if m:
        return int(m.group(1)), m.group(2).strip()

    return None, name


def match_files_to_tracks(audio_files: list[Path], track_map: dict) -> dict[str, dict | None]:
    """Build a mapping of audio file path (str) -> MusicBrainz track info.

    Performs track matching once so the same mapping can be used for both
    renaming and tagging, ensuring each file gets the correct metadata.
    """
    file_to_track: dict[str, dict | None] = {}
    for mp3_path in audio_files:
        file_track_num, _ = parse_track_filename(mp3_path.name)

        track_info = None
        if track_map and file_track_num:
            # Infer disc number from subfolder name (CD1, Disc 2, etc.)
            disc_match = re.match(r'(?:cd|disc|disk)\s*(\d+)', mp3_path.parent.name, re.IGNORECASE)
            inferred_disc = int(disc_match.group(1)) if disc_match else None

            if inferred_disc:
                track_info = track_map.get((inferred_disc, file_track_num))
            if not track_info:
                track_info = track_map.get((1, file_track_num))
            if not track_info:
                for key, val in track_map.items():
                    if key[1] == file_track_num:
                        track_info = val
                        break

        file_to_track[str(mp3_path)] = track_info

    return file_to_track


def _search_mb_releases(artist: str, album: str, year: str | None = None) -> list:
    """Search MusicBrainz with progressive fallback: exact -> no year -> stripped suffixes."""
    searches = []

    # 1. Exact query (with year if available)
    query = f'artist:"{artist}" AND release:"{album}"'
    if year:
        query += f' AND date:{year}'
    searches.append(query)

    # 2. Without year
    if year:
        searches.append(f'artist:"{artist}" AND release:"{album}"')

    # 3. With common suffixes stripped (fuzzy)
    stripped = _ALBUM_SUFFIXES.sub('', album).strip()
    if stripped != album:
        searches.append(f'artist:"{artist}" AND release:"{stripped}"')

    for query in searches:
        try:
            results = _mb_api_call(mb.search_releases, query=query, limit=5)
            releases = results.get('release-list', [])
            if releases:
                return releases
        except mb.WebServiceError as e:
            print(f"  WARNING: MusicBrainz search failed: {e}")
            return []

    return []


def get_canonical_artist_name(release: dict) -> str | None:
    """Extract the canonical artist name from a MusicBrainz release."""
    credit = release.get('artist-credit', [])
    if credit:
        # artist-credit can be a list of dicts with 'artist' key
        if isinstance(credit, list) and credit:
            artist_entry = credit[0]
            if isinstance(artist_entry, dict):
                artist_obj = artist_entry.get('artist', {})
                return artist_obj.get('name')
            # Sometimes it's a plain string
            if isinstance(artist_entry, str):
                return artist_entry
    return None


def search_release(artist: str, album: str, year: str | None = None) -> dict | None:
    """Search MusicBrainz for a release and return full metadata."""
    releases = _search_mb_releases(artist, album, year)
    if not releases:
        return None

    # Pick the best match (highest score)
    release = releases[0]
    release_id = release['id']

    # Fetch full release details including recordings
    try:
        full = _mb_api_call(
            mb.get_release_by_id,
            release_id,
            includes=['recordings', 'artist-credits', 'labels', 'release-groups']
        )
    except mb.WebServiceError as e:
        print(f"  WARNING: Could not fetch release details: {e}")
        return None

    return full.get('release', full)


def _release_type_sort_key(release_type: str) -> int:
    """Return a sort key for album type ordering: Album → EP → Single → Compilation → Live → Other."""
    t = release_type.lower() if release_type else ''
    if t == 'album':   return 0
    elif t == 'ep':    return 1
    elif t == 'single': return 2
    elif t == 'compilation': return 3
    elif t == 'live':  return 4
    else:              return 5


def _search_mb_recording_options(artist: str, title: str) -> list[dict]:
    """Search MusicBrainz for a recording by artist and title.

    Returns a list of unique album options deduplicated by (album_name_lower, year),
    sorted with studio albums first and compilations last. Each option is a dict
    with keys: album, year, release_id, recording_title, release_type.
    """
    query = f'artist:"{artist}" AND recording:"{title}"'
    try:
        results = _mb_api_call(mb.search_recordings, query=query, limit=25)
        recordings = results.get('recording-list', [])
        if not recordings:
            return []

        seen: set[tuple[str, str | None]] = set()
        options: list[dict] = []

        for recording in recordings:
            for release in recording.get('release-list', []):
                album = release.get('title', '')
                year = release['date'][:4] if release.get('date') else None
                key = (album.lower(), year)
                if key in seen:
                    continue
                seen.add(key)

                # Extract release group type (Album, Single, Compilation, etc.)
                rg = release.get('release-group', {})
                release_type = rg.get('type', '') if rg else ''

                options.append({
                    'album': album,
                    'year': year,
                    'release_id': release.get('id'),
                    'recording_title': recording.get('title', title),
                    'release_type': release_type,
                })

        # Sort: studio albums first, compilations last
        options.sort(key=lambda o: _release_type_sort_key(o.get('release_type', '')))

        return options
    except mb.WebServiceError as e:
        print(f"    WARNING: MusicBrainz recording search failed: {e}")
        return []


def _fetch_artist_albums(artist: str, include_compilations: bool = False) -> list[dict]:
    """Fetch all studio albums (and optionally compilations) for an artist from MusicBrainz.

    Returns a list of dicts with keys: album, year, release_id, release_type.
    Results are sorted by year (oldest first).
    """
    try:
        results = _mb_api_call(mb.search_artists, query=f'artist:"{artist}"', limit=5)
        artist_list = results.get('artist-list', [])
        if not artist_list:
            return []

        # Pick the best-matching artist
        artist_id = artist_list[0].get('id')
        if not artist_id:
            return []

        # Fetch release groups (albums, singles, EPs, and optionally compilations)
        type_filter = ['album', 'single', 'ep']
        if include_compilations:
            type_filter.append('compilation')

        offset = 0
        all_rgs: list[dict] = []
        while True:
            rg_results = _mb_api_call(
                mb.browse_release_groups,
                artist=artist_id,
                release_type=type_filter,
                limit=100,
                offset=offset,
            )
            rgs = rg_results.get('release-group-list', [])
            if not rgs:
                break
            all_rgs.extend(rgs)
            if len(all_rgs) >= int(rg_results.get('release-group-count', 0)):
                break
            offset += len(rgs)

        albums: list[dict] = []
        seen: set[str] = set()
        for rg in all_rgs:
            title = rg.get('title', '')
            rg_type = rg.get('type', '')
            year = rg.get('first-release-date', '')[:4] or None
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            albums.append({
                'album': title,
                'year': year,
                'release_id': None,  # We don't have a specific release ID from release groups
                'recording_title': None,
                'release_type': rg_type,
            })

        albums.sort(key=lambda a: (
            _release_type_sort_key(a.get('release_type', '')),
            a.get('year') or '9999',
        ))
        return albums
    except mb.WebServiceError as e:
        print(f"    WARNING: Could not fetch artist albums: {e}")
        return []


def get_release_group_info(release: dict) -> dict:
    """Extract genre/type info from the release group."""
    info = {}
    rg = release.get('release-group', {})
    if rg:
        rg_id = rg.get('id')
        if rg_id:
            try:
                rg_full = _mb_api_call(mb.get_release_group_by_id, rg_id, includes=['tags'])
                rg_data = rg_full.get('release-group', rg_full)
                tags = rg_data.get('tag-list', [])
                if tags:
                    # Sort by count, pick top tag as genre
                    tags.sort(key=lambda t: int(t.get('count', 0)), reverse=True)
                    info['genre'] = tags[0]['name'].title()
            except mb.WebServiceError:
                pass
    return info


def fetch_cover_art(release_id: str, retries: int = 2) -> bytes | None:
    """Fetch front cover art from the Cover Art Archive with retry."""
    url = f"https://coverartarchive.org/release/{release_id}/front-500"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'MP3Tagger/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            # Don't retry on 404 (no art exists)
            if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                break
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
    return None


def build_track_list(release: dict) -> dict[tuple[int, int], dict]:
    """Build a mapping of (disc_num, track_num) -> track info from a release."""
    tracks = {}
    media_list = release.get('medium-list', [])
    for medium in media_list:
        disc_num = int(medium.get('position', 1))
        for track in medium.get('track-list', []):
            track_num = int(track.get('position', track.get('number', 0)))
            recording = track.get('recording', {})
            title = recording.get('title', track.get('title', ''))
            tracks[(disc_num, track_num)] = {
                'title': title,
                'disc_num': disc_num,
                'track_num': track_num,
                'total_tracks': int(medium.get('track-count', 0)),
                'total_discs': len(media_list),
            }
    return tracks


def apply_tags(
    filepath: str,
    artist: str,
    album: str,
    year: str | None,
    track_info: dict | None,
    genre: str | None,
    label: str | None,
    cover_art: bytes | None,
    dry_run: bool = False,
    strip_comments: bool = False,
) -> dict:
    """Apply tags to an audio file (MP3/FLAC/M4A). Returns a summary of changes."""
    changes = {}

    # Parse what we can from the filename as fallback
    file_track_num, file_title = parse_track_filename(os.path.basename(filepath))

    title = (track_info or {}).get('title', file_title)
    track_num = (track_info or {}).get('track_num', file_track_num)
    total_tracks = (track_info or {}).get('total_tracks')
    disc_num = (track_info or {}).get('disc_num', 1)
    total_discs = (track_info or {}).get('total_discs', 1)

    changes['title'] = title
    changes['artist'] = artist
    changes['album'] = album
    changes['track'] = f"{track_num}/{total_tracks}" if total_tracks else str(track_num) if track_num else None
    changes['disc'] = f"{disc_num}/{total_discs}" if total_discs else str(disc_num)
    changes['year'] = year
    changes['genre'] = genre
    changes['label'] = label
    changes['has_cover'] = cover_art is not None
    changes['comments_removed'] = False

    ext = Path(filepath).suffix.lower()

    # Check for existing comments before any modifications (MP3 only)
    if strip_comments and ext == '.mp3':
        try:
            existing_tags = ID3(filepath)
            comm_frames = existing_tags.getall('COMM')
            if comm_frames:
                changes['comments_removed'] = True
                changes['comments_found'] = '; '.join(
                    str(f.text[0]) if f.text else f.desc for f in comm_frames
                )
        except ID3NoHeaderError:
            pass

    if dry_run:
        return changes

    if ext == '.mp3':
        _apply_mp3_tags(filepath, artist, album, year, title, track_num,
                        total_tracks, disc_num, total_discs, genre, label,
                        cover_art, strip_comments)
    elif ext == '.flac':
        _apply_flac_tags(filepath, artist, album, year, title, track_num,
                         total_tracks, disc_num, total_discs, genre, label,
                         cover_art)
    elif ext in ('.m4a', '.mp4', '.aac'):
        _apply_m4a_tags(filepath, artist, album, year, title, track_num,
                        total_tracks, disc_num, total_discs, genre, cover_art)

    return changes


def _apply_mp3_tags(filepath, artist, album, year, title, track_num,
                    total_tracks, disc_num, total_discs, genre, label,
                    cover_art, strip_comments):
    """Apply ID3v2.4 tags to an MP3 file."""
    try:
        tags = ID3(filepath)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall('TIT2')
    tags.add(TIT2(encoding=3, text=[title]))
    tags.delall('TPE1')
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.delall('TPE2')
    tags.add(TPE2(encoding=3, text=[artist]))
    tags.delall('TALB')
    tags.add(TALB(encoding=3, text=[album]))

    if track_num:
        tags.delall('TRCK')
        track_str = f"{track_num}/{total_tracks}" if total_tracks else str(track_num)
        tags.add(TRCK(encoding=3, text=[track_str]))

    tags.delall('TPOS')
    disc_str = f"{disc_num}/{total_discs}" if total_discs else str(disc_num)
    tags.add(TPOS(encoding=3, text=[disc_str]))

    if year:
        tags.delall('TDRC')
        tags.add(TDRC(encoding=3, text=[year]))
    if genre:
        tags.delall('TCON')
        tags.add(TCON(encoding=3, text=[genre]))
    if label:
        tags.delall('TPUB')
        tags.add(TPUB(encoding=3, text=[label]))

    if cover_art:
        tags.delall('APIC')
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3,
                       desc='Front Cover', data=cover_art))

    if strip_comments:
        tags.delall('COMM')

    tags.save(filepath, v2_version=4)


def _apply_flac_tags(filepath, artist, album, year, title, track_num,
                     total_tracks, disc_num, total_discs, genre, label,
                     cover_art):
    """Apply Vorbis comments to a FLAC file."""
    from mutagen.flac import FLAC, Picture

    audio = FLAC(filepath)
    audio['title'] = title
    audio['artist'] = artist
    audio['albumartist'] = artist
    audio['album'] = album

    if track_num:
        audio['tracknumber'] = str(track_num)
        if total_tracks:
            audio['tracktotal'] = str(total_tracks)

    audio['discnumber'] = str(disc_num)
    if total_discs:
        audio['disctotal'] = str(total_discs)

    if year:
        audio['date'] = year
    if genre:
        audio['genre'] = genre
    if label:
        audio['organization'] = label

    if cover_art:
        audio.clear_pictures()
        pic = Picture()
        pic.type = 3  # Cover (front)
        pic.mime = 'image/jpeg'
        pic.desc = 'Front Cover'
        pic.data = cover_art
        audio.add_picture(pic)

    audio.save()


def _apply_m4a_tags(filepath, artist, album, year, title, track_num,
                    total_tracks, disc_num, total_discs, genre, cover_art):
    """Apply MP4/M4A tags."""
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(filepath)
    if audio.tags is None:
        audio.add_tags()

    audio.tags['\xa9nam'] = [title]
    audio.tags['\xa9ART'] = [artist]
    audio.tags['aART'] = [artist]
    audio.tags['\xa9alb'] = [album]

    if track_num:
        audio.tags['trkn'] = [(track_num, total_tracks or 0)]
    audio.tags['disk'] = [(disc_num, total_discs or 0)]

    if year:
        audio.tags['\xa9day'] = [year]
    if genre:
        audio.tags['\xa9gen'] = [genre]

    if cover_art:
        audio.tags['covr'] = [MP4Cover(cover_art, imageformat=MP4Cover.FORMAT_JPEG)]

    audio.save()


AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.mp4', '.aac'}


def find_audio_files(directory: Path) -> list[Path]:
    """Find all supported audio files in a directory, including multi-disc subfolders."""
    files = []
    for ext in AUDIO_EXTENSIONS:
        files.extend(directory.glob(f'*{ext}'))

    # Check for multi-disc subfolders: CD1, CD2, Disc 1, Disc 2, etc.
    disc_pattern = re.compile(r'^(cd|disc|disk)\s*\d+$', re.IGNORECASE)
    for subdir in sorted(directory.iterdir()):
        if subdir.is_dir() and disc_pattern.match(subdir.name):
            for ext in AUDIO_EXTENSIONS:
                files.extend(subdir.glob(f'*{ext}'))

    return sorted(files, key=lambda p: (p.parent.name, p.name))


def organize_loose_files(artist_name: str, artist_dir: Path, audio_files: list[Path],
                         dry_run: bool, log: list,
                         include_compilations: bool = False) -> list[Path]:
    """Organize loose audio files into album subfolders using MusicBrainz data.

    Interactive: always presents a numbered list of album options and asks the
    user to choose.  The list includes all studio albums from the artist's
    discography (fetched once upfront), with recording-specific matches marked
    with an asterisk (*) and shown first.

    By default only studio albums, singles, and EPs are shown. Pass
    include_compilations=True to also show compilations and other types.

    All options are sorted by release year (oldest first).

    Returns list of album directories that were created.
    """
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Organizing {len(audio_files)} loose file(s) for {artist_name}")

    # Fetch full discography for this artist once
    print(f"  Fetching discography for {artist_name}...")
    all_artist_albums = _fetch_artist_albums(artist_name, include_compilations=include_compilations)
    if all_artist_albums:
        print(f"  Found {len(all_artist_albums)} album(s) in discography")
    else:
        print(f"  Could not fetch discography — will rely on recording searches only")

    # Group files by album using interactive MusicBrainz recording lookups
    album_groups: dict[tuple, dict] = {}
    unmatched: list[Path] = []

    for audio_path in sorted(audio_files, key=lambda p: p.name):
        _, file_title = parse_track_filename(audio_path.name)
        print(f"  Looking up: {file_title}")

        # Search for this specific recording to find which albums it appears on
        recording_options = _search_mb_recording_options(artist_name, file_title)

        # Filter out compilations/other unless --include-compilations
        if not include_compilations:
            recording_options = [o for o in recording_options
                                 if o.get('release_type', '').lower() in ('album', 'single', 'ep', '')]

        # Build merged list: recording matches first (marked), then remaining artist albums
        recording_keys = {(o['album'].lower(), o.get('year')) for o in recording_options}

        # Mark recording matches
        for o in recording_options:
            o['_matched'] = True

        # Add remaining artist albums that weren't in the recording results
        remaining = []
        for a in all_artist_albums:
            key = (a['album'].lower(), a.get('year'))
            if key not in recording_keys:
                entry = dict(a)
                entry['_matched'] = False
                remaining.append(entry)

        # Combine: recording matches first, then rest of discography
        options = recording_options + remaining

        # Sort by type (Album → EP → Single → Compilation → Live → Other),
        # then by year within each type (oldest first, unknown years last)
        options.sort(key=lambda o: (
            _release_type_sort_key(o.get('release_type', '')),
            o.get('year') or '9999',
        ))

        if not options:
            print(f"    No album found")
            unmatched.append(audio_path)
            continue

        # Always show numbered list and ask user to choose
        print(f"    Album options:")
        for i, opt in enumerate(options, 1):
            rtype = f" [{opt.get('release_type')}]" if opt.get('release_type') else ''
            match_marker = ' *' if opt.get('_matched') else ''
            print(f"      {i}. ({opt.get('year', '?')}) {opt['album']}{rtype}{match_marker}")
        print(f"      0. Skip this file")
        try:
            choice = input(f"    Select [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n    Skipping remaining files.")
            unmatched.append(audio_path)
            break
        if choice == '0':
            print(f"    Skipped")
            unmatched.append(audio_path)
            continue
        if choice == '':
            idx = 0
        else:
            try:
                idx = int(choice) - 1
            except ValueError:
                idx = 0
        if idx < 0 or idx >= len(options):
            idx = 0
        selected = options[idx]
        print(f"    -> {selected['album']} ({selected.get('year', '?')})")

        album_key = (selected['album'], selected.get('year', ''))
        if album_key not in album_groups:
            album_groups[album_key] = {
                'album': selected['album'],
                'year': selected.get('year'),
                'release_id': selected.get('release_id'),
                'files': [],
            }
        album_groups[album_key]['files'].append(audio_path)

    if unmatched:
        print(f"  {len(unmatched)} file(s) could not be matched to an album")
        for f in unmatched:
            log.append({
                'type': 'file',
                'status': 'skipped',
                'reason': 'No MusicBrainz album match found for recording',
                'previous_path': str(f),
                'new_path': '',
            })

    # Create album folders and move files
    created_dirs: list[Path] = []
    for (_album_key, _year_key), group in album_groups.items():
        album_name = group['album']
        year = group['year']

        if year:
            folder_name = build_album_folder_name(year, album_name)
        else:
            folder_name = sanitize_filename(album_name)

        album_dir = artist_dir / folder_name

        if dry_run:
            print(f"  WOULD CREATE: '{folder_name}/' ({len(group['files'])} file(s))")
            for f in group['files']:
                print(f"    WOULD MOVE: '{f.name}' -> '{folder_name}/'")
                log.append({
                    'type': 'file',
                    'status': 'would_rename',
                    'previous_path': str(f),
                    'new_path': str(album_dir / f.name),
                })
        else:
            album_dir.mkdir(exist_ok=True)
            print(f"  CREATED: '{folder_name}/'")
            for f in group['files']:
                new_path = album_dir / f.name
                if new_path.exists():
                    print(f"    WARNING: Cannot move '{f.name}' (target exists in '{folder_name}/')")
                    log.append({
                        'type': 'file',
                        'status': 'skipped',
                        'reason': 'Target file already exists in album folder',
                        'previous_path': str(f),
                        'new_path': str(new_path),
                    })
                    continue
                f.rename(new_path)
                print(f"    MOVED: '{f.name}' -> '{folder_name}/'")
                log.append({
                    'type': 'file',
                    'status': 'renamed',
                    'previous_path': str(f),
                    'new_path': str(new_path),
                })
            created_dirs.append(album_dir)

    return created_dirs


def survey_loose_files(artist_name: str, audio_files: list[Path]) -> tuple[dict, list]:
    """Survey loose files via MusicBrainz without moving them (for report mode).

    Non-interactive: auto-picks the first result for each file.
    Returns (album_groups, unmatched) where album_groups maps
    folder_name -> list of filenames, and unmatched is a list of filenames.
    """
    album_groups: dict[str, list[str]] = {}
    unmatched: list[str] = []

    for audio_path in sorted(audio_files, key=lambda p: p.name):
        _, file_title = parse_track_filename(audio_path.name)
        print(f"  Looking up: {file_title}")

        options = _search_mb_recording_options(artist_name, file_title)
        if not options:
            print(f"    No album found")
            unmatched.append(audio_path.name)
            continue

        selected = options[0]
        year = selected.get('year')
        album = selected['album']
        if year:
            folder_name = build_album_folder_name(year, album)
        else:
            folder_name = sanitize_filename(album)

        print(f"    -> {album} ({year or '?'})")

        if folder_name not in album_groups:
            album_groups[folder_name] = []
        album_groups[folder_name].append(audio_path.name)

    return album_groups, unmatched


def strip_artist_from_files(artist_name: str, audio_files: list[Path],
                            dry_run: bool, log: list) -> list[Path]:
    """Rename audio files by stripping the artist name prefix.

    E.g. 'Styx - Lady.mp3' -> 'Lady.mp3', '01 Styx - Lady.mp3' -> '01 Lady.mp3'.
    Returns updated list of Paths (reflecting any renames).
    """
    updated: list[Path] = []
    for audio_path in audio_files:
        new_name = strip_artist_from_filename(audio_path.name, artist_name)
        if not new_name or new_name == audio_path.name:
            updated.append(audio_path)
            continue

        new_path = audio_path.parent / new_name
        if new_path.exists() and new_path != audio_path:
            print(f"  WARNING: Cannot strip artist from '{audio_path.name}' ('{new_name}' already exists)")
            updated.append(audio_path)
            continue

        if dry_run:
            print(f"  WOULD STRIP ARTIST: '{audio_path.name}' -> '{new_name}'")
            log.append({
                'type': 'file',
                'status': 'would_rename',
                'previous_path': str(audio_path),
                'new_path': str(new_path),
            })
            updated.append(audio_path)
        else:
            audio_path.rename(new_path)
            print(f"  STRIPPED ARTIST: '{audio_path.name}' -> '{new_name}'")
            log.append({
                'type': 'file',
                'status': 'renamed',
                'previous_path': str(audio_path),
                'new_path': str(new_path),
            })
            updated.append(new_path)

    return updated


def process_album(artist_name: str, album_dir: Path, genre_override: str | None,
                  dry_run: bool, skip_art: bool, rename: bool, strip_comments: bool,
                  log: list, skip_tagged: bool = False, keep_art: bool = False,
                  do_strip_artist: bool = False, rename_folders: bool = False) -> int:
    """Process all audio files in an album directory. Returns count of files processed."""
    folder_name = album_dir.name
    year, album_name = parse_album_folder(folder_name)

    mp3_files = find_audio_files(album_dir)
    if not mp3_files:
        return 0

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing: {artist_name} - {album_name} ({year or 'unknown year'})")
    print(f"  Found {len(mp3_files)} audio file(s)")

    # If --skip-tagged, check if ALL files are already fully tagged.
    # If so, skip the entire album (no MusicBrainz API calls needed).
    if skip_tagged and all(has_complete_tags(str(f)) for f in mp3_files):
        print(f"  All files already tagged — skipping album")
        return 0

    # Strip artist name from filenames before any matching (e.g. "Styx - Lady.mp3" -> "Lady.mp3")
    if do_strip_artist:
        mp3_files = strip_artist_from_files(artist_name, mp3_files, dry_run, log)

    # Look up on MusicBrainz
    release = search_release(artist_name, album_name, year)
    track_map = {}
    genre = genre_override
    label = None
    cover_art = None

    if release:
        print(f"  MusicBrainz match: {release.get('title', '?')} (id: {release.get('id', '?')[:8]}...)")
        track_map = build_track_list(release)

        # Correct artist name from MusicBrainz canonical spelling
        canonical_artist = get_canonical_artist_name(release)
        if canonical_artist and canonical_artist != artist_name:
            print(f"  Artist correction: '{artist_name}' -> '{canonical_artist}'")
            artist_name = canonical_artist

        # Get genre from release group tags
        if not genre:
            rg_info = get_release_group_info(release)
            genre = rg_info.get('genre')

        # Get label
        label_list = release.get('label-info-list', [])
        if label_list:
            label = label_list[0].get('label', {}).get('name')

        # Update year from release if we didn't have one
        if not year and release.get('date'):
            year = release['date'][:4]

        # Use the MusicBrainz album title, preserving any edition suffix from the
        # original folder name (e.g. "(Deluxe Edition)") that MB doesn't include.
        mb_album_name = _preserve_album_suffix(album_name, release.get('title', album_name))

        # Fetch cover art
        if not skip_art:
            print("  Fetching album art...")
            cover_art = fetch_cover_art(release['id'])
            if cover_art:
                print(f"  Got cover art ({len(cover_art) // 1024}KB)")
            else:
                print("  No cover art found on Cover Art Archive")
    else:
        mb_album_name = album_name
        print("  WARNING: No MusicBrainz match found — using filename metadata only")

    # Match every file to its MusicBrainz track info ONCE, before any renaming.
    # This single mapping is then used for both renaming and tagging so each
    # file is guaranteed to get the correct metadata.
    file_to_track = match_files_to_tracks(mp3_files, track_map)

    # Rename album folder to [YEAR] Album Name format
    if (rename or rename_folders) and year:
        old_album_dir = album_dir
        album_dir = rename_album_folder(album_dir, year, mb_album_name, dry_run, log)
        # Update file_to_track paths after folder rename
        if not dry_run and album_dir != old_album_dir:
            updated: dict[str, dict | None] = {}
            old_prefix = str(old_album_dir)
            new_prefix = str(album_dir)
            for fpath, tinfo in file_to_track.items():
                if fpath.startswith(old_prefix):
                    updated[new_prefix + fpath[len(old_prefix):]] = tinfo
                else:
                    updated[fpath] = tinfo
            file_to_track = updated

    # Rename track files to "NN - Title.ext" format using the pre-matched mapping
    if rename and track_map:
        path_updates = rename_track_files(file_to_track, dry_run, log)
        # Update file_to_track paths after file renames
        if path_updates:
            updated = {}
            for fpath, tinfo in file_to_track.items():
                updated[path_updates.get(fpath, fpath)] = tinfo
            file_to_track = updated

    count = 0
    skipped_tagged = 0
    for filepath_str, track_info in file_to_track.items():
        mp3_path = Path(filepath_str)

        # Skip already-tagged files if requested
        if skip_tagged and has_complete_tags(filepath_str):
            skipped_tagged += 1
            continue

        # Determine cover art for this file
        file_cover_art = cover_art
        if keep_art and cover_art:
            # Preserve existing art if the file already has embedded art
            if _file_has_cover_art(filepath_str):
                file_cover_art = None

        try:
            changes = apply_tags(
                filepath=filepath_str,
                artist=artist_name,
                album=mb_album_name,
                year=year,
                track_info=track_info,
                genre=genre,
                label=label,
                cover_art=file_cover_art,
                dry_run=dry_run,
                strip_comments=strip_comments,
            )
        except (PermissionError, mutagen.MutagenError) as e:
            print(f"  ERROR: Could not tag '{mp3_path.name}': {e}")
            log.append({
                'type': 'file',
                'status': 'skipped',
                'reason': str(e),
                'previous_path': filepath_str,
                'new_path': filepath_str,
            })
            continue

        status = "WOULD TAG" if dry_run else "TAGGED"
        title = changes.get('title', '?')
        track = changes.get('track', '?')
        art_indicator = " [+art]" if changes.get('has_cover') else ""
        genre_str = f" [{changes.get('genre')}]" if changes.get('genre') else ""
        comment_indicator = " [-comments]" if changes.get('comments_removed') else ""
        print(f"  {status}: {track} - {title}{genre_str}{art_indicator}{comment_indicator}")

        log.append({
            'type': 'file',
            'status': 'would_tag' if dry_run else 'tagged',
            'previous_path': filepath_str,
            'new_path': filepath_str,
            'artist': changes.get('artist', ''),
            'album': changes.get('album', ''),
            'title': changes.get('title', ''),
            'track': changes.get('track', ''),
            'genre': changes.get('genre', ''),
            'year': changes.get('year', ''),
            'has_cover': str(changes.get('has_cover', False)),
            'mb_matched': str(release is not None),
        })
        count += 1

    if skipped_tagged:
        print(f"  Skipped {skipped_tagged} already-tagged file(s)")

    return count


def scan_and_process(root: str, genre_override: str | None, dry_run: bool, skip_art: bool,
                     rename: bool = False, strip_comments: bool = False,
                     output_file: str | None = None, filter_str: str | None = None,
                     skip_tagged: bool = False, keep_art: bool = False,
                     confirm: bool = False, organize: bool = False,
                     organize_report: str | None = None,
                     strip_artist: bool = False,
                     include_compilations: bool = False,
                     rename_folders: bool = False):
    """Scan the root music directory and process all artist/album folders."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        print(f"ERROR: '{root}' is not a directory")
        sys.exit(1)

    # --confirm mode: run a dry-run preview first, then ask before applying
    if confirm and not dry_run:
        print("PREVIEW MODE — showing what would be changed...\n")
        scan_and_process(root, genre_override, dry_run=True, skip_art=skip_art,
                         rename=rename, strip_comments=strip_comments,
                         output_file=None, filter_str=filter_str,
                         skip_tagged=skip_tagged, keep_art=keep_art,
                         confirm=False, organize=organize,
                         organize_report=None, strip_artist=strip_artist,
                         include_compilations=include_compilations,
                         rename_folders=rename_folders)
        print()
        try:
            answer = input("Apply these changes? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return
        if answer not in ('y', 'yes'):
            print("Aborted — no changes were made.")
            return
        print()

    print(f"Scanning: {root_path}")
    if dry_run:
        print("DRY RUN MODE — no files will be modified\n")

    log: list[dict] = []
    organize_report_data: list[dict] = []
    total = 0
    stats = {'artists': 0, 'albums': 0, 'files': 0, 'mb_matched': 0, 'mb_unmatched': 0,
             'skipped': 0, 'renamed_folders': 0, 'renamed_files': 0}
    artist_dirs = sorted([d for d in root_path.iterdir() if d.is_dir()])

    if not artist_dirs:
        print("No artist directories found.")
        return

    # Check if the root itself looks like an artist folder (contains audio files in subdirs)
    has_audio_in_subdirs = False
    for d in artist_dirs:
        if find_audio_files(d):
            has_audio_in_subdirs = True
            break

    if has_audio_in_subdirs:
        print(f"NOTE: It looks like '{root_path.name}' might be an artist folder.")
        print("       Expected structure: ArtistName/[Year] Album/tracks.mp3")
        print("       If results look wrong, pass the parent directory instead.\n")

    # Apply filter
    filter_lower = filter_str.lower() if filter_str else None

    for artist_dir in artist_dirs:
        if not artist_dir.is_dir():
            continue

        artist_name = artist_dir.name

        # Filter by artist name
        if filter_lower and filter_lower not in artist_name.lower():
            continue

        album_dirs = sorted([d for d in artist_dir.iterdir() if d.is_dir()])

        # Handle loose audio files in artist folder (flat structure)
        direct_audio = [f for f in artist_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
        if direct_audio and organize_report:
            print(f"\n  Surveying {len(direct_audio)} loose file(s) for {artist_name}")
            albums, unmatched = survey_loose_files(artist_name, direct_audio)
            organize_report_data.append({
                'artist': artist_name,
                'albums': albums,
                'unmatched': unmatched,
            })
        elif direct_audio and organize:
            organize_loose_files(artist_name, artist_dir, direct_audio, dry_run, log,
                                 include_compilations=include_compilations)
            # Re-scan album dirs after organizing (new folders may have been created)
            if not dry_run:
                album_dirs = sorted([d for d in artist_dir.iterdir() if d.is_dir()])
        elif direct_audio and not album_dirs:
            print(f"\n  WARNING: Audio files found directly in '{artist_name}/' — skipping.")
            print(f"           Expected: {artist_name}/[YEAR] Album Name/track.mp3")
            print(f"           Use --organize to auto-create album folders from MusicBrainz data")
            print(f"           Use --organize-report FILE to survey albums without moving files")
            for audio_path in direct_audio:
                stats['skipped'] += 1
                log.append({
                    'type': 'file',
                    'status': 'skipped',
                    'reason': 'Audio file found directly in artist folder (no album subfolder)',
                    'previous_path': str(audio_path),
                    'new_path': '',
                    'artist': artist_name,
                    'album': '',
                    'title': '',
                    'track': '',
                    'genre': '',
                    'year': '',
                    'has_cover': '',
                    'mb_matched': '',
                })
            continue

        stats['artists'] += 1

        for album_dir in album_dirs:
            if not album_dir.is_dir():
                continue

            # Filter by album name too if filter doesn't match artist
            if filter_lower and filter_lower not in artist_name.lower():
                _, album_name = parse_album_folder(album_dir.name)
                if filter_lower not in album_name.lower():
                    continue

            stats['albums'] += 1
            count = process_album(artist_name, album_dir, genre_override, dry_run,
                                  skip_art, rename, strip_comments, log,
                                  skip_tagged=skip_tagged, keep_art=keep_art,
                                  do_strip_artist=strip_artist,
                                  rename_folders=rename_folders)
            stats['files'] += count
            total += count

    # Count stats from log
    for entry in log:
        if entry.get('mb_matched') == 'True':
            stats['mb_matched'] += 1
        elif entry.get('mb_matched') == 'False':
            stats['mb_unmatched'] += 1
        if entry.get('status') in ('renamed', 'would_rename'):
            if entry.get('type') == 'folder':
                stats['renamed_folders'] += 1
            else:
                stats['renamed_files'] += 1
        if entry.get('status') == 'skipped':
            stats['skipped'] += 1

    # Print summary
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}{'=' * 40}")
    print(f"{prefix}Summary:")
    print(f"{prefix}  Artists processed:   {stats['artists']}")
    print(f"{prefix}  Albums processed:    {stats['albums']}")
    print(f"{prefix}  Files tagged:        {stats['files']}")
    print(f"{prefix}  MusicBrainz matched: {stats['mb_matched']}")
    print(f"{prefix}  MusicBrainz missed:  {stats['mb_unmatched']}")
    if stats['renamed_folders'] or stats['renamed_files']:
        print(f"{prefix}  Folders renamed:     {stats['renamed_folders']}")
        print(f"{prefix}  Files renamed:       {stats['renamed_files']}")
    if stats['skipped']:
        print(f"{prefix}  Skipped:             {stats['skipped']}")
    print(f"{prefix}{'=' * 40}")

    # Write output report
    if output_file:
        write_output_report(output_file, log, dry_run)

    # Write organize survey report
    if organize_report and organize_report_data:
        write_organize_report(organize_report, organize_report_data)


def write_output_report(output_file: str, log: list[dict], dry_run: bool):
    """Write the processing log to a CSV file."""
    output_path = Path(output_file).resolve()

    fieldnames = [
        'type', 'status', 'reason', 'previous_path', 'new_path',
        'artist', 'album', 'title', 'track', 'genre', 'year',
        'has_cover', 'mb_matched',
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for entry in log:
            writer.writerow(entry)

    print(f"Output report written to: {output_path}")
    print(f"  Total entries: {len(log)}")

    # Summary counts
    tagged = sum(1 for e in log if e['status'] in ('tagged', 'would_tag'))
    renamed = sum(1 for e in log if e['status'] in ('renamed', 'would_rename'))
    skipped = sum(1 for e in log if e['status'] == 'skipped')
    print(f"  Tagged: {tagged}, Renamed: {renamed}, Skipped: {skipped}")


def write_organize_report(output_file: str, report_data: list[dict]):
    """Write an organize survey report to a text file.

    report_data is a list of dicts, each with keys:
      artist: str
      albums: dict[str, list[str]]   (folder_name -> list of filenames)
      unmatched: list[str]           (filenames that couldn't be matched)
    """
    output_path = Path(output_file).resolve()

    with open(output_path, 'w', encoding='utf-8') as f:
        for entry in report_data:
            f.write(f"=== {entry['artist']} ===\n")

            if entry['unmatched']:
                f.write("\nUnmatched:\n")
                for name in entry['unmatched']:
                    f.write(f"  {name}\n")

            if entry['albums']:
                f.write("\nAlbums:\n")
                for folder_name, filenames in sorted(entry['albums'].items()):
                    f.write(f"  {folder_name}\n")
                    for name in filenames:
                        f.write(f"    - {name}\n")

            f.write("\n")

    print(f"\nOrganize report written to: {output_path}")
    total_artists = len(report_data)
    total_albums = sum(len(e['albums']) for e in report_data)
    total_unmatched = sum(len(e['unmatched']) for e in report_data)
    print(f"  Artists: {total_artists}, Albums: {total_albums}, Unmatched files: {total_unmatched}")


def main():
    parser = argparse.ArgumentParser(
        description='Tag audio files (MP3/FLAC/M4A) with metadata from MusicBrainz',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Expected folder structure:
  /music-root/
    Artist Name/
      [2024] Album Name/
        01 - Track One.mp3
        02 - Track Two.flac
      [2020] Another Album/
        CD1/
          01. First Song.mp3
        CD2/
          01. Bonus Track.mp3

Supported formats: MP3, FLAC, M4A/MP4/AAC

Examples:
  %(prog)s /path/to/music --dry-run              # preview changes
  %(prog)s /path/to/music                        # apply tags
  %(prog)s /path/to/music --confirm              # preview then ask before applying
  %(prog)s /path/to/music --genre Rock           # force genre
  %(prog)s /path/to/music --no-art               # skip album art
  %(prog)s /path/to/music --keep-art             # don't overwrite existing art
  %(prog)s /path/to/music --rename               # also fix folder/file names
  %(prog)s /path/to/music --rename --dry-run     # preview renames
  %(prog)s /path/to/music --skip-tagged          # skip already-tagged files
  %(prog)s /path/to/music --filter "Radiohead"   # process one artist only
  %(prog)s /path/to/music --strip-comments       # remove ID3 comments
  %(prog)s /path/to/music --strip-artist         # remove artist name from filenames
  %(prog)s /path/to/music --organize             # sort loose files into album folders
  %(prog)s /path/to/music --organize --include-compilations  # include compilations
  %(prog)s /path/to/music --organize-report r.txt # survey loose files without moving
  %(prog)s /path/to/music --rename-folders        # rename folders only (not tracks)
  %(prog)s /path/to/music --output report.csv    # generate output report
        """
    )
    parser.add_argument('directory', help='Root music directory to scan')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without modifying files')
    parser.add_argument('--confirm', action='store_true',
                        help='Show a preview of changes and ask for confirmation before applying')
    parser.add_argument('--genre', type=str, default=None,
                        help='Override genre for all albums (e.g., "Rock", "Hip-Hop")')
    parser.add_argument('--no-art', action='store_true',
                        help='Skip fetching album cover art')
    parser.add_argument('--keep-art', action='store_true',
                        help='Preserve existing embedded cover art (don\'t overwrite)')
    parser.add_argument('--rename', action='store_true',
                        help='Rename album folders to [YEAR] Album Name format and '
                             'track files to "NN - Title.ext" using MusicBrainz data')
    parser.add_argument('--skip-tagged', action='store_true',
                        help='Skip files that already have complete tags '
                             '(title, artist, album, track number, year)')
    parser.add_argument('--filter', type=str, default=None, metavar='TEXT',
                        dest='filter_str',
                        help='Only process artists/albums matching this text '
                             '(case-insensitive substring match)')
    parser.add_argument('--strip-comments', action='store_true',
                        help='Remove all comment (COMM) frames from MP3 ID3 tags')
    parser.add_argument('--strip-artist', action='store_true',
                        help='Remove artist name prefix from track filenames '
                             '(e.g. "Styx - Lady.mp3" -> "Lady.mp3")')
    parser.add_argument('--organize', action='store_true',
                        help='Interactively look up loose files in artist folders on '
                             'MusicBrainz, let you pick the correct album, create '
                             'subfolders, and move files before tagging')
    parser.add_argument('--organize-report', type=str, default=None, metavar='FILE',
                        help='Survey loose files via MusicBrainz and write a text report '
                             'of album groupings without moving any files')
    parser.add_argument('--include-compilations', action='store_true',
                        help='Include compilations and other album types in '
                             '--organize results (by default only studio albums, '
                             'singles, and EPs are shown)')
    parser.add_argument('--rename-folders', action='store_true',
                        help='Rename album folders to [YEAR] Album Name format '
                             'without renaming track files')
    parser.add_argument('--output', type=str, default=None, metavar='FILE',
                        help='Write a CSV report of all changes (previous paths, '
                             'new paths, skipped files)')

    args = parser.parse_args()
    scan_and_process(
        args.directory, args.genre, args.dry_run, args.no_art,
        rename=args.rename, strip_comments=args.strip_comments,
        output_file=args.output, filter_str=args.filter_str,
        skip_tagged=args.skip_tagged, keep_art=args.keep_art,
        confirm=args.confirm, organize=args.organize,
        organize_report=args.organize_report,
        strip_artist=args.strip_artist,
        include_compilations=args.include_compilations,
        rename_folders=args.rename_folders,
    )


if __name__ == '__main__':
    main()
