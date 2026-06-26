#!/usr/bin/env python3
"""
SUN Merge v1.1 - Folder Comparison & Merge tool (Araxis-style) - By Mr.Dai

A simple desktop GUI to compare two folders side by side, see which files are
identical / different / only-on-one-side, open a file diff view, and copy
(merge) files left->right or right->left.

Pure standard library (tkinter + difflib) - no extra dependencies.
Run:  python foldermerge.py
"""

import os
import sys
import shutil
import filecmp
import difflib
import threading
import queue
import json
import re
import fnmatch
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Optional drag & drop support (pip install tkinterdnd2)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_OK = True
except ImportError:  # app still runs, just without drag & drop
    TkinterDnD = None
    DND_FILES = None
    _DND_OK = False

# Move deleted files to the Recycle Bin (pip install send2trash) so they can be
# restored. Falls back to permanent delete if the library isn't available.
try:
    from send2trash import send2trash
    _TRASH_OK = True
except ImportError:
    send2trash = None
    _TRASH_OK = False

# ---- File status constants -------------------------------------------------
SAME = "identical"
DIFF = "different"
LEFT_ONLY = "left_only"
RIGHT_ONLY = "right_only"

STATUS_LABEL = {
    SAME: "Giống nhau",
    DIFF: "Khác nhau",
    LEFT_ONLY: "Chỉ bên trái",
    RIGHT_ONLY: "Chỉ bên phải",
}

# Row foreground (text) colors per status
STATUS_COLOR = {
    SAME: "black",          # identical -> plain black, so diffs stand out
    DIFF: "#c62828",        # red
    LEFT_ONLY: "#1565c0",   # blue
    RIGHT_ONLY: "#6a1b9a",  # purple
}

# Row background highlight per status (Araxis-style); "" = default
STATUS_BG = {
    SAME: "",
    DIFF: "#fff3cc",        # light amber
    LEFT_ONLY: "#e3f0ff",   # light blue
    RIGHT_ONLY: "#f3e8fb",  # light purple
}


# Supported text encodings. cp932 is Windows' Shift-JIS superset; we try
# strict UTF-8 first (it rejects most Shift-JIS byte sequences) then fall back.
ENCODING_LABELS = {"auto": "Tự động", "utf-8": "UTF-8", "shift_jis": "Shift-JIS"}
# Reading uses utf-8-sig so a BOM is stripped; writing uses plain utf-8 so we
# don't re-add a BOM (which corrupted round-trips).
_ENC_CODEC = {"utf-8": "utf-8-sig", "shift_jis": "cp932"}
_SAVE_CODEC = {"utf-8": "utf-8", "shift_jis": "cp932"}

GUTTER_W = 30   # width of the center merge-arrow gutter in the diff window


def decode_bytes(raw, mode="auto"):
    """Decode bytes to (text, encoding_name).

    mode='auto' detects UTF-8 vs Shift-JIS; otherwise force the given encoding.
    """
    if mode in ("utf-8", "shift_jis"):
        return raw.decode(_ENC_CODEC[mode], errors="replace"), mode
    for enc, name in (("utf-8-sig", "utf-8"), ("cp932", "shift_jis")):
        try:
            return raw.decode(enc), name
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def read_text_file(path, mode="auto"):
    """Read a file, preserving info needed to save it back unchanged.

    Returns (lines, encoding, has_bom, newline, final_newline) where newline is
    the dominant line terminator ('\\r\\n' / '\\n' / '\\r') and final_newline is
    True if the file ended with a line break.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return [f"<không đọc được: {exc}>"], "utf-8", False, os.linesep, True
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text, enc = decode_bytes(raw, mode)
    newline = "\r\n" if "\r\n" in text else ("\r" if "\r" in text else "\n")
    final_newline = text.endswith(("\n", "\r"))
    return text.splitlines(), enc, has_bom, newline, final_newline


def read_lines(path, mode="auto"):
    """Read a text file into (list_of_lines, detected_encoding)."""
    lines, enc, *_ = read_text_file(path, mode)
    return lines, enc


def count_changes(left_path, right_path):
    """Number of changed line-blocks between two text files (for 'Changes')."""
    try:
        a, _ = read_lines(left_path)
        b, _ = read_lines(right_path)
    except OSError:
        return "?"
    sm = difflib.SequenceMatcher(None, a, b)
    return str(sum(1 for op in sm.get_opcodes() if op[0] != "equal"))


def is_binary_file(path):
    """Heuristic: a file is 'binary' if its first 8 KB contains a NUL byte.
    Such files (Excel, images, PDF, zip...) can be compared/copied but not
    shown meaningfully in the text diff view.
    """
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(8192)
    except OSError:
        return False


def mtime_str(path):
    """Last-modified time of a file as 'YYYY-MM-DD HH:MM', or '' if missing."""
    try:
        return datetime.fromtimestamp(
            os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


# Remember recently used folders between sessions. Store the config next to
# the running program: the .exe folder when frozen (PyInstaller), otherwise
# next to this script. (When frozen, __file__ points at a temp dir that is
# wiped on exit, so history would be lost.)
if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_APP_DIR, "foldermerge_config.json")
MAX_RECENT = 12


def parse_drop_data(data):
    """Parse tkinterdnd2 drop data into a list of paths.

    Paths containing spaces are wrapped in {curly braces}; others are
    space-separated. We avoid Tcl's splitlist here because it mangles the
    backslashes in native Windows paths.
    """
    if not data:
        return []
    return [braced or plain
            for braced, plain in re.findall(r"\{([^}]*)\}|(\S+)", data)]


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# Default value for the user-editable exclude filter: heavy / noise folders
# that are usually not worth comparing. The user can delete any of these from
# the filter box to actually compare them (e.g. node_modules).
DEFAULT_EXCLUDE = ("node_modules;.git;.svn;.hg;__pycache__;.venv;venv;"
                   "dist;build;.next;.cache;.idea;.vs;bin;obj")


def parse_excludes(text):
    """Split a user filter string ('*.log; temp; báo giá*.csv') into patterns."""
    return [p.strip() for p in re.split(r"[;,\n]", text or "") if p.strip()]


def _excluded(name, rel, patterns):
    """True if a file/dir matches any exclude glob (by basename or rel path)."""
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p)
               for p in patterns)


def list_files(root, exclude=()):
    """Return a set of file paths relative to root (using forward slashes).

    ``exclude`` is a list of glob patterns; matching files/dirs are skipped
    (and excluded dirs are not descended into). Nothing is hardcoded — the
    caller decides what to skip, so even node_modules can be compared.
    """
    result = set()
    if not root or not os.path.isdir(root):
        return result
    for dirpath, dirs, files in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        # prune excluded dirs in place so os.walk doesn't descend into them
        dirs[:] = [
            d for d in dirs
            if not _excluded(
                d, ("" if rel_dir == "." else rel_dir + "/") + d
                .replace("\\", "/"), exclude)]
        for name in files:
            rel = name if rel_dir == "." else os.path.join(rel_dir, name)
            rel = rel.replace("\\", "/")
            if _excluded(name, rel, exclude):
                continue
            result.add(rel)
    return result


def file_status(left_root, right_root, rel):
    """Compute the status of one relative file path across the two roots."""
    lpath = os.path.join(left_root, rel)
    rpath = os.path.join(right_root, rel)
    lexists = os.path.isfile(lpath)
    rexists = os.path.isfile(rpath)
    if lexists and not rexists:
        return LEFT_ONLY
    if rexists and not lexists:
        return RIGHT_ONLY
    # both exist -> compare contents (shallow=False does a real byte compare)
    try:
        if filecmp.cmp(lpath, rpath, shallow=False):
            return SAME
        return DIFF
    except OSError:
        return DIFF


class DiffWindow(tk.Toplevel):
    """Side-by-side file diff with block-level, two-way merge.

    Click a highlighted block, then push it to the other side with the
    toolbar arrows. Each side can be saved back to disk.
    """

    # Readable palette: dark text on light, distinct backgrounds.
    TAG_BG = {
        "equal": "white",
        "delete": "#ffd1d1",    # only-on-left  (light red)
        "insert": "#cdebc6",    # only-on-right (light green)
        "replace": "#d6e6ff",   # changed       (light blue)
        "filler": "#ececec",    # alignment gap (grey)
        "sel": "#9ec5ff",       # selected block (stronger blue)
    }

    def __init__(self, master, left_path, right_path):
        super().__init__(master)
        self.left_path = left_path
        self.right_path = right_path
        self.enc_mode = tk.StringVar(value="auto")  # auto / utf-8 / shift_jis
        self.left_enc = "utf-8"
        self.right_enc = "utf-8"
        self.blocks = []          # list of dicts: kind,i1,i2,j1,j2,tag
        self.sel = None           # index of selected block
        self.left_dirty = False
        self.right_dirty = False
        self._undo_stack = []     # snapshots taken before each merge
        self._edit_job = None     # pending live-rediff job (edit mode)
        self.geometry("1180x740")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._reload_files()
        self._build_toolbar()
        self._build_panes()
        self._render()

    def _sync_edit_state(self):
        """Pull hand edits from the widgets into the line buffers."""
        new_l = self._widget_lines(self.left_text)
        new_r = self._widget_lines(self.right_text)
        if new_l != self.left_lines:
            self.left_dirty = True
        if new_r != self.right_lines:
            self.right_dirty = True
        self.left_lines, self.right_lines = new_l, new_r

    def _on_close(self):
        self._sync_edit_state()
        if self.left_dirty or self.right_dirty:
            ans = messagebox.askyesnocancel(
                "Đóng", "Có thay đổi chưa lưu. Bạn muốn lưu lại không?")
            if ans is None:          # Cancel -> keep window open
                return
            if ans:                  # Yes -> save dirty side(s)
                if self.left_dirty:
                    self._save("left")
                if self.right_dirty:
                    self._save("right")
                if self.left_dirty or self.right_dirty:
                    return           # a save failed; don't close yet
        self.destroy()

    def _reload_files(self):
        mode = self.enc_mode.get()
        (self.left_lines, self.left_enc, self.left_bom,
         self.left_nl, self.left_final) = self._read(self.left_path, mode)
        (self.right_lines, self.right_enc, self.right_bom,
         self.right_nl, self.right_final) = self._read(self.right_path, mode)

    # ---- UI -----------------------------------------------------------------
    def _build_toolbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=4)
        ttk.Button(bar, text="▲ Khác trước",
                   command=lambda: self._nav(-1)).pack(side="left")
        ttk.Button(bar, text="▼ Khác sau",
                   command=lambda: self._nav(1)).pack(side="left", padx=(4, 12))
        ttk.Button(bar, text="◀ Lấy sang TRÁI",
                   command=lambda: self._merge("to_left")).pack(side="left")
        ttk.Button(bar, text="Đưa sang PHẢI ▶",
                   command=lambda: self._merge("to_right")).pack(
            side="left", padx=(4, 4))
        self.undo_btn = ttk.Button(bar, text="↶ Undo", command=self._undo,
                                   state="disabled")
        self.undo_btn.pack(side="left", padx=(0, 12))
        ttk.Button(bar, text="💾 Lưu trái",
                   command=lambda: self._save("left")).pack(side="left")
        ttk.Button(bar, text="💾 Lưu phải",
                   command=lambda: self._save("right")).pack(
            side="left", padx=(4, 0))

        # Encoding selector (auto-detect UTF-8 / Shift-JIS, or force one).
        ttk.Label(bar, text="Encoding:").pack(side="right", padx=(0, 4))
        self.enc_combo = ttk.Combobox(bar, width=10, state="readonly",
                                      values=list(ENCODING_LABELS.values()))
        self.enc_combo.set(ENCODING_LABELS[self.enc_mode.get()])
        self.enc_combo.pack(side="right")
        self.enc_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._on_encoding_change(self.enc_combo.get()))

    def _on_encoding_change(self, label):
        # re-reading from disk discards hand edits -> confirm if dirty
        self._sync_edit_state()
        if self.left_dirty or self.right_dirty:
            if not messagebox.askyesno(
                    "Encoding",
                    "Có thay đổi chưa lưu sẽ bị bỏ khi đổi encoding. Tiếp tục?"):
                self.enc_combo.set(ENCODING_LABELS[self.enc_mode.get()])
                return
        mode = next(k for k, v in ENCODING_LABELS.items() if v == label)
        self.enc_mode.set(mode)
        self._reload_files()         # re-decode both files in the new mode
        self._undo_stack = []        # encoding change resets edit history
        self.left_dirty = self.right_dirty = False
        self.undo_btn.configure(state="disabled")
        self._render()

    def _build_panes(self):
        header = ttk.Frame(self)
        header.pack(fill="x", padx=6)
        self.left_hdr = ttk.Label(header, anchor="w")
        self.left_hdr.pack(side="left", expand=True, fill="x")
        self.right_hdr = ttk.Label(header, anchor="w")
        self.right_hdr.pack(side="right", expand=True, fill="x")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=6, pady=4)
        self.left_text = tk.Text(body, wrap="none", font=("Consolas", 10),
                                 width=10, foreground="black", background="white")
        self.right_text = tk.Text(body, wrap="none", font=("Consolas", 10),
                                  width=10, foreground="black", background="white")
        # Center gutter holds the inline merge arrows (Araxis-style).
        self.gutter = tk.Canvas(body, width=GUTTER_W, highlightthickness=0,
                                background="#eef1f5", takefocus=0)
        self.vscroll = ttk.Scrollbar(body, orient="vertical",
                                     command=self._yview)
        self.left_text.configure(yscrollcommand=self._on_text_scroll)
        self.right_text.configure(yscrollcommand=self._on_text_scroll)
        # Per-pane horizontal scrollbars for long lines (wrap is off).
        lhscroll = ttk.Scrollbar(body, orient="horizontal",
                                 command=self.left_text.xview)
        rhscroll = ttk.Scrollbar(body, orient="horizontal",
                                 command=self.right_text.xview)
        self.left_text.configure(xscrollcommand=lhscroll.set)
        self.right_text.configure(xscrollcommand=rhscroll.set)
        self.left_text.grid(row=0, column=0, sticky="nsew")
        self.gutter.grid(row=0, column=1, sticky="ns")
        self.right_text.grid(row=0, column=2, sticky="nsew")
        self.vscroll.grid(row=0, column=3, sticky="ns")
        lhscroll.grid(row=1, column=0, sticky="ew")
        rhscroll.grid(row=1, column=2, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1, uniform="p")
        body.columnconfigure(2, weight=1, uniform="p")

        for t in (self.left_text, self.right_text):
            for name, bg in self.TAG_BG.items():
                t.tag_configure(name, background=bg)
            t.bind("<Button-1>", lambda e, w=t: self._on_click(e, w))
            t.bind("<MouseWheel>", self._on_wheel)
            t.bind("<Button-4>", self._on_wheel)
            t.bind("<Button-5>", self._on_wheel)
            t.bind("<Shift-MouseWheel>",
                   lambda e, w=t: self._on_hwheel(e, w))
            t.bind("<Configure>", lambda e: self._reposition_gutter())
            t.bind("<KeyRelease>", lambda e, w=t: self._on_key_edit(w))

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, anchor="w",
                  relief="sunken").pack(fill="x", side="bottom")

    # ---- IO -----------------------------------------------------------------
    @staticmethod
    def _read(path, mode="auto"):
        if not path or not os.path.isfile(path):
            return [], "utf-8", False, os.linesep, True
        return read_text_file(path, mode)

    # ---- Scroll sync --------------------------------------------------------
    def _yview(self, *args):
        self.left_text.yview(*args)
        self.right_text.yview(*args)
        self._reposition_gutter()

    def _on_text_scroll(self, first, last):
        self.vscroll.set(first, last)
        self._reposition_gutter()

    # ---- Inline merge arrows (center gutter) --------------------------------
    def _reposition_gutter(self):
        """Redraw the ▶/◀ merge buttons aligned to each visible diff block."""
        g = getattr(self, "gutter", None)
        if g is None:
            return
        g.delete("all")
        for n, blk in enumerate(self.blocks):
            # anchor on whichever side has the block's lines
            if blk["i2"] > blk["i1"]:
                info = self.left_text.dlineinfo(f"{blk['i1'] + 1}.0")
            elif blk["j2"] > blk["j1"]:
                info = self.right_text.dlineinfo(f"{blk['j1'] + 1}.0")
            else:
                info = None
            if not info:                 # block scrolled out of view
                continue
            y = info[1] + info[3] // 2   # vertical centre of the block's 1st line
            self._draw_arrow(g, n, "to_right", "▶", 1, y)
            self._draw_arrow(g, n, "to_left", "◀", GUTTER_W - 15, y)

    def _draw_arrow(self, g, n, direction, char, x, y):
        tag = f"arr{direction}{n}"
        g.create_rectangle(x, y - 8, x + 14, y + 8, fill="#dce8fb",
                            outline="#5a87c4", tags=(tag,))
        g.create_text(x + 7, y, text=char, fill="#1a4f9c",
                      font=("Segoe UI", 7), tags=(tag,))
        g.tag_bind(tag, "<Button-1>",
                   lambda e, nn=n, d=direction: self._merge(d, nn))
        g.tag_bind(tag, "<Enter>", lambda e: g.config(cursor="hand2"))
        g.tag_bind(tag, "<Leave>", lambda e: g.config(cursor=""))

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4:
            delta = -3
        elif getattr(event, "num", None) == 5:
            delta = 3
        else:
            delta = (-1 if event.delta > 0 else 1) * 3
        self.left_text.yview_scroll(delta, "units")
        self.right_text.yview_scroll(delta, "units")
        return "break"

    def _on_hwheel(self, event, widget):
        widget.xview_scroll(-2 if event.delta > 0 else 2, "units")
        return "break"

    # ---- Rendering ----------------------------------------------------------
    def _render(self):
        """Build the editable panes from the line buffers, then colour diffs."""
        for t, lines in ((self.left_text, self.left_lines),
                         (self.right_text, self.right_lines)):
            t.configure(state="normal")
            t.delete("1.0", "end")
            t.insert("1.0", "\n".join(lines))
        self._edit_rediff()
        self.after_idle(self._reposition_gutter)

    @staticmethod
    def _widget_lines(widget):
        return widget.get("1.0", "end-1c").split("\n")

    def _tag_lines(self, widget, a, b, tag):
        widget.tag_add(tag, f"{a + 1}.0", f"{b + 1}.0")

    def _edit_rediff(self):
        """Re-diff the two editable panes in place (keeps cursor & text)."""
        self._edit_job = None
        self.left_lines = self._widget_lines(self.left_text)
        self.right_lines = self._widget_lines(self.right_text)
        for t in (self.left_text, self.right_text):
            for name in ("delete", "insert", "replace", "sel"):
                t.tag_remove(name, "1.0", "end")
        self.blocks = []
        self.sel = None
        sm = difflib.SequenceMatcher(None, self.left_lines, self.right_lines)
        for kind, i1, i2, j1, j2 in sm.get_opcodes():
            if kind == "equal":
                continue
            self.blocks.append({"kind": kind, "i1": i1, "i2": i2,
                                "j1": j1, "j2": j2})
            if i2 > i1:
                self._tag_lines(self.left_text, i1, i2,
                                "delete" if kind == "delete" else "replace")
            if j2 > j1:
                self._tag_lines(self.right_text, j1, j2,
                                "insert" if kind == "insert" else "replace")
        self._reposition_gutter()
        self._update_headers()
        self._update_status()

    def _on_key_edit(self, widget):
        if widget is self.left_text:
            self.left_dirty = True
        else:
            self.right_dirty = True
        if self._edit_job:
            self.after_cancel(self._edit_job)
        self._edit_job = self.after(250, self._edit_rediff)  # debounce

    # ---- Selection & navigation --------------------------------------------
    def _block_at(self, widget, line):
        key = "i" if widget is self.left_text else "j"
        for n, b in enumerate(self.blocks):
            if b[key + "1"] <= line < b[key + "2"]:
                return n
        return None

    def _on_click(self, event, widget):
        # set the current block (target of the toolbar ◀ ▶) without stealing
        # the cursor placement that the default click also performs
        line = int(widget.index(f"@{event.x},{event.y}").split(".")[0]) - 1
        self.sel = self._block_at(widget, line)

    def _select_block(self, n, scroll=True):
        if not (0 <= n < len(self.blocks)):
            return
        self.sel = n
        b = self.blocks[n]
        for t in (self.left_text, self.right_text):
            t.tag_remove("sel", "1.0", "end")
        if b["i2"] > b["i1"]:
            self.left_text.tag_add("sel", f"{b['i1']+1}.0", f"{b['i2']+1}.0")
        if b["j2"] > b["j1"]:
            self.right_text.tag_add("sel", f"{b['j1']+1}.0", f"{b['j2']+1}.0")
        for t in (self.left_text, self.right_text):
            t.tag_raise("sel")
        if scroll:
            if b["i2"] > b["i1"]:
                self.left_text.see(f"{b['i1']+1}.0")
            if b["j2"] > b["j1"]:
                self.right_text.see(f"{b['j1']+1}.0")
        self._update_status()

    def _nav(self, step):
        if not self.blocks:
            return
        if self.sel is None:
            n = 0 if step > 0 else len(self.blocks) - 1
        else:
            n = (self.sel + step) % len(self.blocks)
        self._select_block(n)

    # ---- Merge / undo / save ------------------------------------------------
    @staticmethod
    def _set_widget(widget, lines):
        widget.delete("1.0", "end")
        widget.insert("1.0", "\n".join(lines))

    def _merge(self, direction, n=None):
        # n given -> merge that block (inline arrow); else the current one.
        if n is None:
            n = self.sel
        if n is None or not (0 <= n < len(self.blocks)):
            messagebox.showinfo(
                "Merge", "Bấm vào một đoạn khác biệt (hoặc dùng ▲ ▼) trước.")
            return
        self._sync_edit_state()           # pull current text into line buffers
        top = self.left_text.yview()[0]
        self._push_undo()
        b = self.blocks[n]
        lw, rw = list(self.left_lines), list(self.right_lines)
        if direction == "to_right":
            rw[b["j1"]:b["j2"]] = lw[b["i1"]:b["i2"]]
            self.right_lines = rw
            self._set_widget(self.right_text, rw)
            self.right_dirty = True
        else:
            lw[b["i1"]:b["i2"]] = rw[b["j1"]:b["j2"]]
            self.left_lines = lw
            self._set_widget(self.left_text, lw)
            self.left_dirty = True
        self._edit_rediff()
        # keep the view where it was instead of jumping to the top
        self.left_text.yview_moveto(top)
        self.right_text.yview_moveto(top)
        if self.blocks:
            self.sel = min(n, len(self.blocks) - 1)

    def _push_undo(self):
        # snapshot the full state before a merge so it can be reverted
        self._undo_stack.append((list(self.left_lines), list(self.right_lines),
                                 self.left_dirty, self.right_dirty))
        self.undo_btn.configure(state="normal")

    def _undo(self):
        if not self._undo_stack:
            return
        (self.left_lines, self.right_lines,
         self.left_dirty, self.right_dirty) = self._undo_stack.pop()
        if not self._undo_stack:
            self.undo_btn.configure(state="disabled")
        self._render()

    def _save(self, side):
        # The Text widgets are the source of truth -> capture before saving.
        self.left_lines = self._widget_lines(self.left_text)
        self.right_lines = self._widget_lines(self.right_text)
        if side == "left":
            path, lines, enc = self.left_path, self.left_lines, self.left_enc
            bom, nl, final = self.left_bom, self.left_nl, self.left_final
        else:
            path, lines, enc = self.right_path, self.right_lines, self.right_enc
            bom, nl, final = self.right_bom, self.right_nl, self.right_final
        if not path:
            messagebox.showwarning("Lưu", "Bên này chưa có đường dẫn file.")
            return
        # Save back EXACTLY as read: same encoding, BOM and newline style, so
        # the file's format isn't silently changed.
        codec = _SAVE_CODEC.get(enc, "utf-8")
        text = nl.join(lines) + (nl if (lines and final) else "")
        try:
            data = text.encode(codec)
        except UnicodeEncodeError as exc:
            messagebox.showerror(
                "Lưu", f"Có ký tự không mã hoá được sang {ENCODING_LABELS[enc]}"
                f":\n{exc}\n\nThử đổi Encoding sang UTF-8 rồi lưu lại.")
            return
        if bom and enc == "utf-8":
            data = b"\xef\xbb\xbf" + data
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as exc:
            messagebox.showerror("Lưu", f"Lỗi: {exc}")
            return
        if side == "left":
            self.left_dirty = False
        else:
            self.right_dirty = False
        self._update_headers()
        self.status.set(f"Đã lưu ({ENCODING_LABELS[enc]}): {path}")

    # ---- Labels -------------------------------------------------------------
    def _update_headers(self):
        self.title("Diff:  " + os.path.basename(
            self.left_path or self.right_path or ""))
        lmark = " *" if self.left_dirty else ""
        rmark = " *" if self.right_dirty else ""
        self.left_hdr.config(
            text=f"[{ENCODING_LABELS[self.left_enc]}] "
            + (self.left_path or "(không tồn tại)") + lmark)
        self.right_hdr.config(
            text=f"[{ENCODING_LABELS[self.right_enc]}] "
            + (self.right_path or "(không tồn tại)") + rmark)

    def _update_status(self):
        n = len(self.blocks)
        if n == 0:
            self.status.set("2 file giống hệt nhau — gõ trực tiếp để sửa.")
        else:
            extra = f"  |  đang ở đoạn {self.sel + 1}/{n}" \
                if self.sel is not None else ""
            self.status.set(
                f"{n} đoạn khác. Gõ trực tiếp để sửa, bấm ▶ ◀ ở giữa để "
                f"merge từng đoạn (hoặc ▲ ▼ rồi nút trên thanh).{extra}")


_BASE = TkinterDnD.Tk if _DND_OK else tk.Tk


class App(_BASE):
    def __init__(self):
        super().__init__()
        self.title("SUN Merge v1.1 - So sánh & Hợp nhất thư mục  |  By Mr.Dai")
        self.geometry("1000x650")

        self._config = load_config()
        self.left_var = tk.StringVar(value=self._recent("left")[:1] and
                                     self._recent("left")[0] or "")
        self.right_var = tk.StringVar(value=self._recent("right")[:1] and
                                      self._recent("right")[0] or "")
        self.only_diff = tk.BooleanVar(
            value=bool(self._config.get("only_diff", False)))
        self.exclude_var = tk.StringVar(
            value=self._config.get("exclude", DEFAULT_EXCLUDE))
        self._rows = {}      # rel path -> status
        self._changes = {}   # rel path -> changes count (display string)
        self._lmtime = {}    # rel path -> left  file modified time (string)
        self._rmtime = {}    # rel path -> right file modified time (string)
        self._busy = False   # a comparison is running in the background

        self._build_top()
        self._build_tree()
        self._build_status_bar()
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)

    def _on_app_close(self):
        # Persist UI settings even if the user never pressed 'So sánh'.
        self._config["exclude"] = self.exclude_var.get()
        self._config["only_diff"] = self.only_diff.get()
        save_config(self._config)
        self.destroy()

    def _recent(self, side):
        return self._config.get(f"{side}_recent", [])

    # ---- UI construction ----------------------------------------------------
    def _build_top(self):
        # Two path bars side by side, each sitting above its own pane.
        top = ttk.Frame(self)
        top.pack(fill="x", padx=8, pady=(6, 2))
        top.columnconfigure(0, weight=1, uniform="panes")
        top.columnconfigure(1, weight=1, uniform="panes")

        self.left_combo = self._path_bar(top, 0, self.left_var, "left",
                                         (0, 4))
        self.right_combo = self._path_bar(top, 1, self.right_var, "right",
                                          (4, 0))
        # narrow spacer matching the panes' scrollbar so bars line up with panes
        ttk.Frame(top, width=18).grid(row=0, column=2)

        # Enable drag & drop of a folder onto each entry (if available)
        self._enable_dnd(self.left_combo, self.left_var)
        self._enable_dnd(self.right_combo, self.right_var)

        self._build_toolbar()

    def _path_bar(self, parent, col, var, side, padx):
        """One folder path bar (history combobox + browse) for column `col`."""
        bar = ttk.Frame(parent)
        bar.grid(row=0, column=col, sticky="we", padx=padx)
        combo = ttk.Combobox(bar, textvariable=var, values=self._recent(side))
        combo.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="...", width=3,
                   command=lambda: self._browse(var)).pack(side="left",
                                                           padx=(4, 0))
        return combo

    def _build_toolbar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(0, 4))
        self.compare_btn = ttk.Button(bar, text="So sánh",
                                      command=self.compare)
        self.compare_btn.pack(side="left")
        ttk.Button(bar, text="Mở Diff", command=self.open_diff).pack(
            side="left", padx=(6, 0))
        ttk.Button(bar, text="Copy  →", command=lambda: self.copy("ltr")).pack(
            side="left", padx=(6, 0))
        ttk.Button(bar, text="Copy  ←", command=lambda: self.copy("rtl")).pack(
            side="left", padx=(6, 0))
        ttk.Button(bar, text="🗑 Xóa trái",
                   command=lambda: self.delete("left")).pack(
            side="left", padx=(12, 0))
        ttk.Button(bar, text="🗑 Xóa phải",
                   command=lambda: self.delete("right")).pack(
            side="left", padx=(4, 0))
        ttk.Checkbutton(bar, text="Chỉ hiện khác biệt",
                        variable=self.only_diff,
                        command=self._refresh_tree).pack(side="left", padx=12)

        # Exclude filter: glob patterns separated by ';'. Applied on next
        # compare. Pre-filled with common heavy dirs (delete to compare them).
        ex = ttk.Entry(bar, textvariable=self.exclude_var, width=40)
        ex.pack(side="right")
        ex.bind("<Return>", lambda e: self.compare())
        ttk.Label(bar, text="Bỏ qua:").pack(side="right", padx=(8, 4))

    def _build_tree(self):
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        # Left tree: Name | Sửa đổi | Changes (Changes sits on the inner edge,
        # next to the divider, like Araxis). Right tree: Name | Sửa đổi.
        self.left_tree = ttk.Treeview(frame, selectmode="extended",
                                      columns=("modified", "changes"))
        self.right_tree = ttk.Treeview(frame, selectmode="extended",
                                       columns=("modified",))
        self.left_tree.heading("#0", text="Thư mục trái")
        self.left_tree.heading("modified", text="Sửa đổi")
        self.left_tree.heading("changes", text="Changes")
        self.left_tree.column("modified", width=120, anchor="w", stretch=False)
        self.left_tree.column("changes", width=64, anchor="center",
                              stretch=False)
        self.right_tree.heading("#0", text="Thư mục phải")
        self.right_tree.heading("modified", text="Sửa đổi")
        self.right_tree.column("modified", width=120, anchor="w", stretch=False)

        # One shared scrollbar drives BOTH trees. Each tree only reports its
        # position to the scrollbar (set) - we never cross-drive one tree from
        # the other's scroll callback (that caused an async feedback loop that
        # locked up the UI). The two trees are row-aligned and always scrolled
        # together, so they stay in lockstep.
        self.vscroll = ttk.Scrollbar(frame, orient="vertical",
                                     command=self._yview_both)
        self.left_tree.configure(yscrollcommand=self.vscroll.set)
        self.right_tree.configure(yscrollcommand=self.vscroll.set)

        self.left_tree.grid(row=0, column=0, sticky="nsew")
        self.right_tree.grid(row=0, column=1, sticky="nsew")
        self.vscroll.grid(row=0, column=2, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1, uniform="panes")
        frame.columnconfigure(1, weight=1, uniform="panes")

        for tree in (self.left_tree, self.right_tree):
            for status, color in STATUS_COLOR.items():
                tree.tag_configure(status, foreground=color,
                                   background=STATUS_BG.get(status, ""))
            tree.tag_configure("missing", background="#f0f0f0")
            tree.bind("<Double-1>", lambda e: self.open_diff())
            tree.bind("<MouseWheel>", self._on_wheel)        # Windows / macOS
            tree.bind("<Button-4>", self._on_wheel)          # Linux scroll up
            tree.bind("<Button-5>", self._on_wheel)          # Linux scroll down
            tree.bind("<<TreeviewSelect>>",
                      lambda e, t=tree: self._on_select(t))
            tree.bind("<<TreeviewOpen>>",
                      lambda e, t=tree: self._on_toggle(t, True))
            tree.bind("<<TreeviewClose>>",
                      lambda e, t=tree: self._on_toggle(t, False))

    # ---- Two-pane sync helpers ---------------------------------------------
    def _yview_both(self, *args):
        self.left_tree.yview(*args)
        self.right_tree.yview(*args)

    def _on_wheel(self, event):
        # Scroll both panes together; "break" stops the default single-pane scroll
        if getattr(event, "num", None) == 4:
            delta = -3
        elif getattr(event, "num", None) == 5:
            delta = 3
        else:
            delta = -1 if event.delta > 0 else 1
            delta *= 3
        self.left_tree.yview_scroll(delta, "units")
        self.right_tree.yview_scroll(delta, "units")
        return "break"

    def _on_select(self, source):
        # Idempotent: if both panes already match, do nothing -> no event
        # ping-pong even if <<TreeviewSelect>> fires asynchronously.
        other = self.right_tree if source is self.left_tree else self.left_tree
        sel = source.selection()
        if tuple(other.selection()) == tuple(sel):
            return
        other.selection_set(sel)
        if sel:
            self.left_tree.see(sel[0])
            self.right_tree.see(sel[0])

    def _on_toggle(self, source, opened):
        item = source.focus()
        if not item:
            return
        other = self.right_tree if source is self.left_tree else self.left_tree
        if other.exists(item) and bool(other.item(item, "open")) != opened:
            other.item(item, open=opened)

    def _build_status_bar(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", side="bottom")
        ttk.Label(bar, text="v1.1 · By Mr.Dai", anchor="e",
                  relief="sunken", foreground="#555").pack(side="right")
        hint = ("Kéo-thả 2 thư mục rồi bấm 'So sánh' — hoặc thả 2 file để so "
                "sánh trực tiếp.") if _DND_OK \
            else "Chọn 2 thư mục rồi bấm 'So sánh'."
        self.status = tk.StringVar(value=hint)
        ttk.Label(bar, textvariable=self.status, anchor="w",
                  relief="sunken").pack(side="left", fill="x", expand=True)

    # ---- Actions ------------------------------------------------------------
    def _browse(self, var):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _enable_dnd(self, widget, var):
        """Register a widget as a drop target for a folder path."""
        if not _DND_OK:
            return
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind(
            "<<Drop>>", lambda e: self._on_drop(e, var))

    def _on_drop(self, event, var):
        paths = parse_drop_data(event.data)
        if not paths:
            return
        files = [p for p in paths if os.path.isfile(p)]
        dirs = [p for p in paths if os.path.isdir(p)]
        # Dropped two files at once -> compare them directly in a Diff window.
        if len(files) >= 2:
            self._open_diff(files[0], files[1])
            return
        if dirs:
            var.set(dirs[0])
        elif files:
            # A single file: put it on this side; if the other side is also a
            # file, open the Diff straight away.
            var.set(files[0])
            self._maybe_compare_files()
        else:
            messagebox.showwarning("Kéo thả", "Hãy thả thư mục hoặc file.")

    def _maybe_compare_files(self):
        left, right = self.left_var.get(), self.right_var.get()
        if os.path.isfile(left) and os.path.isfile(right):
            self._open_diff(left, right)

    def _open_diff(self, lpath, rpath):
        """Open the diff view, warning instead if either side is binary."""
        for p in (lpath, rpath):
            if p and is_binary_file(p):
                messagebox.showinfo(
                    "Diff",
                    "File nhị phân (Excel, ảnh, PDF, zip...): chỉ phát hiện "
                    "được giống/khác và copy, không xem được nội dung.")
                return
        DiffWindow(self, lpath, rpath)

    def compare(self):
        left, right = self.left_var.get(), self.right_var.get()
        if not os.path.isdir(left) or not os.path.isdir(right):
            messagebox.showerror("Lỗi", "Cả hai thư mục phải tồn tại.")
            return
        if self._busy:
            return
        self._remember(left, right)   # save last-used folders to history
        excludes = parse_excludes(self.exclude_var.get())
        self._config["exclude"] = self.exclude_var.get()
        save_config(self._config)
        # Heavy work (walking trees, byte-comparing, line-diffing) runs in a
        # worker thread so the GUI never freezes on big projects. The worker
        # does NO tkinter calls; it hands the result back via a queue that the
        # main thread polls (tkinter is not thread-safe).
        self._busy = True
        self.compare_btn.configure(state="disabled")
        self.status.set("Đang quét thư mục... (theo bộ lọc 'Bỏ qua')")
        self._result_q = queue.Queue()

        def worker():
            try:
                all_files = sorted(list_files(left, exclude=excludes) |
                                   list_files(right, exclude=excludes))
                rows, changes, lmt, rmt = {}, {}, {}, {}
                for rel in all_files:
                    st = file_status(left, right, rel)
                    rows[rel] = st
                    lmt[rel] = mtime_str(os.path.join(left, rel))
                    rmt[rel] = mtime_str(os.path.join(right, rel))
                    if st == DIFF:
                        changes[rel] = count_changes(
                            os.path.join(left, rel), os.path.join(right, rel))
                    elif st == SAME:
                        changes[rel] = "0"
                    else:
                        changes[rel] = ""
                self._result_q.put(("done", rows, changes, lmt, rmt))
            except Exception as exc:  # surface failures on the UI thread
                self._result_q.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll_result)

    def _remember(self, left, right):
        """Push the chosen folders to the front of the per-side history."""
        for side, path, combo in (("left", left, self.left_combo),
                                   ("right", right, self.right_combo)):
            recent = [path] + [p for p in self._recent(side) if p != path]
            self._config[f"{side}_recent"] = recent[:MAX_RECENT]
            combo.configure(values=self._config[f"{side}_recent"])
        save_config(self._config)

    def _poll_result(self):
        try:
            msg = self._result_q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_result)  # not done yet, keep polling
            return
        self._busy = False
        self.compare_btn.configure(state="normal")
        if msg[0] == "done":
            _, self._rows, self._changes, self._lmtime, self._rmtime = msg
            self._refresh_tree()
        else:
            self.status.set("Lỗi khi so sánh.")
            messagebox.showerror("Lỗi", msg[1])

    def _refresh_tree(self):
        self.left_tree.delete(*self.left_tree.get_children())
        self.right_tree.delete(*self.right_tree.get_children())
        counts = {SAME: 0, DIFF: 0, LEFT_ONLY: 0, RIGHT_ONLY: 0}
        for rel, status in self._rows.items():
            counts[status] += 1
            if self.only_diff.get() and status == SAME:
                continue
            self._insert_path(rel, status)

        self.status.set(
            f"Tổng: {len(self._rows)}  |  "
            f"Giống: {counts[SAME]}  |  Khác: {counts[DIFF]}  |  "
            f"Chỉ trái: {counts[LEFT_ONLY]}  |  "
            f"Chỉ phải: {counts[RIGHT_ONLY]}"
        )

    def _insert_path(self, rel, status):
        """Insert a file into BOTH trees with the same iids, kept aligned.

        A file present only on one side shows its name on that side and a
        blank, greyed-out row on the other so the two panes stay row-aligned.
        """
        parts = rel.split("/")
        parent = ""
        for i, part in enumerate(parts):
            node_id = "/".join(parts[:i + 1])
            is_file = (i == len(parts) - 1)
            if not self.left_tree.exists(node_id):
                if is_file:
                    on_left = status in (SAME, DIFF, LEFT_ONLY)
                    on_right = status in (SAME, DIFF, RIGHT_ONLY)
                    self.left_tree.insert(
                        parent, "end", iid=node_id,
                        text=part if on_left else "",
                        values=(self._lmtime.get(rel, ""),
                                self._changes.get(rel, "")),
                        tags=(status if on_left else "missing",))
                    self.right_tree.insert(
                        parent, "end", iid=node_id,
                        text=part if on_right else "",
                        values=(self._rmtime.get(rel, ""),),
                        tags=(status if on_right else "missing",))
                else:
                    self.left_tree.insert(parent, "end", iid=node_id,
                                          text=part, open=True)
                    self.right_tree.insert(parent, "end", iid=node_id,
                                           text=part, open=True)
            parent = node_id

    def _selected_rel(self):
        for iid in self.left_tree.selection():
            if iid in self._rows:  # first selected *file* (skip dir nodes)
                return iid
        return None

    def _selected_rels(self):
        """All selected files; a selected folder expands to its files."""
        rels = []
        for iid in self.left_tree.selection():
            if iid in self._rows:
                rels.append(iid)
            else:  # directory node -> include every file beneath it
                prefix = iid + "/"
                rels.extend(r for r in self._rows if r.startswith(prefix))
        seen, out = set(), []
        for r in rels:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out

    def open_diff(self):
        rel = self._selected_rel()
        if rel is None:
            messagebox.showinfo("Diff", "Chọn một file để xem diff.")
            return
        lpath = os.path.join(self.left_var.get(), rel)
        rpath = os.path.join(self.right_var.get(), rel)
        self._open_diff(lpath if os.path.isfile(lpath) else None,
                        rpath if os.path.isfile(rpath) else None)

    def copy(self, direction):
        rels = self._selected_rels()
        if not rels:
            messagebox.showinfo("Copy", "Chọn ít nhất một file để copy.")
            return
        left_root, right_root = self.left_var.get(), self.right_var.get()
        src_root = left_root if direction == "ltr" else right_root
        # only files that actually exist on the source side can be copied
        todo = [r for r in rels
                if os.path.isfile(os.path.join(src_root, r))]
        if not todo:
            messagebox.showinfo(
                "Copy", "Không có file nguồn nào để copy theo hướng này.")
            return
        arrow = "sang PHẢI →" if direction == "ltr" else "sang TRÁI ←"
        if not messagebox.askyesno(
                "Xác nhận",
                f"Copy {len(todo)} file {arrow}?\n(ghi đè nếu đã có)"):
            return

        errors = []
        for rel in todo:
            lpath = os.path.join(left_root, rel)
            rpath = os.path.join(right_root, rel)
            src, dst = (lpath, rpath) if direction == "ltr" else (rpath, lpath)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            except OSError as exc:
                errors.append(f"{rel}: {exc}")
                continue
            self._refresh_row(rel)

        self._refresh_tree()
        if errors:
            messagebox.showerror(
                "Copy", "Một số file lỗi:\n" + "\n".join(errors[:10]))
        else:
            self.status.set(f"Đã copy {len(todo)} file.")

    def _refresh_row(self, rel):
        """Recompute one row's status after a copy/delete; drop it if gone."""
        left_root, right_root = self.left_var.get(), self.right_var.get()
        lpath, rpath = os.path.join(left_root, rel), os.path.join(right_root, rel)
        if not os.path.isfile(lpath) and not os.path.isfile(rpath):
            for d in (self._rows, self._changes, self._lmtime, self._rmtime):
                d.pop(rel, None)
            return
        st = file_status(left_root, right_root, rel)
        self._rows[rel] = st
        self._changes[rel] = "0" if st == SAME else (
            count_changes(lpath, rpath) if st == DIFF else "")
        self._lmtime[rel] = mtime_str(lpath)
        self._rmtime[rel] = mtime_str(rpath)

    def delete(self, side):
        rels = self._selected_rels()
        if not rels:
            messagebox.showinfo("Xóa", "Chọn file để xóa.")
            return
        root = self.left_var.get() if side == "left" else self.right_var.get()
        todo = [r for r in rels if os.path.isfile(os.path.join(root, r))]
        where = "TRÁI" if side == "left" else "PHẢI"
        if not todo:
            messagebox.showinfo("Xóa", f"Không có file nào bên {where} để xóa.")
            return
        if _TRASH_OK:
            prompt = (f"Đưa {len(todo)} file bên {where} vào Thùng rác?\n"
                      f"(có thể khôi phục từ Recycle Bin)")
            dest = "Thùng rác"
        else:
            prompt = (f"Xóa {len(todo)} file bên {where}?\n\n"
                      f"⚠ Xóa thật khỏi ổ đĩa, KHÔNG hoàn tác được!")
            dest = "đã xóa"
        if not messagebox.askyesno("Xác nhận xóa", prompt):
            return
        errors = []
        for rel in todo:
            full = os.path.join(root, rel)
            try:
                if _TRASH_OK:
                    send2trash(os.path.abspath(full))
                else:
                    os.remove(full)
            except OSError as exc:
                errors.append(f"{rel}: {exc}")
                continue
            self._refresh_row(rel)
        self._refresh_tree()
        if errors:
            messagebox.showerror(
                "Xóa", "Một số file lỗi:\n" + "\n".join(errors[:10]))
        else:
            self.status.set(f"Đã đưa {len(todo)} file bên {where} vào {dest}.")


if __name__ == "__main__":
    App().mainloop()
