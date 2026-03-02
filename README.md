# neon-ytdown

[![CI](https://github.com/whysixmift/neon-ytdown/actions/workflows/ci.yml/badge.svg)](https://github.com/whysixmift/neon-ytdown/actions/workflows/ci.yml)

A modern desktop YouTube downloader GUI powered by `yt-dlp`.

Built with Python + Tkinter, with a queue-first workflow, optional playlist preview, and practical download automation.

## Features

- Queue-based downloader with task states:
  - `pending`, `scheduled`, `downloading`, `done`, `failed`, `canceled`
- Queue controls:
  - Add to queue, start queue, cancel current, retry failed, remove finished
- Optional playlist preview + selection before downloading
- Quality presets:
  - `Best`, `1080p`, `720p`, `Audio only`
- Metadata options:
  - Subtitle download (`--sub-langs` configurable)
  - Thumbnail download
- Auto file naming templates:
  - Title
  - Uploader - Title
  - Date - Title
  - PlaylistIndex - Title
- Optional scheduling (`YYYY-MM-DD HH:MM`)
- Post-download actions:
  - Open output folder, notify, shutdown
- Built-in `yt-dlp` tools:
  - Check, install, update
- Live progress, speed, ETA, and rolling logs
- Persistent local settings + recent link history

## Keyboard Shortcuts

- `Ctrl+Enter`: Quick Download
- `Ctrl+Shift+A`: Add to Queue
- `F5`: Start Queue
- `Ctrl+L`: Clear Log

## Requirements

- Python 3.10+
- `tkinter` (usually bundled with Python, install separately on some Linux distros)

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Run

```bash
python3 youtube_downloader_gui.py
```

## Usage

1. Paste one or more YouTube links.
2. Choose quality preset, output folder, and optional metadata settings.
3. Optional: click **Preview Playlist** and choose entries.
4. Click **Add to Queue** (or **Quick Download**).
5. Click **Start Queue**.

For scheduled downloads:

1. Set `Schedule at` using `YYYY-MM-DD HH:MM`.
2. Add tasks to queue.
3. Start queue (tasks wait until scheduled time).

## Project Structure

```text
neon-ytdown/
├── .github/
│   └── workflows/
│       └── ci.yml
├── requirements.txt
├── youtube_downloader_gui.py
├── README.md
└── .gitignore
```

## Development

Local syntax check:

```bash
python3 -m py_compile youtube_downloader_gui.py
```

CI workflow:

- GitHub Actions runs on push/PR to `main`
- Matrix: Python `3.10`, `3.11`, `3.12`
- Installs `requirements.txt`
- Runs `py_compile`

## Notes

- This app is a GUI wrapper around `yt-dlp`.
- Respect YouTube Terms of Service and copyright laws in your region.

## Author

Made by `@avrjulian.ino`
