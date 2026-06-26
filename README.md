# SUN Merge

A simple, Araxis-style **folder & file compare/merge** tool for Windows — built with Python + Tkinter, no heavy dependencies.

> Công cụ so sánh & hợp nhất thư mục / file đơn giản, giao diện kiểu Araxis Merge. **By Mr.Dai**

## Download

Grab the ready-to-run **[dist/SUN_Merge.exe](dist/SUN_Merge.exe)** — a single file,
no install, no Python needed. Just download and double-click on Windows.
(On the GitHub file page, click the **Download** / raw button to save it.)

## Features

**Folder comparison**
- Two side-by-side panes, synced scroll / selection / expand
- Columns: name, last-modified time, change count
- Identical files in plain black; only differences are coloured
- Exclude filter (glob patterns, e.g. `node_modules;.git;*.log`) — editable, nothing hardcoded
- Copy or delete multiple files / whole folders at once (delete → Recycle Bin)
- Drag & drop two folders to compare — or drop two files to diff directly
- Remembers recent folders and settings between sessions

**File compare & merge**
- Live side-by-side diff with colour highlighting
- **Always editable** — type directly in either pane, the diff updates live
- Inline ▶ ◀ merge arrows at each diff block (Araxis-style), plus toolbar merge
- Navigate diffs (▲ ▼), multi-level Undo
- Auto-detect **UTF-8 / Shift-JIS** encoding; saves back preserving the original
  encoding, BOM and line endings (CRLF/LF)
- Horizontal scrollbars; prompts to save on close if there are unsaved changes
- Binary files (Excel, images, PDF…) are detected: compared/copied but not text-diffed

## Run from source

Requires Python 3.10+.

```bash
pip install tkinterdnd2 send2trash   # optional: drag-drop + recycle-bin delete
python foldermerge.py
```

The app still runs without the optional packages (it just loses drag-drop and
moves deletes straight to permanent delete).

## Build a standalone .exe

```bash
pip install pyinstaller
build_exe.bat      # Windows — produces dist/SUN_Merge.exe
```

A single `SUN_Merge.exe` that runs on any Windows machine without Python.

## License

[MIT](LICENSE) © 2026 Mr.Dai
