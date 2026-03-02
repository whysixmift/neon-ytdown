import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


APP_TITLE = "Youtube Downloader"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ytdown_gui_config.json")
MAX_HISTORY = 25
PROGRESS_PATTERN = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")


class YoutubeDownloaderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("920x700")
        self.root.minsize(860, 620)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.is_downloading = False
        self.is_previewing = False
        self.is_maintaining_dependency = False

        self.playlist_cache: dict[str, list[dict[str, str]]] = {}
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
            text="Fast yt-dlp GUI with playlist preview, history, and dependency tools",
            style="Subtle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        input_card = ttk.LabelFrame(main, text="Input", style="Card.TLabelframe", padding=12)
        input_card.pack(fill="x", pady=(12, 0))

        ttk.Label(input_card, text="Paste YouTube link(s), one per line:").pack(anchor="w")
        self.links_text = scrolledtext.ScrolledText(input_card, height=8, wrap="word")
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

        ttk.Label(options, text="Format:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.format_var = tk.StringVar(value="mp4")
        ttk.Combobox(
            options,
            textvariable=self.format_var,
            values=["mp4", "mp3"],
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="w", padx=(0, 16))

        ttk.Label(options, text="Max items:").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.count_var = tk.StringVar(value="1")
        ttk.Spinbox(options, from_=1, to=9999, textvariable=self.count_var, width=8).grid(
            row=0,
            column=3,
            sticky="w",
        )

        ttk.Label(options, text="Output folder:").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        default_output = os.path.join(os.path.expanduser("~"), "Downloads", "Youtube Downloader")
        self.output_var = tk.StringVar(value=default_output)
        ttk.Entry(options, textvariable=self.output_var).grid(
            row=1,
            column=1,
            columnspan=3,
            sticky="ew",
            pady=(10, 0),
        )
        ttk.Button(options, text="Browse", command=self.pick_output_folder).grid(
            row=1,
            column=4,
            sticky="w",
            padx=(8, 0),
            pady=(10, 0),
        )

        self.preview_first_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="Preview playlist first (optional)",
            variable=self.preview_first_var,
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(12, 0))
        options.columnconfigure(3, weight=1)

        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(12, 0))
        self.download_btn = ttk.Button(actions, text="Download", command=self.start_download)
        self.download_btn.pack(side="left")
        self.preview_btn = ttk.Button(actions, text="Preview Playlist", command=self.start_preview)
        self.preview_btn.pack(side="left", padx=(8, 0))
        self.clear_btn = ttk.Button(actions, text="Clear Log", command=self.clear_log)
        self.clear_btn.pack(side="left", padx=(8, 0))

        self.check_dep_btn = ttk.Button(actions, text="Check yt-dlp", command=self._check_yt_dlp_status)
        self.check_dep_btn.pack(side="right")
        self.update_dep_btn = ttk.Button(actions, text="Update yt-dlp", command=self._update_yt_dlp)
        self.update_dep_btn.pack(side="right", padx=(8, 0))
        self.install_dep_btn = ttk.Button(actions, text="Install yt-dlp", command=self._install_yt_dlp)
        self.install_dep_btn.pack(side="right", padx=(8, 0))

        status_card = ttk.Frame(main, style="Card.TFrame", padding=10)
        status_card.pack(fill="x", pady=(12, 0))
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(status_card, variable=self.progress_var, maximum=100).pack(fill="x")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status_card, textvariable=self.status_var, style="Subtle.TLabel").pack(anchor="w", pady=(6, 0))

        log_card = ttk.LabelFrame(main, text="Log", style="Card.TLabelframe", padding=10)
        log_card.pack(fill="both", expand=True, pady=(12, 0))
        self.log_text = scrolledtext.ScrolledText(log_card, height=14, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

    def _setup_styles(self) -> None:
        self.root.configure(bg="#f5f7fb")
        style = ttk.Style()
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Main.TFrame", background="#f5f7fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabelframe", background="#ffffff")
        style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#1d3557")
        style.configure("Title.TLabel", font=("Helvetica", 20, "bold"), foreground="#0b2545", background="#ffffff")
        style.configure("Subtle.TLabel", foreground="#5a6b81", background="#ffffff")
        style.configure("TButton", padding=7)

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
        self.format_var.set(str(self.config.get("format", "mp4")))
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
            "format": self.format_var.get().strip(),
            "count": self.count_var.get().strip(),
            "output_dir": self.output_var.get().strip(),
            "preview_first": self.preview_first_var.get(),
            "recent_links": self.recent_links[:MAX_HISTORY],
            "last_links": self.links_text.get("1.0", tk.END).strip(),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError:
            self._log("Warning: gagal menyimpan config lokal.")

    def _on_close(self) -> None:
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

    def insert_history_link(self) -> None:
        selected = self.history_var.get().strip()
        if not selected:
            return
        current = self.links_text.get("1.0", tk.END).strip()
        if current:
            self.links_text.insert(tk.END, f"\n{selected}")
        else:
            self.links_text.insert("1.0", selected)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message)
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _drain_log_queue(self) -> None:
        while not self.log_queue.empty():
            self._append_log(self.log_queue.get_nowait())
        self.root.after(120, self._drain_log_queue)

    def _log(self, message: str) -> None:
        self.log_queue.put(message + "\n")

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

        self.playlist_cache[link] = entries
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

    def start_download(self) -> None:
        if self.is_downloading or self.is_previewing or self.is_maintaining_dependency:
            messagebox.showinfo(APP_TITLE, "Another task is running.")
            return

        links = self._get_clean_links()
        if not links:
            messagebox.showwarning(APP_TITLE, "Masukkan minimal 1 link YouTube.")
            return

        try:
            requested_count = int(self.count_var.get())
            if requested_count < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_TITLE, "Jumlah video/lagu harus angka >= 1.")
            return

        selected_format = self.format_var.get().strip().lower()
        if selected_format not in {"mp4", "mp3"}:
            messagebox.showwarning(APP_TITLE, "Format harus mp4 atau mp3.")
            return

        output_dir = self.output_var.get().strip()
        if not output_dir:
            messagebox.showwarning(APP_TITLE, "Folder output tidak boleh kosong.")
            return

        if self.preview_first_var.get() and len(links) == 1 and links[0] not in self.playlist_selection:
            should_preview = messagebox.askyesno(
                APP_TITLE,
                "Preview mode aktif. Belum ada selection untuk link ini. Preview sekarang?",
            )
            if should_preview:
                self.start_preview()
                return

        self._begin_download(links, requested_count, selected_format, output_dir)

    def _begin_download(
        self,
        links: list[str],
        requested_count: int,
        selected_format: str,
        output_dir: str,
    ) -> None:
        os.makedirs(output_dir, exist_ok=True)
        self._remember_links(links)
        self._save_config()

        self.is_downloading = True
        self._set_busy_state(True)
        self._set_progress(0)
        self._set_status("Download started")

        worker = threading.Thread(
            target=self._run_download,
            args=(links, requested_count, selected_format, output_dir),
            daemon=True,
        )
        worker.start()

    def _run_download(self, links: list[str], requested_count: int, selected_format: str, output_dir: str) -> None:
        try:
            self._log(f"Format: {selected_format.upper()}")
            self._log(f"Jumlah diminta: {requested_count}")
            self._log(f"Output: {output_dir}")
            self._log("-" * 60)

            if len(links) == 1:
                selected_items = self.playlist_selection.get(links[0])
                self._download_single(links[0], requested_count, selected_format, output_dir, selected_items)
            else:
                chosen_links = links[:requested_count]
                if requested_count > len(links):
                    self._log(f"Hanya ada {len(links)} link. Akan download semua link yang tersedia.")
                for idx, link in enumerate(chosen_links, start=1):
                    self._set_status(f"Downloading link {idx}/{len(chosen_links)}")
                    self._set_progress(0)
                    self._log(f"[{idx}/{len(chosen_links)}] {link}")
                    self._download_single(link, None, selected_format, output_dir, None)

            self._set_progress(100)
            self._log("-" * 60)
            self._log("Selesai.")
            self._set_status("Download complete")
        except Exception as exc:  # pylint: disable=broad-except
            self._log(f"Error: {exc}")
            self._set_status(f"Download failed: {exc}")
        finally:
            self.root.after(0, self._finish_download)

    def _finish_download(self) -> None:
        self.is_downloading = False
        self._set_busy_state(False)

    def _download_single(
        self,
        link: str,
        playlist_end: int | None,
        selected_format: str,
        output_dir: str,
        playlist_items: list[int] | None,
    ) -> None:
        output_template = os.path.join(output_dir, "%(title)s.%(ext)s")

        cmd = self._resolve_yt_dlp_command()
        if not cmd:
            raise RuntimeError("yt-dlp not found. Install it first.")

        if selected_format == "mp3":
            cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            cmd += ["-f", "bv*+ba/b", "--merge-output-format", "mp4"]

        cmd += ["-o", output_template]
        if playlist_items:
            cmd += ["--playlist-items", ",".join(str(i) for i in sorted(set(playlist_items)))]
        elif playlist_end is not None:
            cmd += ["--playlist-end", str(playlist_end)]
        cmd.append(link)

        self._log("Command: " + " ".join(cmd))
        self._log("")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        assert process.stdout is not None
        for line in process.stdout:
            clean = line.rstrip()
            self._log(clean)
            match = PROGRESS_PATTERN.search(clean)
            if match:
                try:
                    percent = float(match.group(1))
                    self._set_progress(percent)
                    self._set_status(f"Downloading... {percent:.1f}%")
                except ValueError:
                    pass

        exit_code = process.wait()
        if exit_code != 0:
            raise RuntimeError(f"yt-dlp gagal (exit code {exit_code})")


def main() -> None:
    root = tk.Tk()
    YoutubeDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
