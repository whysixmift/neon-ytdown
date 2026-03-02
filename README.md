# ytdown

A modern desktop GUI for downloading YouTube content with `yt-dlp`.

Built with Python + Tkinter, focused on speed, simple workflows, and a clean user experience.

## Highlights

- Download as `mp4` or `mp3`
- Multiple links support (one link per line)
- Optional playlist preview before download
- Select specific playlist items to download
- Real-time progress + live logs
- Persistent settings and recent-link history
- Built-in `yt-dlp` dependency tools:
  - Check version
  - Install `yt-dlp`
  - Update `yt-dlp`

## UI Preview

Current interface includes:

- Input panel for YouTube links
- Format and output controls
- Playlist preview mode toggle
- Dependency management actions
- Progress bar + status line + live log

(You can add screenshots later in a `screenshots/` folder.)

## Quick Start

### 1. Requirements

- Python 3.10+ (3.12 recommended)
- `tkinter` available in your Python installation

### 2. Install `yt-dlp`

You can use the in-app **Install yt-dlp** button, or install manually:

```bash
python3 -m pip install -U yt-dlp
```

### 3. Run

```bash
python3 youtube_downloader_gui.py
```

## How To Use

1. Paste one or more YouTube links.
2. Choose format (`mp4` or `mp3`).
3. Choose max items and output folder.
4. Optional: enable **Preview playlist first**.
5. Click **Download**.

For playlist selection:

1. Click **Preview Playlist**.
2. Select entries you want.
3. Click **Use Selection**.
4. Start download.

## Project Structure

```text
ytdown/
├── youtube_downloader_gui.py
├── README.md
└── .gitignore
```

## Development

### Lint/Type (optional)

```bash
python3 -m py_compile youtube_downloader_gui.py
```

### Roadmap

- Cancel/pause active download
- Download queue manager
- Better metadata handling (thumbnail, tags)
- Packaged binary builds (AppImage / Windows exe)

## Notes

- This app is a GUI wrapper around `yt-dlp`.
- Respect YouTube Terms of Service and copyright laws in your region.

## Author

Made by `@avrjulian.ino`
