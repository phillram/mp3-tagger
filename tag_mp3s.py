#!/usr/bin/env python3
"""
MP3 Metadata Tagger
===================
Scans a folder structure of Artist/[YEAR] Album/track.mp3 files,
looks up metadata from MusicBrainz + Cover Art Archive, and writes
ID3v2.4 tags to each file.

Usage:
    python3 tag_mp3s.py /path/to/music
    python3 tag_mp3s.py /path/to/music --dry-run
    python3 tag_mp3s.py /path/to/music --no-art
    python3 tag_mp3s.py /path/to/music --genre "Rock"   # override genre

Requirements:
    pip3 install mutagen musicbrainzngs
"""

import argparse
import os
import re
import shutil
import sys
import time
import json
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

def _rate_limit():
    global _last_mb_request
    now = time.time()
    elapsed = now - _last_mb_request
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _last_mb_request = time.time()


def parse_album_folder(folder_name: str) -> tuple[str | None, str | None]:
    """Parse '[YEAR] Album Name' folder format. Returns (year, album_name)."""
    # Match [2024] Album Name or (2024) Album Name
    m = re.match(r'[\[\(](\d{4})[\]\)]\s+(.+)', folder_name)
    if m:
        return m.group(1), m.group(2).strip()

    # Also try: 2024 - Album Name
    m = re.match(r'(\d{4})\s*[-–]\s*(.+)', folder_name)
    if m:
        return m.group(1), m.group(2).strip()

    # Also try: 2024 Album Name (year at start)
    m = re.match(r'(\d{4})\s+(.+)', folder_name)
    if m:
        return m.group(1), m.group(2).strip()

    return None, folder_name


def is_correct_album_format(folder_name: str) -> bool:
    """Check if folder name already matches [YEAR] Album Name format."""
    return bool(re.match(r'^\[\d{4}\]\s+.+', folder_name))


def build_album_folder_name(year: str, album_name: str) -> str:
    """Build the canonical [YEAR] Album Name folder name."""
    return f"[{year}] {album_name}"


def rename_album_folder(album_dir: Path, year: str, album_name: str, dry_run: bool) -> Path:
    """Rename an album folder to [YEAR] Album Name format. Returns the new path."""
    correct_name = build_album_folder_name(year, album_name)
    if album_dir.name == correct_name:
        return album_dir

    new_path = album_dir.parent / correct_name

    # Handle collision
    if new_path.exists() and new_path != album_dir:
        print(f"  WARNING: Cannot rename '{album_dir.name}' -> '{correct_name}' (target already exists)")
        return album_dir

    if dry_run:
        print(f"  WOULD RENAME: '{album_dir.name}' -> '{correct_name}'")
        return album_dir
    else:
        album_dir.rename(new_path)
        print(f"  RENAMED: '{album_dir.name}' -> '{correct_name}'")
        return new_path


def rename_track_files(album_dir: Path, track_map: dict, dry_run: bool):
    """Rename track files to 'NN - Track Title.mp3' format using MusicBrainz data."""
    mp3_files = sorted(album_dir.glob('*.mp3'), key=lambda p: p.name)
    for mp3_path in mp3_files:
        file_track_num, file_title = parse_track_filename(mp3_path.name)
        if not file_track_num or not track_map:
            continue

        # Find matching track info
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
        # Sanitize title for filesystem
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', mb_title)
        new_name = f"{track_num:02d} - {safe_title}.mp3"

        if mp3_path.name == new_name:
            continue

        new_path = album_dir / new_name
        if new_path.exists() and new_path != mp3_path:
            print(f"  WARNING: Cannot rename '{mp3_path.name}' -> '{new_name}' (target exists)")
            continue

        if dry_run:
            print(f"  WOULD RENAME FILE: '{mp3_path.name}' -> '{new_name}'")
        else:
            mp3_path.rename(new_path)
            print(f"  RENAMED FILE: '{mp3_path.name}' -> '{new_name}'")


def parse_track_filename(filename: str) -> tuple[int | None, str]:
    """Extract track number and title from filename. Returns (track_num, title)."""
    name = Path(filename).stem

    # Try: 01 - Track Name, 01. Track Name, 01 Track Name
    m = re.match(r'^(\d{1,3})\s*[-–.]\s*(.+)', name)
    if m:
        return int(m.group(1)), m.group(2).strip()

    m = re.match(r'^(\d{1,3})\s+(.+)', name)
    if m:
        return int(m.group(1)), m.group(2).strip()

    return None, name


def search_release(artist: str, album: str, year: str | None = None) -> dict | None:
    """Search MusicBrainz for a release and return full metadata."""
    _rate_limit()

    query = f'artist:"{artist}" AND release:"{album}"'
    if year:
        query += f' AND date:{year}'

    try:
        results = mb.search_releases(query=query, limit=5)
    except mb.WebServiceError as e:
        print(f"  WARNING: MusicBrainz search failed: {e}")
        return None

    if not results.get('release-list'):
        # Try without year
        if year:
            _rate_limit()
            query = f'artist:"{artist}" AND release:"{album}"'
            try:
                results = mb.search_releases(query=query, limit=5)
            except mb.WebServiceError:
                return None

    releases = results.get('release-list', [])
    if not releases:
        return None

    # Pick the best match (highest score)
    release = releases[0]
    release_id = release['id']

    # Fetch full release details including recordings
    _rate_limit()
    try:
        full = mb.get_release_by_id(
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
            _rate_limit()
            try:
                rg_full = mb.get_release_group_by_id(rg_id, includes=['tags'])
                rg_data = rg_full.get('release-group', rg_full)
                tags = rg_data.get('tag-list', [])
                if tags:
                    # Sort by count, pick top tag as genre
                    tags.sort(key=lambda t: int(t.get('count', 0)), reverse=True)
                    info['genre'] = tags[0]['name'].title()
            except mb.WebServiceError:
                pass
    return info


def fetch_cover_art(release_id: str) -> bytes | None:
    """Fetch front cover art from the Cover Art Archive."""
    url = f"https://coverartarchive.org/release/{release_id}/front-500"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MP3Tagger/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        # Try the release group instead
        pass
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
) -> dict:
    """Apply ID3v2.4 tags to an MP3 file. Returns a summary of changes."""
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

    if dry_run:
        return changes

    # Load or create ID3 tags
    try:
        tags = ID3(filepath)
    except ID3NoHeaderError:
        tags = ID3()

    # Song title
    tags.delall('TIT2')
    tags.add(TIT2(encoding=3, text=[title]))

    # Artist
    tags.delall('TPE1')
    tags.add(TPE1(encoding=3, text=[artist]))

    # Album artist
    tags.delall('TPE2')
    tags.add(TPE2(encoding=3, text=[artist]))

    # Album
    tags.delall('TALB')
    tags.add(TALB(encoding=3, text=[album]))

    # Track number
    if track_num:
        tags.delall('TRCK')
        track_str = f"{track_num}/{total_tracks}" if total_tracks else str(track_num)
        tags.add(TRCK(encoding=3, text=[track_str]))

    # Disc number
    tags.delall('TPOS')
    disc_str = f"{disc_num}/{total_discs}" if total_discs else str(disc_num)
    tags.add(TPOS(encoding=3, text=[disc_str]))

    # Release date/year
    if year:
        tags.delall('TDRC')
        tags.add(TDRC(encoding=3, text=[year]))

    # Genre
    if genre:
        tags.delall('TCON')
        tags.add(TCON(encoding=3, text=[genre]))

    # Label/publisher
    if label:
        tags.delall('TPUB')
        tags.add(TPUB(encoding=3, text=[label]))

    # Album cover art
    if cover_art:
        # Remove existing APIC frames
        tags.delall('APIC')
        tags.add(APIC(
            encoding=3,
            mime='image/jpeg',
            type=3,  # Cover (front)
            desc='Front Cover',
            data=cover_art,
        ))

    tags.save(filepath, v2_version=4)
    return changes


def process_album(artist_name: str, album_dir: Path, genre_override: str | None,
                  dry_run: bool, skip_art: bool, rename: bool) -> int:
    """Process all MP3s in an album directory. Returns count of files processed."""
    folder_name = album_dir.name
    year, album_name = parse_album_folder(folder_name)

    mp3_files = sorted(album_dir.glob('*.mp3'), key=lambda p: p.name)
    if not mp3_files:
        return 0

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing: {artist_name} - {album_name} ({year or 'unknown year'})")
    print(f"  Found {len(mp3_files)} MP3 file(s)")

    # Look up on MusicBrainz
    release = search_release(artist_name, album_name, year)
    track_map = {}
    genre = genre_override
    label = None
    cover_art = None

    if release:
        print(f"  MusicBrainz match: {release.get('title', '?')} (id: {release.get('id', '?')[:8]}...)")
        track_map = build_track_list(release)

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
        album_dir = rename_album_folder(album_dir, year, mb_album_name, dry_run)
        # Re-scan mp3 files after potential rename
        if not dry_run:
            mp3_files = sorted(album_dir.glob('*.mp3'), key=lambda p: p.name)

    # Rename track files to "NN - Title.mp3" format
    if rename and track_map:
        rename_track_files(album_dir, track_map, dry_run)
        # Re-scan after potential renames
        if not dry_run:
            mp3_files = sorted(album_dir.glob('*.mp3'), key=lambda p: p.name)

    count = 0
    for mp3_path in mp3_files:
        file_track_num, file_title = parse_track_filename(mp3_path.name)

        # Try to match to MusicBrainz track data
        track_info = None
        if track_map and file_track_num:
            # Try disc 1 first, then search all discs
            track_info = track_map.get((1, file_track_num))
            if not track_info:
                for key, val in track_map.items():
                    if key[1] == file_track_num:
                        track_info = val
                        break

        changes = apply_tags(
            filepath=str(mp3_path),
            artist=artist_name,
            album=mb_album_name,
            year=year,
            track_info=track_info,
            genre=genre,
            label=label,
            cover_art=cover_art,
            dry_run=dry_run,
        )

        status = "WOULD TAG" if dry_run else "TAGGED"
        title = changes.get('title', '?')
        track = changes.get('track', '?')
        art_indicator = " [+art]" if changes.get('has_cover') else ""
        genre_str = f" [{changes.get('genre')}]" if changes.get('genre') else ""
        print(f"  {status}: {track} - {title}{genre_str}{art_indicator}")
        count += 1

    return count


def scan_and_process(root: str, genre_override: str | None, dry_run: bool, skip_art: bool,
                     rename: bool = False):
    """Scan the root music directory and process all artist/album folders."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        print(f"ERROR: '{root}' is not a directory")
        sys.exit(1)

    print(f"Scanning: {root_path}")
    if dry_run:
        print("DRY RUN MODE — no files will be modified\n")

    total = 0
    artist_dirs = sorted([d for d in root_path.iterdir() if d.is_dir()])

    if not artist_dirs:
        print("No artist directories found.")
        return

    # Check if the root itself looks like an artist folder (contains album folders with MP3s)
    # by checking if any immediate subdirectories have MP3s
    has_mp3_in_subdirs = False
    for d in artist_dirs:
        if list(d.glob('*.mp3')):
            has_mp3_in_subdirs = True
            break

    if has_mp3_in_subdirs:
        # Root might be an artist folder — treat it as Artist/albums
        print(f"NOTE: It looks like '{root_path.name}' might be an artist folder.")
        print("       Expected structure: ArtistName/[Year] Album/tracks.mp3")
        print("       If results look wrong, pass the parent directory instead.\n")

    for artist_dir in artist_dirs:
        if not artist_dir.is_dir():
            continue

        artist_name = artist_dir.name
        album_dirs = sorted([d for d in artist_dir.iterdir() if d.is_dir()])

        # If this artist folder itself contains mp3s (flat structure), treat it as a single album
        direct_mp3s = list(artist_dir.glob('*.mp3'))
        if direct_mp3s and not album_dirs:
            print(f"\n  WARNING: MP3s found directly in '{artist_name}/' — skipping.")
            print(f"           Expected: {artist_name}/[YEAR] Album Name/track.mp3")
            continue

        for album_dir in album_dirs:
            if not album_dir.is_dir():
                continue
            count = process_album(artist_name, album_dir, genre_override, dry_run, skip_art, rename)
            total += count

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Done! Processed {total} file(s).")


def main():
    parser = argparse.ArgumentParser(
        description='Tag MP3 files with metadata from MusicBrainz',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Expected folder structure:
  /music-root/
    Artist Name/
      [2024] Album Name/
        01 - Track One.mp3
        02 - Track Two.mp3
      [2020] Another Album/
        01. First Song.mp3

Examples:
  %(prog)s /path/to/music --dry-run       # preview changes
  %(prog)s /path/to/music                 # apply tags
  %(prog)s /path/to/music --genre Rock    # force genre
  %(prog)s /path/to/music --no-art        # skip album art
  %(prog)s /path/to/music --rename        # also fix folder/file names
  %(prog)s /path/to/music --rename --dry-run  # preview renames
        """
    )
    parser.add_argument('directory', help='Root music directory to scan')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without modifying files')
    parser.add_argument('--genre', type=str, default=None,
                        help='Override genre for all albums (e.g., "Rock", "Hip-Hop")')
    parser.add_argument('--no-art', action='store_true',
                        help='Skip fetching album cover art')
    parser.add_argument('--rename', action='store_true',
                        help='Rename album folders to [YEAR] Album Name format and '
                             'track files to "NN - Title.mp3" using MusicBrainz data')

    args = parser.parse_args()
    scan_and_process(args.directory, args.genre, args.dry_run, args.no_art, args.rename)


if __name__ == '__main__':
    main()
