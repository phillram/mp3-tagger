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


def rename_track_files(album_dir: Path, track_map: dict, dry_run: bool, log: list):
    """Rename track files to 'NN - Track Title.ext' format using MusicBrainz data."""
    audio_files = find_audio_files(album_dir)
    for mp3_path in audio_files:
        file_track_num, file_title = parse_track_filename(mp3_path.name)
        if not file_track_num or not track_map:
            continue

        # Infer disc number from subfolder name (CD1, Disc 2, etc.)
        disc_match = re.match(r'(?:cd|disc|disk)\s*(\d+)', mp3_path.parent.name, re.IGNORECASE)
        inferred_disc = int(disc_match.group(1)) if disc_match else None

        # Find matching track info
        track_info = None
        if inferred_disc:
            track_info = track_map.get((inferred_disc, file_track_num))
        if not track_info:
            track_info = track_map.get((1, file_track_num))
        if not track_info:
            for key, val in track_map.items():
                if key[1] == file_track_num:
                    track_info = val
                    break

        if not track_info:
            continue

        mb_title = track_info['title']
        track_num = track_info['track_num']
        safe_title = sanitize_filename(mb_title)
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
                'previous_path': str(mp3_path),
                'new_path': str(new_path),
            })
            continue

        if dry_run:
            print(f"  WOULD RENAME FILE: '{mp3_path.name}' -> '{new_name}'")
            log.append({
                'type': 'file',
                'status': 'would_rename',
                'previous_path': str(mp3_path),
                'new_path': str(new_path),
            })
        else:
            mp3_path.rename(new_path)
            print(f"  RENAMED FILE: '{mp3_path.name}' -> '{new_name}'")
            log.append({
                'type': 'file',
                'status': 'renamed',
                'previous_path': str(mp3_path),
                'new_path': str(new_path),
            })


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
    """Check if a file already has a reasonably complete set of tags."""
    ext = Path(filepath).suffix.lower()
    try:
        if ext == '.mp3':
            tags = ID3(filepath)
            required = ['TIT2', 'TPE1', 'TALB', 'TRCK', 'TDRC']
            return all(tags.getall(frame) for frame in required)
        elif ext == '.flac':
            from mutagen.flac import FLAC
            audio = FLAC(filepath)
            required = ['title', 'artist', 'album', 'tracknumber', 'date']
            return all(audio.get(tag) for tag in required)
        elif ext in ('.m4a', '.mp4', '.aac'):
            from mutagen.mp4 import MP4
            audio = MP4(filepath)
            required = ['\xa9nam', '\xa9ART', '\xa9alb', 'trkn', '\xa9day']
            return all(audio.tags and audio.tags.get(tag) for tag in required)
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


def process_album(artist_name: str, album_dir: Path, genre_override: str | None,
                  dry_run: bool, skip_art: bool, rename: bool, strip_comments: bool,
                  log: list, skip_tagged: bool = False, keep_art: bool = False) -> int:
    """Process all audio files in an album directory. Returns count of files processed."""
    folder_name = album_dir.name
    year, album_name = parse_album_folder(folder_name)

    mp3_files = find_audio_files(album_dir)
    if not mp3_files:
        return 0

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing: {artist_name} - {album_name} ({year or 'unknown year'})")
    print(f"  Found {len(mp3_files)} audio file(s)")

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

        # Use the MusicBrainz album title (canonical spelling/casing)
        mb_album_name = release.get('title', album_name)

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

    # Rename album folder to [YEAR] Album Name format
    if rename and year:
        album_dir = rename_album_folder(album_dir, year, mb_album_name, dry_run, log)
        # Re-scan files after potential rename
        if not dry_run:
            mp3_files = find_audio_files(album_dir)

    # Rename track files to "NN - Title.ext" format
    if rename and track_map:
        rename_track_files(album_dir, track_map, dry_run, log)
        # Re-scan after potential renames
        if not dry_run:
            mp3_files = find_audio_files(album_dir)

    count = 0
    skipped_tagged = 0
    for mp3_path in mp3_files:
        # Skip already-tagged files if requested
        if skip_tagged and has_complete_tags(str(mp3_path)):
            skipped_tagged += 1
            continue

        file_track_num, file_title = parse_track_filename(mp3_path.name)

        # Try to match to MusicBrainz track data — use disc subfolder to infer disc number
        track_info = None
        inferred_disc = None
        disc_match = re.match(r'(?:cd|disc|disk)\s*(\d+)', mp3_path.parent.name, re.IGNORECASE)
        if disc_match:
            inferred_disc = int(disc_match.group(1))

        if track_map and file_track_num:
            if inferred_disc:
                track_info = track_map.get((inferred_disc, file_track_num))
            if not track_info:
                track_info = track_map.get((1, file_track_num))
            if not track_info:
                for key, val in track_map.items():
                    if key[1] == file_track_num:
                        track_info = val
                        break

        # Determine cover art for this file
        file_cover_art = cover_art
        if keep_art and cover_art:
            # Preserve existing art if the file already has embedded art
            if _file_has_cover_art(str(mp3_path)):
                file_cover_art = None

        changes = apply_tags(
            filepath=str(mp3_path),
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
            'previous_path': str(mp3_path),
            'new_path': str(mp3_path),
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
                     confirm: bool = False):
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
                         skip_tagged=skip_tagged, keep_art=keep_art, confirm=False)
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

        # If this artist folder itself contains audio files (flat structure), skip with warning
        direct_audio = [f for f in artist_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS]
        if direct_audio and not album_dirs:
            print(f"\n  WARNING: Audio files found directly in '{artist_name}/' — skipping.")
            print(f"           Expected: {artist_name}/[YEAR] Album Name/track.mp3")
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
                                  skip_tagged=skip_tagged, keep_art=keep_art)
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
    parser.add_argument('--output', type=str, default=None, metavar='FILE',
                        help='Write a CSV report of all changes (previous paths, '
                             'new paths, skipped files)')

    args = parser.parse_args()
    scan_and_process(
        args.directory, args.genre, args.dry_run, args.no_art,
        rename=args.rename, strip_comments=args.strip_comments,
        output_file=args.output, filter_str=args.filter_str,
        skip_tagged=args.skip_tagged, keep_art=args.keep_art,
        confirm=args.confirm,
    )


if __name__ == '__main__':
    main()
