import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk


APP_TITLE = "Youtube Downloader"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ytdown_gui_config.json")
LOG_PATH = os.path.join(os.path.expanduser("~"), ".ytdown_gui.log")
MAX_HISTORY = 25
MAX_LOG_LINES = 3500
PROGRESS_PATTERN = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
PROGRESS_DETAIL_PATTERN = re.compile(r"\[download\]\s+\d+(?:\.\d+)?%.*? at (?P<speed>\S+)\s+ETA (?P<eta>[0-9:]+)")

MEDIA_TYPE_OPTIONS = ["Video", "Audio only"]
VIDEO_QUALITY_OPTIONS = ["Best", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
AUDIO_QUALITY_OPTIONS = ["Best", "320k", "256k", "192k", "128k"]
AUDIO_QUALITY_MAP = {
    "Best": "0",
    "320k": "320K",
    "256k": "256K",
    "192k": "192K",
    "128k": "128K",
}

FILENAME_TEMPLATES = {
    "Title": "%(title)s.%(ext)s",
    "Uploader - Title": "%(uploader)s - %(title)s.%(ext)s",
    "Date - Title": "%(upload_date)s - %(title)s.%(ext)s",
    "PlaylistIndex - Title": "%(playlist_index)s - %(title)s.%(ext)s",
}

POST_ACTIONS = ["None", "Open output folder", "Notify", "Shutdown"]


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window: tk.Toplevel | None = None
        self.widget.bind("<Enter>", self._show)
        self.widget.bind("<Leave>", self._hide)

    def _show(self, _event: tk.Event) -> None:
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            background="#1f2d3d",
            foreground="#f8fbff",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=4,
            font=("Helvetica", 9),
        )
        label.pack()

    def _hide(self, _event: tk.Event) -> None:
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


class YoutubeDownloaderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1080x820")
        self.root.minsize(980, 700)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.log_line_count = 0

        self.is_downloading = False
        self.is_previewing = False
        self.is_maintaining_dependency = False

        self.current_process: subprocess.Popen[str] | None = None
        self.cancel_event = threading.Event()
        self.state_lock = threading.Lock()

        self.task_counter = 0
        self.download_tasks: list[dict] = []
        self.log_file_lock = threading.Lock()

        self.playlist_selection: dict[str, list[int]] = {}
        self.recent_links: list[str] = []

        self.config = self._load_config()
        self._build_ui()
        self._apply_loaded_config()

        self.root.after(120, self._drain_log_queue)
        self.root.after(200, self._check_yt_dlp_status)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self._setup_styles()

        main = ttk.Frame(self.root, style="Main.TFrame", padding=16)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main, style="Card.TFrame", padding=(14, 12))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Queue-based yt-dlp GUI with preview, scheduler, and richer download controls",
            style="Subtle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            header,
            text=(
                "Shortcuts: Ctrl+Enter quick download | Ctrl+Shift+A add queue | "
                "F5 start queue | Ctrl+L clear log | Ctrl+Shift+L view saved log"
            ),
            style="Subtle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        input_card = ttk.LabelFrame(main, text="Input", style="Card.TLabelframe", padding=12)
        input_card.pack(fill="x", pady=(12, 0))

        ttk.Label(input_card, text="Paste YouTube link(s), one per line:").pack(anchor="w")
        self.links_text = scrolledtext.ScrolledText(input_card, height=6, wrap="word")
        self.links_text.pack(fill="x", pady=(6, 10))

        recent_row = ttk.Frame(input_card)
        recent_row.pack(fill="x")
        ttk.Label(recent_row, text="Recent link:").pack(side="left", padx=(0, 8))
        self.history_var = tk.StringVar(value="")
        self.history_combo = ttk.Combobox(recent_row, textvariable=self.history_var, state="readonly")
        self.history_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(recent_row, text="Insert", command=self.insert_history_link).pack(side="left", padx=(8, 0))

        options = ttk.LabelFrame(main, text="Options", style="Card.TLabelframe", padding=12)
        options.pack(fill="x", pady=(12, 0))

        ttk.Label(options, text="Content type:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.media_type_var = tk.StringVar(value="Video")
        ttk.Combobox(
            options,
            textvariable=self.media_type_var,
            values=MEDIA_TYPE_OPTIONS,
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w", padx=(0, 16))

        ttk.Label(options, text="Video quality:").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.video_quality_var = tk.StringVar(value="Best")
        ttk.Combobox(
            options,
            textvariable=self.video_quality_var,
            values=VIDEO_QUALITY_OPTIONS,
            state="readonly",
            width=10,
        ).grid(row=0, column=3, sticky="w", padx=(0, 16))

        ttk.Label(options, text="Audio quality:").grid(row=0, column=4, sticky="w", padx=(0, 8))
        self.audio_quality_var = tk.StringVar(value="Best")
        ttk.Combobox(
            options,
            textvariable=self.audio_quality_var,
            values=AUDIO_QUALITY_OPTIONS,
            state="readonly",
            width=10,
        ).grid(row=0, column=5, sticky="w")

        ttk.Label(options, text="Max items:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        self.count_var = tk.StringVar(value="1")
        ttk.Spinbox(options, from_=1, to=9999, textvariable=self.count_var, width=8).grid(
            row=1,
            column=1,
            sticky="w",
            padx=(0, 16),
            pady=(10, 0),
        )

        ttk.Label(options, text="Name template:").grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(10, 0))
        self.filename_template_label_var = tk.StringVar(value="Title")
        ttk.Combobox(
            options,
            textvariable=self.filename_template_label_var,
            values=list(FILENAME_TEMPLATES.keys()),
            state="readonly",
            width=22,
        ).grid(row=1, column=3, columnspan=3, sticky="w", pady=(10, 0))

        ttk.Label(options, text="Output folder:").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        default_output = os.path.join(os.path.expanduser("~"), "Downloads", "Youtube Downloader")
        self.output_var = tk.StringVar(value=default_output)
        ttk.Entry(options, textvariable=self.output_var).grid(
            row=2,
            column=1,
            columnspan=4,
            sticky="ew",
            pady=(10, 0),
        )
        ttk.Button(options, text="Browse", command=self.pick_output_folder).grid(
            row=2,
            column=5,
            sticky="w",
            padx=(8, 0),
            pady=(10, 0),
        )

        self.preview_first_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="Preview playlist first (optional)",
            variable=self.preview_first_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))

        self.subtitles_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="Download subtitles",
            variable=self.subtitles_var,
        ).grid(row=3, column=2, columnspan=2, sticky="w", pady=(12, 0))

        self.thumbnail_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="Download thumbnail",
            variable=self.thumbnail_var,
        ).grid(row=3, column=4, columnspan=2, sticky="w", pady=(12, 0))

        ttk.Label(options, text="Subtitle langs:").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        self.sub_langs_var = tk.StringVar(value="en.*,id.*")
        ttk.Entry(options, textvariable=self.sub_langs_var, width=20).grid(
            row=4,
            column=1,
            sticky="w",
            pady=(10, 0),
        )

        ttk.Label(options, text="Schedule at (optional):").grid(row=4, column=2, sticky="w", padx=(0, 8), pady=(10, 0))
        self.schedule_var = tk.StringVar(value="")
        ttk.Entry(options, textvariable=self.schedule_var, width=20).grid(
            row=4,
            column=3,
            sticky="w",
            pady=(10, 0),
        )
        ttk.Label(options, text="YYYY-MM-DD HH:MM").grid(row=4, column=4, sticky="w", pady=(10, 0))

        ttk.Label(options, text="After queue done:").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        self.post_action_var = tk.StringVar(value="None")
        ttk.Combobox(
            options,
            textvariable=self.post_action_var,
            values=POST_ACTIONS,
            state="readonly",
            width=20,
        ).grid(row=5, column=1, sticky="w", pady=(10, 0))

        for idx in (1, 3, 4):
            options.columnconfigure(idx, weight=1)

        queue_card = ttk.LabelFrame(main, text="Queue", style="Card.TLabelframe", padding=10)
        queue_card.pack(fill="both", expand=True, pady=(12, 0))

        self.queue_tree = ttk.Treeview(
            queue_card,
            columns=("id", "status", "quality", "schedule", "link"),
            show="headings",
            height=8,
        )
        self.queue_tree.heading("id", text="#")
        self.queue_tree.heading("status", text="Status")
        self.queue_tree.heading("quality", text="Quality")
        self.queue_tree.heading("schedule", text="Schedule")
        self.queue_tree.heading("link", text="Link")
        self.queue_tree.column("id", width=48, anchor="center", stretch=False)
        self.queue_tree.column("status", width=110, anchor="center", stretch=False)
        self.queue_tree.column("quality", width=190, anchor="center", stretch=False)
        self.queue_tree.column("schedule", width=150, anchor="center", stretch=False)
        self.queue_tree.column("link", width=600, anchor="w")
        self.queue_tree.tag_configure("pending", background="#f8fbff")
        self.queue_tree.tag_configure("scheduled", background="#fff8e8")
        self.queue_tree.tag_configure("downloading", background="#e8f4ff")
        self.queue_tree.tag_configure("done", background="#e9fbe8")
        self.queue_tree.tag_configure("failed", background="#ffe9e9")
        self.queue_tree.tag_configure("canceled", background="#f3f3f3")
        self.queue_tree.pack(fill="both", expand=True)

        queue_buttons = ttk.Frame(queue_card)
        queue_buttons.pack(fill="x", pady=(10, 0))
        self.add_queue_btn = ttk.Button(queue_buttons, text="Add to Queue", command=self.add_to_queue)
        self.add_queue_btn.pack(side="left")
        self.start_queue_btn = ttk.Button(queue_buttons, text="Start Queue", command=self.start_queue)
        self.start_queue_btn.pack(side="left", padx=(8, 0))
        self.preview_btn = ttk.Button(queue_buttons, text="Preview Playlist", command=self.start_preview)
        self.preview_btn.pack(side="left", padx=(8, 0))
        self.cancel_btn = ttk.Button(queue_buttons, text="Cancel Current", command=self.cancel_current_download)
        self.cancel_btn.pack(side="left", padx=(8, 0))
        self.retry_btn = ttk.Button(queue_buttons, text="Retry Failed", command=self.retry_failed)
        self.retry_btn.pack(side="left", padx=(8, 0))
        self.remove_finished_btn = ttk.Button(queue_buttons, text="Remove Finished", command=self.remove_finished)
        self.remove_finished_btn.pack(side="left", padx=(8, 0))

        dep_buttons = ttk.Frame(queue_buttons)
        dep_buttons.pack(side="right")
        self.check_dep_btn = ttk.Button(dep_buttons, text="Check yt-dlp", command=self._check_yt_dlp_status)
        self.check_dep_btn.pack(side="right")
        self.update_dep_btn = ttk.Button(dep_buttons, text="Update yt-dlp", command=self._update_yt_dlp)
        self.update_dep_btn.pack(side="right", padx=(8, 0))
        self.install_dep_btn = ttk.Button(dep_buttons, text="Install yt-dlp", command=self._install_yt_dlp)
        self.install_dep_btn.pack(side="right", padx=(8, 0))

        summary_row = ttk.Frame(queue_card)
        summary_row.pack(fill="x", pady=(8, 0))
        self.queue_summary_var = tk.StringVar(value="Queue: total 0 | pending 0 | running 0 | done 0 | failed 0")
        ttk.Label(summary_row, textvariable=self.queue_summary_var, style="Subtle.TLabel").pack(side="left")

        status_card = ttk.Frame(main, style="Card.TFrame", padding=10)
        status_card.pack(fill="x", pady=(12, 0))
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(status_card, variable=self.progress_var, maximum=100).pack(fill="x")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status_card, textvariable=self.status_var, style="Subtle.TLabel").pack(anchor="w", pady=(6, 0))
        self.progress_info_var = tk.StringVar(value="")
        ttk.Label(status_card, textvariable=self.progress_info_var, style="Subtle.TLabel").pack(anchor="w", pady=(2, 0))

        log_card = ttk.LabelFrame(main, text="Log", style="Card.TLabelframe", padding=10)
        log_card.pack(fill="both", expand=True, pady=(12, 0))
        self.log_text = scrolledtext.ScrolledText(log_card, height=12, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

        footer_buttons = ttk.Frame(main)
        footer_buttons.pack(fill="x", pady=(8, 0))
        self.download_btn = ttk.Button(footer_buttons, text="Quick Download", command=self.quick_download)
        self.download_btn.pack(side="left")
        self.clear_btn = ttk.Button(footer_buttons, text="Clear Log", command=self.clear_log)
        self.clear_btn.pack(side="left", padx=(8, 0))
        self.view_log_btn = ttk.Button(footer_buttons, text="View Saved Log", command=self.view_saved_log)
        self.view_log_btn.pack(side="left", padx=(8, 0))
        self.cancel_btn.configure(state="disabled")

        self._refresh_queue_summary()
        self._bind_shortcuts()
        self._install_tooltips()

    def _setup_styles(self) -> None:
        self.root.configure(bg="#f4f6fb")
        style = ttk.Style()
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Main.TFrame", background="#f4f6fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabelframe", background="#ffffff")
        style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#16324f")
        style.configure("Title.TLabel", font=("Helvetica", 20, "bold"), foreground="#0b2545", background="#ffffff")
        style.configure("Subtle.TLabel", foreground="#4f6179", background="#ffffff")
        style.configure("TButton", padding=6, font=("Helvetica", 10))

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-Return>", lambda _e: self.quick_download())
        self.root.bind("<Control-Shift-A>", lambda _e: self.add_to_queue())
        self.root.bind("<F5>", lambda _e: self.start_queue())
        self.root.bind("<Control-l>", lambda _e: self.clear_log())
        self.root.bind("<Control-Shift-L>", lambda _e: self.view_saved_log())

    def _install_tooltips(self) -> None:
        ToolTip(self.add_queue_btn, "Add current links and options as queued tasks")
        ToolTip(self.start_queue_btn, "Start processing pending/scheduled tasks")
        ToolTip(self.cancel_btn, "Cancel the currently running download")
        ToolTip(self.retry_btn, "Move failed/canceled tasks back to pending")
        ToolTip(self.preview_btn, "Preview playlist entries for first link and select items")
        ToolTip(self.download_btn, "Add to queue and start immediately")
        ToolTip(self.view_log_btn, "Open persisted log viewer")

    def _load_config(self) -> dict:
        if not os.path.exists(CONFIG_PATH):
            return {}

        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _apply_loaded_config(self) -> None:
        self.count_var.set(str(self.config.get("count", "1")))
        self.output_var.set(
            str(
                self.config.get(
                    "output_dir",
                    os.path.join(os.path.expanduser("~"), "Downloads", "Youtube Downloader"),
                )
            )
        )
        self.preview_first_var.set(bool(self.config.get("preview_first", False)))
        legacy_quality = str(self.config.get("quality", "Best"))
        media_type = str(self.config.get("media_type", "Video"))
        if "audio" in legacy_quality.lower():
            media_type = "Audio only"
        if media_type not in MEDIA_TYPE_OPTIONS:
            media_type = "Video"
        self.media_type_var.set(media_type)
        self.video_quality_var.set(str(self.config.get("video_quality", self._legacy_video_quality(legacy_quality))))
        self.audio_quality_var.set(str(self.config.get("audio_quality", "Best")))
        self.filename_template_label_var.set(str(self.config.get("filename_template_label", "Title")))
        self.subtitles_var.set(bool(self.config.get("download_subtitles", False)))
        self.thumbnail_var.set(bool(self.config.get("download_thumbnail", False)))
        self.sub_langs_var.set(str(self.config.get("sub_langs", "en.*,id.*")))
        self.schedule_var.set(str(self.config.get("schedule", "")))
        self.post_action_var.set(str(self.config.get("post_action", "None")))

        links_from_config = self.config.get("recent_links", [])
        if isinstance(links_from_config, list):
            self.recent_links = [str(item).strip() for item in links_from_config if str(item).strip()]
        self.recent_links = self.recent_links[:MAX_HISTORY]
        self._refresh_history_dropdown()

        initial_links = self.config.get("last_links", "")
        if isinstance(initial_links, str) and initial_links.strip():
            self.links_text.insert("1.0", initial_links)

    def _save_config(self) -> None:
        payload = {
            "count": self.count_var.get().strip(),
            "output_dir": self.output_var.get().strip(),
            "preview_first": self.preview_first_var.get(),
            "media_type": self.media_type_var.get().strip(),
            "video_quality": self.video_quality_var.get().strip(),
            "audio_quality": self.audio_quality_var.get().strip(),
            "filename_template_label": self.filename_template_label_var.get().strip(),
            "download_subtitles": self.subtitles_var.get(),
            "download_thumbnail": self.thumbnail_var.get(),
            "sub_langs": self.sub_langs_var.get().strip(),
            "schedule": self.schedule_var.get().strip(),
            "post_action": self.post_action_var.get().strip(),
            "recent_links": self.recent_links[:MAX_HISTORY],
            "last_links": self.links_text.get("1.0", tk.END).strip(),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError:
            self._log("Warning: gagal menyimpan config lokal.")

    def _on_close(self) -> None:
        self.cancel_event.set()
        self._terminate_current_process()
        self._save_config()
        self.root.destroy()

    def pick_output_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.output_var.get().strip() or os.path.expanduser("~"))
        if chosen:
            self.output_var.set(chosen)

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")
        self.log_line_count = 0
        self.progress_info_var.set("")

    def view_saved_log(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Saved Log")
        dialog.geometry("920x520")
        dialog.minsize(700, 380)
        dialog.transient(self.root)

        container = ttk.Frame(dialog, padding=12)
        container.pack(fill="both", expand=True)

        path_var = tk.StringVar(value=f"File: {LOG_PATH}")
        ttk.Label(container, textvariable=path_var, style="Subtle.TLabel").pack(anchor="w", pady=(0, 8))

        text = scrolledtext.ScrolledText(container, wrap="word")
        text.pack(fill="both", expand=True)

        def load_log() -> None:
            try:
                with open(LOG_PATH, "r", encoding="utf-8") as f:
                    data = f.read()
            except FileNotFoundError:
                data = "(No saved log yet)"
            except OSError as exc:
                data = f"Failed to read log: {exc}"

            text.delete("1.0", tk.END)
            text.insert("1.0", data)
            text.see(tk.END)

        button_row = ttk.Frame(container)
        button_row.pack(fill="x", pady=(8, 0))
        ttk.Button(button_row, text="Refresh", command=load_log).pack(side="left")
        ttk.Button(button_row, text="Close", command=dialog.destroy).pack(side="right")

        load_log()

    def insert_history_link(self) -> None:
        selected = self.history_var.get().strip()
        if not selected:
            return
        current = self.links_text.get("1.0", tk.END).strip()
        if current:
            self.links_text.insert(tk.END, f"\n{selected}")
        else:
            self.links_text.insert("1.0", selected)

    def _append_log_batch(self, messages: list[str]) -> None:
        if not messages:
            return

        chunk = "".join(messages)
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, chunk)
        self.log_line_count += chunk.count("\n")

        if self.log_line_count > MAX_LOG_LINES:
            delete_lines = self.log_line_count - MAX_LOG_LINES
            self.log_text.delete("1.0", f"{delete_lines + 1}.0")
            self.log_line_count = MAX_LOG_LINES

        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        pending: list[str] = []
        while True:
            try:
                pending.append(self.log_queue.get_nowait())
            except queue.Empty:
                break

        if pending:
            self._append_log_batch(pending)

        self.root.after(120, self._drain_log_queue)

    def _log(self, message: str) -> None:
        line = message + "\n"
        self.log_queue.put(line)
        self._write_log_file(message)

    def _write_log_file(self, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {message}\n"
        try:
            with self.log_file_lock:
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(line)
        except OSError:
            pass

    def _legacy_video_quality(self, legacy_quality: str) -> str:
        normalized = legacy_quality.strip()
        if normalized in VIDEO_QUALITY_OPTIONS:
            return normalized
        if normalized == "Best":
            return "Best"
        if normalized == "1080p":
            return "1080p"
        if normalized == "720p":
            return "720p"
        return "Best"

    def _build_quality_label(self, media_type: str, video_quality: str, audio_quality: str) -> str:
        if media_type == "Audio only":
            return f"Audio ({audio_quality})"
        return f"Video ({video_quality})"

    def _set_status(self, message: str) -> None:
        self.root.after(0, self.status_var.set, message)

    def _set_progress(self, value: float) -> None:
        value = max(0.0, min(100.0, value))
        self.root.after(0, self.progress_var.set, value)

    def _get_clean_links(self) -> list[str]:
        raw = self.links_text.get("1.0", tk.END)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _remember_links(self, links: list[str]) -> None:
        for link in links:
            if link in self.recent_links:
                self.recent_links.remove(link)
            self.recent_links.insert(0, link)
        self.recent_links = self.recent_links[:MAX_HISTORY]
        self._refresh_history_dropdown()

    def _refresh_history_dropdown(self) -> None:
        self.history_combo["values"] = self.recent_links
        if self.recent_links and not self.history_var.get().strip():
            self.history_var.set(self.recent_links[0])

    def _set_busy_state(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.download_btn.configure(state=state)
        self.preview_btn.configure(state=state)
        self.add_queue_btn.configure(state=state)
        self.start_queue_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def _set_dependency_buttons_state(self, state: str) -> None:
        self.install_dep_btn.configure(state=state)
        self.update_dep_btn.configure(state=state)
        self.check_dep_btn.configure(state=state)

    def _resolve_yt_dlp_command(self) -> list[str] | None:
        if shutil.which("yt-dlp"):
            return ["yt-dlp"]
        if shutil.which("pipx"):
            return ["pipx", "run", "yt-dlp"]
        return None

    def _check_yt_dlp_status(self) -> None:
        if self.is_maintaining_dependency:
            return

        def worker() -> None:
            cmd = self._resolve_yt_dlp_command()
            if not cmd:
                self._set_status("yt-dlp not found. Use Install yt-dlp.")
                return

            process = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
            if process.returncode == 0:
                version = process.stdout.strip().splitlines()[0] if process.stdout.strip() else "unknown"
                self._set_status(f"yt-dlp ready (version: {version})")
            else:
                self._set_status("yt-dlp command exists but failed to run.")

        threading.Thread(target=worker, daemon=True).start()

    def _run_dependency_command(self, cmd: list[str], title: str) -> None:
        if self.is_downloading or self.is_previewing or self.is_maintaining_dependency:
            messagebox.showinfo(APP_TITLE, "Please wait for current task to finish.")
            return

        def worker() -> None:
            self.is_maintaining_dependency = True
            self.root.after(0, lambda: self._set_dependency_buttons_state("disabled"))
            self._set_status(title)
            self._log(f"\n[{title}] {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                self._log(line.rstrip())

            code = process.wait()
            if code == 0:
                self._set_status(f"{title} done")
                self._log(f"[{title}] success")
            else:
                self._set_status(f"{title} failed (exit code {code})")
                self._log(f"[{title}] failed (exit code {code})")

            self.is_maintaining_dependency = False
            self.root.after(0, lambda: self._set_dependency_buttons_state("normal"))
            self._check_yt_dlp_status()

        threading.Thread(target=worker, daemon=True).start()

    def _install_yt_dlp(self) -> None:
        if shutil.which("pipx"):
            cmd = ["pipx", "install", "yt-dlp"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
        self._run_dependency_command(cmd, "Install yt-dlp")

    def _update_yt_dlp(self) -> None:
        if shutil.which("yt-dlp"):
            cmd = ["yt-dlp", "-U"]
        elif shutil.which("pipx"):
            cmd = ["pipx", "upgrade", "yt-dlp"]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
        self._run_dependency_command(cmd, "Update yt-dlp")

    def _parse_schedule_time(self) -> datetime | None:
        raw = self.schedule_var.get().strip()
        if not raw:
            return None
        try:
            scheduled = datetime.strptime(raw, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("Schedule format must be YYYY-MM-DD HH:MM") from exc

        if scheduled <= datetime.now():
            raise ValueError("Schedule must be in the future")
        return scheduled

    def _collect_batch_tasks(self) -> list[dict]:
        links = self._get_clean_links()
        if not links:
            raise ValueError("Masukkan minimal 1 link YouTube.")

        try:
            requested_count = int(self.count_var.get())
            if requested_count < 1:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Jumlah video/lagu harus angka >= 1.") from exc

        media_type = self.media_type_var.get().strip()
        video_quality = self.video_quality_var.get().strip()
        audio_quality = self.audio_quality_var.get().strip()
        if media_type not in MEDIA_TYPE_OPTIONS:
            raise ValueError("Content type tidak valid.")
        if video_quality not in VIDEO_QUALITY_OPTIONS:
            raise ValueError("Video quality tidak valid.")
        if audio_quality not in AUDIO_QUALITY_OPTIONS:
            raise ValueError("Audio quality tidak valid.")

        output_dir = self.output_var.get().strip()
        if not output_dir:
            raise ValueError("Folder output tidak boleh kosong.")

        schedule_at = self._parse_schedule_time()
        template_label = self.filename_template_label_var.get().strip()
        template = FILENAME_TEMPLATES.get(template_label)
        if not template:
            raise ValueError("Template nama file tidak valid.")

        if self.preview_first_var.get() and len(links) == 1 and links[0] not in self.playlist_selection:
            raise ValueError("Preview mode aktif, lakukan Preview Playlist dulu untuk link ini.")

        selected_links = links[:requested_count]
        if len(links) > 1 and requested_count > len(links):
            self._log(f"Hanya ada {len(links)} link. Akan memakai semua link yang tersedia.")

        tasks: list[dict] = []
        for link in selected_links:
            task: dict = {
                "id": self._next_task_id(),
                "url": link,
                "status": "scheduled" if schedule_at else "pending",
                "media_type": media_type,
                "video_quality": video_quality,
                "audio_quality": audio_quality,
                "quality_label": self._build_quality_label(media_type, video_quality, audio_quality),
                "output_dir": output_dir,
                "filename_template": template,
                "filename_template_label": template_label,
                "download_subtitles": self.subtitles_var.get(),
                "download_thumbnail": self.thumbnail_var.get(),
                "sub_langs": self.sub_langs_var.get().strip() or "en.*",
                "schedule_at": schedule_at,
                "playlist_items": self.playlist_selection.get(link),
                "tries": 0,
                "error": "",
            }
            tasks.append(task)

        self._remember_links(selected_links)
        self._save_config()
        return tasks

    def _next_task_id(self) -> int:
        self.task_counter += 1
        return self.task_counter

    def _queue_schedule_label(self, task: dict) -> str:
        schedule_at = task.get("schedule_at")
        if isinstance(schedule_at, datetime):
            return schedule_at.strftime("%Y-%m-%d %H:%M")
        return "now"

    def _add_task_to_ui(self, task: dict) -> None:
        self.queue_tree.insert(
            "",
            "end",
            iid=str(task["id"]),
            values=(
                task["id"],
                task["status"],
                task["quality_label"],
                self._queue_schedule_label(task),
                task["url"],
            ),
            tags=(task["status"],),
        )
        self._refresh_queue_summary()

    def _update_task_ui(self, task: dict) -> None:
        iid = str(task["id"])
        if self.queue_tree.exists(iid):
            self.queue_tree.item(
                iid,
                values=(
                    task["id"],
                    task["status"],
                    task["quality_label"],
                    self._queue_schedule_label(task),
                    task["url"],
                ),
                tags=(task["status"],),
            )
        self._refresh_queue_summary()

    def _set_task_status(self, task: dict, status: str, error: str = "") -> None:
        task["status"] = status
        task["error"] = error
        self.root.after(0, lambda: self._update_task_ui(task))

    def _refresh_queue_summary(self) -> None:
        total = len(self.download_tasks)
        pending = 0
        running = 0
        done = 0
        failed = 0
        for task in self.download_tasks:
            status = task.get("status")
            if status in {"pending", "scheduled"}:
                pending += 1
            elif status == "downloading":
                running += 1
            elif status == "done":
                done += 1
            elif status in {"failed", "canceled"}:
                failed += 1
        self.queue_summary_var.set(
            f"Queue: total {total} | pending {pending} | running {running} | done {done} | failed {failed}"
        )

    def add_to_queue(self) -> None:
        if self.is_previewing or self.is_maintaining_dependency:
            messagebox.showinfo(APP_TITLE, "Another task is running.")
            return

        try:
            tasks = self._collect_batch_tasks()
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return

        self.download_tasks.extend(tasks)
        for task in tasks:
            self._add_task_to_ui(task)

        self._set_status(f"Added {len(tasks)} task(s) to queue")
        self._log(f"Added {len(tasks)} task(s) to queue")

    def quick_download(self) -> None:
        self.add_to_queue()
        self.start_queue()

    def start_queue(self) -> None:
        if self.is_downloading or self.is_previewing or self.is_maintaining_dependency:
            return

        if not any(task["status"] in {"pending", "scheduled"} for task in self.download_tasks):
            messagebox.showinfo(APP_TITLE, "No pending tasks in queue.")
            return

        self.cancel_event.clear()
        self.is_downloading = True
        self._set_busy_state(True)
        self._set_progress(0)
        self._set_status("Queue started")
        threading.Thread(target=self._run_queue_worker, daemon=True).start()

    def cancel_current_download(self) -> None:
        self.cancel_event.set()
        self._terminate_current_process()
        self._set_status("Cancelling current download...")

    def _terminate_current_process(self) -> None:
        with self.state_lock:
            proc = self.current_process

        if proc is None:
            return

        try:
            proc.terminate()
        except OSError:
            pass

    def retry_failed(self) -> None:
        changed = 0
        for task in self.download_tasks:
            if task["status"] in {"failed", "canceled"}:
                task["status"] = "pending"
                task["error"] = ""
                changed += 1
                self._update_task_ui(task)

        if changed == 0:
            messagebox.showinfo(APP_TITLE, "No failed/canceled task to retry.")
            return

        self._set_status(f"Retried {changed} task(s)")
        self._log(f"Retried {changed} task(s)")

    def remove_finished(self) -> None:
        kept: list[dict] = []
        removed = 0
        for task in self.download_tasks:
            if task["status"] in {"done", "failed", "canceled"}:
                iid = str(task["id"])
                if self.queue_tree.exists(iid):
                    self.queue_tree.delete(iid)
                removed += 1
            else:
                kept.append(task)

        self.download_tasks = kept
        self._set_status(f"Removed {removed} finished task(s)")
        self._refresh_queue_summary()

    def _wait_for_schedule(self, task: dict) -> bool:
        schedule_at = task.get("schedule_at")
        if not isinstance(schedule_at, datetime):
            return True

        while True:
            if self.cancel_event.is_set():
                return False
            now = datetime.now()
            if now >= schedule_at:
                task["schedule_at"] = None
                self._set_task_status(task, "pending")
                return True

            wait_seconds = int((schedule_at - now).total_seconds())
            self._set_task_status(task, "scheduled")
            self._set_status(f"Waiting schedule for task #{task['id']} ({wait_seconds}s left)")
            time.sleep(min(1, max(0.1, wait_seconds)))

    def _run_queue_worker(self) -> None:
        try:
            self._log("=" * 72)
            self._log("Queue processing started")
            was_canceled = False

            for task in self.download_tasks:
                if task["status"] not in {"pending", "scheduled"}:
                    continue

                if not self._wait_for_schedule(task):
                    self._set_task_status(task, "canceled", "Canceled before scheduled time")
                    was_canceled = True
                    continue

                if self.cancel_event.is_set():
                    self._set_task_status(task, "canceled", "Canceled by user")
                    was_canceled = True
                    continue

                task["tries"] += 1
                self._set_task_status(task, "downloading")
                self._set_status(f"Downloading task #{task['id']}")
                self._set_progress(0)

                self._log("-" * 72)
                self._log(f"Task #{task['id']} ({task['quality_label']}): {task['url']}")

                try:
                    self._download_task(task)
                    if self.cancel_event.is_set():
                        self._set_task_status(task, "canceled", "Canceled by user")
                        was_canceled = True
                    else:
                        self._set_task_status(task, "done")
                except Exception as exc:  # pylint: disable=broad-except
                    if self.cancel_event.is_set():
                        self._set_task_status(task, "canceled", "Canceled by user")
                        was_canceled = True
                    else:
                        self._set_task_status(task, "failed", str(exc))
                        self._log(f"Task #{task['id']} failed: {exc}")

            self._set_progress(100)
            self._log("Queue processing finished")
            if not was_canceled:
                self._run_post_action()
        finally:
            self.cancel_event.clear()
            self.root.after(0, self._finish_download)

    def _finish_download(self) -> None:
        self.is_downloading = False
        self._set_busy_state(False)
        self._set_status("Queue idle")
        self.progress_info_var.set("")

    def _build_download_command(self, task: dict) -> list[str]:
        cmd = self._resolve_yt_dlp_command()
        if not cmd:
            raise RuntimeError("yt-dlp not found. Install it first.")

        media_type = task["media_type"]
        if media_type == "Audio only":
            cmd += ["-x", "--audio-format", "mp3", "--audio-quality", AUDIO_QUALITY_MAP[task["audio_quality"]]]
        else:
            video_quality = task["video_quality"]
            if video_quality == "Best":
                format_selector = "bv*+ba/b"
            else:
                max_height = video_quality.replace("p", "")
                format_selector = f"bv*[height<={max_height}]+ba/b[height<={max_height}]"
            cmd += ["-f", format_selector, "--merge-output-format", "mp4"]

        output_template = os.path.join(task["output_dir"], task["filename_template"])
        cmd += ["-o", output_template]

        if task.get("playlist_items"):
            items = sorted(set(task["playlist_items"]))
            cmd += ["--playlist-items", ",".join(str(i) for i in items)]

        if task["download_subtitles"]:
            cmd += ["--write-subs", "--sub-langs", task["sub_langs"]]
            if media_type != "Audio only":
                cmd += ["--embed-subs"]

        if task["download_thumbnail"]:
            cmd += ["--write-thumbnail", "--convert-thumbnails", "jpg"]

        cmd.append(task["url"])
        return cmd

    def _download_task(self, task: dict) -> None:
        os.makedirs(task["output_dir"], exist_ok=True)
        cmd = self._build_download_command(task)

        self._log("Command: " + " ".join(cmd))
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        with self.state_lock:
            self.current_process = process

        assert process.stdout is not None
        for line in process.stdout:
            clean = line.rstrip()
            self._log(clean)
            match = PROGRESS_PATTERN.search(clean)
            if match:
                try:
                    percent = float(match.group(1))
                    self._set_progress(percent)
                    self._set_status(f"Task #{task['id']} downloading... {percent:.1f}%")
                except ValueError:
                    pass
            detail = PROGRESS_DETAIL_PATTERN.search(clean)
            if detail:
                speed = detail.group("speed")
                eta = detail.group("eta")
                self.root.after(0, self.progress_info_var.set, f"Speed: {speed} | ETA: {eta}")

            if self.cancel_event.is_set():
                self._terminate_current_process()

        exit_code = process.wait()

        with self.state_lock:
            self.current_process = None

        if self.cancel_event.is_set():
            return

        if exit_code != 0:
            raise RuntimeError(f"yt-dlp gagal (exit code {exit_code})")

    def _run_post_action(self) -> None:
        action = self.post_action_var.get().strip()
        if action == "None":
            return

        if action == "Open output folder":
            output_dir = self.output_var.get().strip()
            if output_dir:
                self.root.after(0, lambda: self._open_output_folder(output_dir))
            return

        if action == "Notify":
            self.root.after(0, lambda: messagebox.showinfo(APP_TITLE, "Queue finished."))
            self.root.after(0, self.root.bell)
            return

        if action == "Shutdown":
            self._log("Post action: scheduling system shutdown in 1 minute")
            try:
                if sys.platform.startswith("win"):
                    subprocess.Popen(["shutdown", "/s", "/t", "60"])
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["shutdown", "-h", "+1"])
                elif sys.platform == "darwin":
                    subprocess.Popen(["shutdown", "-h", "+1"])
            except OSError as exc:
                self._log(f"Shutdown action failed: {exc}")

    def _open_output_folder(self, path: str) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            self._log(f"Cannot open folder: {exc}")

    def start_preview(self) -> None:
        if self.is_downloading or self.is_previewing:
            messagebox.showinfo(APP_TITLE, "Another task is running.")
            return

        links = self._get_clean_links()
        if not links:
            messagebox.showwarning(APP_TITLE, "Paste at least one YouTube link first.")
            return

        link = links[0]
        self.is_previewing = True
        self._set_busy_state(True)
        self._set_status("Fetching playlist preview...")

        def worker() -> None:
            try:
                entries = self._fetch_playlist_entries(link)
                self.root.after(0, lambda: self._open_preview_dialog(link, entries))
            except Exception as exc:  # pylint: disable=broad-except
                self.root.after(0, lambda: messagebox.showerror(APP_TITLE, f"Preview gagal: {exc}"))
                self._set_status("Preview failed")
            finally:
                self.is_previewing = False
                self.root.after(0, lambda: self._set_busy_state(False))

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_playlist_entries(self, link: str) -> list[dict[str, str]]:
        cmd = self._resolve_yt_dlp_command()
        if not cmd:
            raise RuntimeError("yt-dlp not found. Install it first.")

        process = subprocess.run(
            cmd + ["--flat-playlist", "--dump-single-json", link],
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            raise RuntimeError(process.stdout.strip() or "unknown yt-dlp error")

        try:
            data = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid preview response") from exc

        entries_raw = data.get("entries")
        entries: list[dict[str, str]] = []
        if isinstance(entries_raw, list) and entries_raw:
            for idx, item in enumerate(entries_raw, start=1):
                title = "Untitled"
                if isinstance(item, dict):
                    title = str(item.get("title") or item.get("id") or f"Item {idx}")
                entries.append({"index": str(idx), "title": title})
        else:
            title = str(data.get("title") or link)
            entries.append({"index": "1", "title": title})

        return entries

    def _open_preview_dialog(self, link: str, entries: list[dict[str, str]]) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Playlist Preview")
        dialog.geometry("760x520")
        dialog.minsize(660, 420)
        dialog.transient(self.root)
        dialog.grab_set()

        container = ttk.Frame(dialog, padding=12)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="Select items to download:", font=("Helvetica", 11, "bold")).pack(anchor="w")
        ttk.Label(container, text=link, foreground="#4b5d73").pack(anchor="w", pady=(2, 8))

        tree = ttk.Treeview(container, columns=("idx", "title"), show="headings", selectmode="extended")
        tree.heading("idx", text="#")
        tree.heading("title", text="Title")
        tree.column("idx", width=60, anchor="center", stretch=False)
        tree.column("title", anchor="w", width=620)
        tree.pack(fill="both", expand=True)

        for row in entries:
            iid = row["index"]
            tree.insert("", "end", iid=iid, values=(row["index"], row["title"]))

        all_ids = [row["index"] for row in entries]
        tree.selection_set(all_ids)

        button_row = ttk.Frame(container)
        button_row.pack(fill="x", pady=(10, 0))

        def select_all() -> None:
            tree.selection_set(all_ids)

        def clear_all() -> None:
            tree.selection_set(())

        def apply_selection() -> None:
            selected = sorted(int(iid) for iid in tree.selection())
            if not selected:
                messagebox.showwarning(APP_TITLE, "Select at least one item.")
                return
            self.playlist_selection[link] = selected
            self._set_status(f"Playlist selection saved ({len(selected)} item)")
            self._log(f"Preview selection for {link}: {selected}")
            dialog.destroy()

        ttk.Button(button_row, text="Select All", command=select_all).pack(side="left")
        ttk.Button(button_row, text="Clear", command=clear_all).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Use Selection", command=apply_selection).pack(side="right")


def main() -> None:
    root = tk.Tk()
    YoutubeDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
