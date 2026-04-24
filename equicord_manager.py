"""
Equicord Manager Pro — GUI helper to clone, build, inject, and copy userplugins.
Requires: git, Node.js, pnpm, customtkinter (pip install customtkinter).

Run: python equicord_manager.py
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

EQUICORD_REPO = "https://github.com/Equicord/Equicord.git"

# Equilotl CLI (pnpm inject) — see: EquilotlCli.exe --help
_VALID_INJECT_BRANCHES = frozenset({"auto", "stable", "canary", "ptb"})


def _normalize_inject_branch(branch: str) -> str:
    b = (branch or "auto").strip().lower()
    return b if b in _VALID_INJECT_BRANCHES else "auto"


def _settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "equicord_manager"
    d.mkdir(parents=True, exist_ok=True)
    return d / "settings.json"


def _load_saved_settings() -> tuple[str, str]:
    """Restore (repo_path, inject_branch). Missing or invalid repo on disk → Not Selected."""
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ("Not Selected", "auto")
    repo = data.get("repo_path") or "Not Selected"
    branch = data.get("inject_branch", "auto")
    if not isinstance(branch, str):
        branch = "auto"
    branch = _normalize_inject_branch(branch)
    if repo and repo != "Not Selected":
        repo = os.path.abspath(os.path.normpath(repo))
        pkg = os.path.join(repo, "package.json")
        if not os.path.isdir(repo) or not os.path.isfile(pkg):
            repo = "Not Selected"
    return (repo, branch)


def _persist_manager_settings(repo_path: str, inject_branch: str) -> None:
    try:
        _settings_path().write_text(
            json.dumps(
                {
                    "repo_path": repo_path,
                    "inject_branch": _normalize_inject_branch(inject_branch),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _inject_install_argv(branch: str) -> list[str]:
    """
    argv for Equicord inject (Equilotl). Do not use `pnpm run inject -- -branch …` on Windows —
    pnpm can inject an extra `--`, so runInstaller.mjs parses the wrong argv slice and the
    installer stays interactive. Call node + runInstaller.mjs directly (same as package.json).
    """
    b = _normalize_inject_branch(branch)
    return ["node", "scripts/runInstaller.mjs", "--", "--install", "-branch", b]


def _inject_install_shell_line(branch: str) -> str:
    """One line for cmd/bash scripts (repo cwd must be the Equicord root)."""
    b = _normalize_inject_branch(branch)
    return f"node scripts/runInstaller.mjs -- --install -branch {b}"


def _find_matching_brace(text: str, open_idx: int) -> int:
    """Index of the `}` that closes `{` at open_idx, or -1."""
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _parse_plugin_name_description(src: str) -> tuple[str | None, str | None]:
    """Read `name` and `description` from `export default definePlugin({ ... })`."""
    m = re.search(r"export\s+default\s+definePlugin\s*\(\s*\{", src)
    if not m:
        return None, None
    open_brace = m.end() - 1  # opening `{` of the plugin object
    close_brace = _find_matching_brace(src, open_brace)
    if close_brace == -1:
        return None, None
    block = src[open_brace : close_brace + 1]
    name_m = re.search(r"^\s*name:\s*[\"']([^\"']+)[\"']", block, re.MULTILINE)
    desc_m = re.search(r"^\s*description:\s*[\"']([^\"']+)[\"']", block, re.MULTILINE)
    return (
        name_m.group(1).strip() if name_m else None,
        desc_m.group(1).strip() if desc_m else None,
    )


def _read_userplugin_entry(plugin_dir: str) -> tuple[str, str, str]:
    """
    Returns (folder_name, title, description).
    Title/description come from definePlugin; folder is the directory name.
    """
    folder = os.path.basename(plugin_dir.rstrip(os.sep))
    for fname in ("index.tsx", "index.ts", "index.jsx", "index.js"):
        path = os.path.join(plugin_dir, fname)
        if not os.path.isfile(path):
            continue
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            return folder, folder, "(could not read entry file)"
        title, desc = _parse_plugin_name_description(text)
        if not title:
            title = folder
        if not desc:
            desc = "(no description in definePlugin)"
        return folder, title, desc
    return folder, folder, "(no index.ts / index.tsx)"


def _scan_userplugins(repo_root: str) -> list[tuple[str, str, str]]:
    """Sorted list of (folder, title, description) for src/userplugins."""
    base = os.path.join(repo_root, "src", "userplugins")
    if not os.path.isdir(base):
        return []
    out: list[tuple[str, str, str]] = []
    for name in sorted(os.listdir(base), key=str.lower):
        if name.startswith("."):
            continue
        pd = os.path.join(base, name)
        if os.path.isdir(pd):
            out.append(_read_userplugin_entry(pd))
    return out


def _resolve_cmd(cmd: list[str]) -> list[str]:
    """Resolve the program to a full path so GUI launches (narrow PATH) still find git/pnpm."""
    if not cmd:
        return cmd
    exe = cmd[0]
    if os.path.sep in exe or (sys.platform == "win32" and len(exe) > 1 and exe[1] == ":"):
        return cmd
    resolved = shutil.which(exe)
    if not resolved:
        raise FileNotFoundError(
            f"Cannot find '{exe}' on PATH. Install it, add it to your user PATH, "
            "sign out of Windows or reboot, then try again. "
            "If it works in a terminal but not here, run: python equicord_manager.py from that terminal."
        )
    return [resolved] + cmd[1:]


def _run(cmd: list[str] | str, cwd: str | None = None, shell: bool = False) -> None:
    """Run a subprocess; on Windows hide console window for CLI tools when possible."""
    if isinstance(cmd, list):
        cmd = _resolve_cmd(cmd)
    if cwd is not None and not os.path.isdir(cwd):
        raise FileNotFoundError(
            f"Working directory does not exist or is not a folder:\n{cwd}\n\n"
            "Use Setup → Link Folder and pick the Equicord repo root (folder with package.json)."
        )
    kwargs: dict = {
        "cwd": cwd,
        "check": True,
        "shell": shell,
    }
    if sys.platform == "win32" and not shell and isinstance(cmd, list):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    if isinstance(cmd, str):
        kwargs["shell"] = True
    subprocess.run(cmd, **kwargs)


def _run_streaming(
    cmd: list[str],
    cwd: str | None,
    log_line: Callable[[str], None],
) -> int:
    """Run a command and stream merged stdout/stderr to log_line (one line at a time)."""
    cmd = _resolve_cmd(cmd)
    if cwd is not None and not os.path.isdir(cwd):
        raise FileNotFoundError(
            f"Working directory does not exist or is not a folder:\n{cwd}\n\n"
            "Use Setup → Link Folder and pick the Equicord repo root (folder with package.json)."
        )
    kwargs: dict = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "errors": "replace",
        "bufsize": 1,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    proc = subprocess.Popen(cmd, **kwargs)
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        log_line(line.rstrip("\r\n"))
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
    return proc.returncode


def _which_ok(name: str) -> bool:
    path = shutil.which(name)
    if not path:
        return False
    try:
        _run([path, "--version"], shell=False)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _open_update_terminal(repo: str, inject_branch: str = "auto") -> None:
    """Open a visible terminal so git pull / pnpm run with the user's normal PATH.

    `pnpm build` must run before Equilotl inject. Inject only rewrites Discord's
    `app.asar` stub to `require("<repo>/dist/desktop")`; if dist/desktop is missing
    (fresh clone, or caller never built), Discord crashes at startup with
    'Cannot find module ...dist\\desktop'.
    """
    inject_line = _inject_install_shell_line(inject_branch)

    if sys.platform == "win32":
        # Use cwd= instead of embedding the path in `cd /d "..."` — avoids cmd.exe syntax
        # errors (e.g. trailing backslash before a quote: "C:\path\" breaks parsing).
        repo_path = os.path.abspath(os.path.normpath(repo))
        if not os.path.isdir(repo_path):
            raise FileNotFoundError(f"Not a folder: {repo_path}")
        subprocess.Popen(
            [
                "cmd",
                "/k",
                f"git pull && pnpm install --no-frozen-lockfile && pnpm build && {inject_line}",
            ],
            cwd=repo_path,
            creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
        )
        return

    rq = shlex.quote(repo)
    script = "\n".join(
        [
            "#!/bin/bash",
            "set -e",
            f"cd {rq}",
            "git pull",
            "pnpm install --no-frozen-lockfile",
            "pnpm build",
            inject_line,
            "echo",
            "echo Done.",
            "read -r _",
            "",
        ]
    )
    suffix = ".command" if sys.platform == "darwin" else ".sh"
    fd, path = tempfile.mkstemp(suffix=suffix, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(path, 0o755)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise

    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Terminal", path])
        return

    for term in (
        ["gnome-terminal", "--", "bash", path],
        ["konsole", "-e", "bash", path],
        ["xterm", "-e", "bash", path],
        ["x-terminal-emulator", "-e", "bash", path],
    ):
        try:
            subprocess.Popen(term)
            return
        except OSError:
            continue
    raise RuntimeError(
        "Could not open a terminal. From the repo root run: "
        "git pull && pnpm install --no-frozen-lockfile && pnpm build && "
        "node scripts/runInstaller.mjs -- --install -branch auto"
    )


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class EquicordApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Equicord Manager Pro")
        self.geometry("920x780")
        saved_repo, saved_branch = _load_saved_settings()
        self.repo_path = ctk.StringVar(value=saved_repo)
        # Equilotl: -branch auto skips the interactive Discord picker (see EquilotlCli --help).
        self.target_client = ctk.StringVar(value=saved_branch)
        self.log_text: ctk.CTkTextbox | None = None

        self.repo_path.trace_add("write", self._on_settings_var_changed)
        self.target_client.trace_add("write", self._on_settings_var_changed)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.main_view = ctk.CTkFrame(self, corner_radius=15, fg_color="#2B2D31")
        self.main_view.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")

        self.progress: ctk.CTkProgressBar | None = None
        self.check_dependencies()

    def _on_settings_var_changed(self, *_args: object) -> None:
        _persist_manager_settings(self.repo_path.get(), self.target_client.get())

    def ui(self, fn) -> None:
        """Run Tk UI updates on the main thread."""
        self.after(0, fn)

    def set_progress(self, value: float) -> None:
        def _() -> None:
            if self.progress is not None:
                self.progress.set(value)

        self.ui(_)

    def _append_log(self, line: str) -> None:
        def _() -> None:
            if self.log_text is not None:
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")

        self.ui(_)

    def _clear_log(self) -> None:
        def _() -> None:
            if self.log_text is not None:
                self.log_text.delete("1.0", "end")

        self.ui(_)

    def _add_log_panel(self) -> None:
        """Call after clearing main_view children; creates the mini console at the bottom."""
        log_outer = ctk.CTkFrame(self.main_view, fg_color="transparent")
        log_outer.pack(fill="both", expand=True, pady=(12, 0))

        ctk.CTkLabel(
            log_outer,
            text="Output (live)",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#B5BAC1",
        ).pack(anchor="w", padx=4)

        self.log_text = ctk.CTkTextbox(
            log_outer,
            height=200,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color="#1E1F22",
            text_color="#DCDDDE",
            border_width=1,
            border_color="#3F4147",
        )
        self.log_text.pack(fill="both", expand=True, padx=2, pady=(4, 0))

    def check_dependencies(self) -> None:
        missing: list[tuple[str, str]] = []
        for tool, url in [
            ("git", "https://git-scm.com"),
            ("node", "https://nodejs.org"),
            ("pnpm", "https://pnpm.io"),
        ]:
            if not _which_ok(tool):
                missing.append((tool.capitalize(), url))

        if missing:
            self.show_missing_deps(missing)
        else:
            self.setup_sidebar()
            repo = self.repo_path.get()
            if repo != "Not Selected" and os.path.isfile(os.path.join(repo, "package.json")):
                self.main_ui()
            else:
                self.setup_ui()

    def show_missing_deps(self, missing: list[tuple[str, str]]) -> None:
        for widget in self.main_view.winfo_children():
            widget.destroy()
        self.log_text = None

        ctk.CTkLabel(
            self.main_view,
            text="Requirements missing",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#ed4245",
        ).pack(pady=30)

        for name, url in missing:
            frame = ctk.CTkFrame(self.main_view, fg_color="transparent")
            frame.pack(pady=5)
            ctk.CTkLabel(frame, text=f"• {name}", width=100).pack(side="left")
            ctk.CTkButton(
                frame, text="Download", width=80, height=25, command=lambda u=url: webbrowser.open(u)
            ).pack(side="left", padx=10)

        ctk.CTkButton(self.main_view, text="Refresh", command=self.check_dependencies).pack(pady=30)

    def setup_sidebar(self) -> None:
        for w in self.sidebar.winfo_children():
            w.destroy()

        ctk.CTkLabel(self.sidebar, text="EQUICORD", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=30)
        ctk.CTkButton(self.sidebar, text="Setup", command=self.setup_ui).pack(pady=10, padx=20)
        ctk.CTkButton(self.sidebar, text="Manager", command=self.main_ui).pack(pady=10, padx=20)

    def setup_ui(self) -> None:
        for widget in self.main_view.winfo_children():
            widget.destroy()

        ctk.CTkLabel(self.main_view, text="Repository Setup", font=ctk.CTkFont(size=24, weight="bold")).pack(
            pady=20
        )
        ctk.CTkButton(
            self.main_view, text="Clone New Equicord", width=300, command=self.clone_repo
        ).pack(pady=10)
        ctk.CTkButton(
            self.main_view, text="Link Folder", width=300, fg_color="gray", command=self.browse_existing
        ).pack(pady=10)

        self._add_log_panel()
        self._append_log("Clone progress will appear here. Link Folder first if you already have a repo.")

    def main_ui(self) -> None:
        if self.repo_path.get() == "Not Selected":
            self.setup_ui()
            return

        for widget in self.main_view.winfo_children():
            widget.destroy()

        ctk.CTkLabel(self.main_view, text="Equicord Manager", font=ctk.CTkFont(size=24, weight="bold")).pack(
            pady=20
        )

        picker_frame = ctk.CTkFrame(self.main_view, fg_color="transparent")
        picker_frame.pack(pady=10)
        for client in ["auto", "stable", "canary", "ptb"]:
            label = "AUTO" if client == "auto" else client.upper()
            ctk.CTkRadioButton(
                picker_frame, text=label, variable=self.target_client, value=client
            ).pack(side="left", padx=10)

        self.progress = ctk.CTkProgressBar(self.main_view, width=400)
        self.progress.pack(pady=20)
        self.progress.set(0)

        ctk.CTkButton(
            self.main_view,
            text="Install Custom Plugin(s)",
            width=300,
            height=45,
            fg_color="#23a559",
            command=self.install_plugin,
        ).pack(pady=10)
        ctk.CTkButton(
            self.main_view,
            text="Reinstall / Update (opens Command Prompt)",
            width=300,
            height=45,
            fg_color="#5865F2",
            command=self.open_reinstall_terminal,
        ).pack(pady=10)

        ctk.CTkLabel(
            self.main_view,
            text="Discord target: AUTO = non-interactive inject (Equilotl -branch auto). Or pick Stable / Canary / PTB.\n"
            "Reinstall opens cmd in the repo. Install plugin(s) lets you pick several folders, then one build + inject.",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(pady=10)

        self._populate_userplugins_section()

        self._add_log_panel()

    def _populate_userplugins_section(self) -> None:
        repo = self.repo_path.get()
        hdr = ctk.CTkFrame(self.main_view, fg_color="transparent")
        hdr.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(
            hdr,
            text="Installed userplugins",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left", anchor="w")
        ctk.CTkButton(hdr, text="Refresh", width=88, height=28, command=self.main_ui).pack(side="right")

        plugins = _scan_userplugins(repo)
        if not plugins:
            ctk.CTkLabel(
                self.main_view,
                text="No plugins in src/userplugins yet. Install one above or copy a folder there.",
                font=ctk.CTkFont(size=12),
                text_color="#949BA4",
                wraplength=640,
                justify="left",
            ).pack(anchor="w", pady=(0, 8))
            return

        scroll = ctk.CTkScrollableFrame(self.main_view, height=240, fg_color="#1E1F22", corner_radius=8)
        scroll.pack(fill="both", expand=True, pady=(0, 8))

        for folder, title, desc in plugins:
            card = ctk.CTkFrame(scroll, fg_color="#2B2D31", corner_radius=8)
            card.pack(fill="x", pady=4, padx=4)
            ctk.CTkLabel(
                card,
                text=title,
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w",
                justify="left",
            ).pack(anchor="w", padx=12, pady=(10, 2))
            ctk.CTkLabel(
                card,
                text=f"Folder: {folder}",
                font=ctk.CTkFont(size=11),
                text_color="#949BA4",
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(0, 2))
            ctk.CTkLabel(
                card,
                text=desc,
                font=ctk.CTkFont(size=12),
                text_color="#B5BAC1",
                anchor="w",
                justify="left",
                wraplength=580,
            ).pack(anchor="w", padx=12, pady=(0, 10))

    def run_task(self, task_func) -> None:
        threading.Thread(target=task_func, daemon=True).start()

    def clone_repo(self) -> None:
        """filedialog must run on main thread."""
        target = filedialog.askdirectory()
        if not target:
            return
        full_path = os.path.join(target, "Equicord")
        self.run_task(lambda: self._clone_repo_worker(target, full_path))

    def _clone_repo_worker(self, target: str, full_path: str) -> None:
        try:
            self._clear_log()
            self._append_log("Starting clone…")
            self.set_progress(0.1)
            if os.path.exists(full_path):
                self.ui(
                    lambda: messagebox.showerror(
                        "Error", f"Folder already exists:\n{full_path}\n\nRemove it or use Link Folder."
                    )
                )
                self.set_progress(0)
                return

            self._append_log(f"$ git clone … → {full_path}")
            _run_streaming(["git", "clone", EQUICORD_REPO, full_path], cwd=target, log_line=self._append_log)
            self.set_progress(0.4)
            self._append_log("")
            self._append_log("$ pnpm install --no-frozen-lockfile")
            _run_streaming(["pnpm", "install", "--no-frozen-lockfile"], cwd=full_path, log_line=self._append_log)
            # Build immediately so dist/desktop exists before the user hits Reinstall → inject.
            # Without this, Equilotl patches Discord's app.asar to require a path that does not
            # yet exist and Discord refuses to start.
            self.set_progress(0.6)
            self._append_log("")
            self._append_log("$ pnpm build")
            _run_streaming(["pnpm", "build"], cwd=full_path, log_line=self._append_log)
            self.set_progress(1.0)
            self.repo_path.set(full_path)
            self._append_log("")
            self._append_log("Done.")
            self.ui(lambda: messagebox.showinfo("Success", f"Cloned to:\n{full_path}"))
            self.ui(self.main_ui)
        except subprocess.CalledProcessError as e:
            self.set_progress(0)
            self._append_log(f"\n[Command failed with exit code {e.returncode}]")
            err_msg = f"Clone/install failed (exit {e.returncode}). See Output panel."
            self.ui(lambda: messagebox.showerror("Error", err_msg))
        except Exception as e:
            self.set_progress(0)
            self._append_log(f"\n[Error] {e}")
            err_msg = str(e)
            self.ui(lambda: messagebox.showerror("Error", err_msg))

    def browse_existing(self) -> None:
        path = filedialog.askdirectory()
        if not path:
            return
        pkg = os.path.join(path, "package.json")
        if os.path.isfile(pkg):
            self.repo_path.set(path)
            self.main_ui()
        else:
            messagebox.showerror("Error", "Select the Equicord repo root (folder containing package.json).")

    def open_reinstall_terminal(self) -> None:
        """Do not run inject in-process; open cmd.exe so PATH matches a normal terminal."""
        repo = self.repo_path.get()
        if not repo or repo == "Not Selected" or not os.path.isdir(repo):
            messagebox.showerror(
                "Error",
                "Select a valid Equicord folder first (Setup → Link Folder or Clone).\n"
                "The folder must contain package.json.",
            )
            return
        br = _normalize_inject_branch(self.target_client.get())
        self._append_log(
            f"Opening terminal in:\n{repo}\n→ git pull && pnpm install && pnpm build && "
            f"{_inject_install_shell_line(br)}\n"
        )
        try:
            _open_update_terminal(repo, br)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def install_plugin(self) -> None:
        repo = self.repo_path.get()
        if not repo or repo == "Not Selected" or not os.path.isdir(repo):
            messagebox.showerror(
                "Error",
                "Select a valid Equicord folder first (Setup → Link Folder or Clone).\n"
                "The folder must contain package.json.",
            )
            return

        plugin_srcs: list[str] = []
        while True:
            n = len(plugin_srcs)
            title = "Select plugin folder (contains index.ts or index.tsx)"
            if n:
                title = f"Select another plugin folder — {n} already selected (Cancel to finish)"
            plugin_src = filedialog.askdirectory(title=title, parent=self)
            if not plugin_src:
                break
            plugin_srcs.append(plugin_src)
            if not messagebox.askyesno(
                "Add another plugin?",
                f"Selected:\n{plugin_src}\n\nAdd another plugin folder before building?",
                parent=self,
            ):
                break

        if not plugin_srcs:
            return

        basenames = [os.path.basename(os.path.normpath(p)) for p in plugin_srcs]
        if len(basenames) != len(set(basenames)):
            messagebox.showerror(
                "Duplicate folder names",
                "Two or more selected folders have the same name. "
                "Rename one on disk, or install them in separate runs.",
                parent=self,
            )
            return

        self.run_task(lambda: self._install_plugins_worker(plugin_srcs, repo))

    def _install_plugins_worker(self, plugin_srcs: list[str], repo: str) -> None:
        try:
            if not os.path.isdir(repo) or not os.path.isfile(os.path.join(repo, "package.json")):
                raise FileNotFoundError(
                    "Invalid Equicord repo. Use Setup → Link Folder and select the folder that contains package.json."
                )
            n = len(plugin_srcs)
            self._clear_log()
            for i, plugin_src in enumerate(plugin_srcs):
                name = os.path.basename(os.path.normpath(plugin_src))
                target_dir = os.path.join(repo, "src", "userplugins", name)
                self._append_log(f"[{i + 1}/{n}] Copying plugin to:\n{target_dir}\n")
                # Copy phase uses ~first half of the bar; build/inject the rest.
                self.set_progress((i / max(n, 1)) * 0.45)
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir)
                shutil.copytree(plugin_src, target_dir)
                self.set_progress(((i + 1) / max(n, 1)) * 0.45)

            self.set_progress(0.5)
            self._append_log("$ pnpm build")
            _run_streaming(["pnpm", "build"], cwd=repo, log_line=self._append_log)
            self.set_progress(0.85)
            self._append_log("")
            br = _normalize_inject_branch(self.target_client.get())
            self._append_log(f"$ {_inject_install_shell_line(br)}")
            _run_streaming(_inject_install_argv(br), cwd=repo, log_line=self._append_log)
            self.set_progress(1.0)
            self._append_log("")
            self._append_log("Done.")
            target_dirs = [
                os.path.join(repo, "src", "userplugins", os.path.basename(os.path.normpath(p)))
                for p in plugin_srcs
            ]
            self.ui(lambda: self._after_install_plugins_success(target_dirs))
        except subprocess.CalledProcessError as e:
            self.set_progress(0)
            self._append_log(f"\n[Command failed with exit code {e.returncode}]")
            self.ui(lambda: messagebox.showerror("Error", f"Build/inject failed (exit {e.returncode}). See Output."))
        except Exception as e:
            self.set_progress(0)
            self._append_log(f"\n[Error] {e}")
            err_msg = str(e)
            self.ui(lambda: messagebox.showerror("Error", err_msg))

    def _after_install_plugins_success(self, target_dirs: list[str]) -> None:
        body = "\n".join(target_dirs)
        if len(target_dirs) == 1:
            msg = f"Installed to:\n{body}"
        else:
            msg = f"Installed {len(target_dirs)} plugins:\n\n{body}"
        messagebox.showinfo("Success", msg)
        self.main_ui()


if __name__ == "__main__":
    app = EquicordApp()
    app.mainloop()
