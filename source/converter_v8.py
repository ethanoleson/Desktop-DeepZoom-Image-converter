### DeepZoom Converter with JPEG Support, Logging, and Path Safety

import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from pathlib import Path
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
import datetime
import io

# Trust very large images
Image.MAX_IMAGE_PIXELS = 50_000_000_000

# DeepZoom backend
try:
    from deepzoom import ImageCreator
    HAS_DEEPZOOM = True
except Exception:
    HAS_DEEPZOOM = False

root = tk.Tk()
root.title("DeepZoom Image Converter")
root.geometry("960x640")

input_files = []
input_summary = tk.StringVar()
output_dir = tk.StringVar()
status_jpeg = tk.StringVar()
status_dz = tk.StringVar()
elapsed_total = tk.StringVar()
progress_var = tk.IntVar()
progress_max = tk.IntVar(value=100)

log_file_stream = io.StringIO()

def get_log_file_path():
    folder = output_dir.get()
    if not folder:
        return f"converter_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"converter_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

class ConsoleRedirect:
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.text_widget.configure(state='disabled')
        self.suspend_logging = False  # New flag

    def write(self, message):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state='disabled')
        if not self.suspend_logging:
            log_file_stream.write(message)

    def flush(self):
        pass

# === Helpers ===
def path_to_file_uri(p: str) -> str:
    p_abs = os.path.abspath(p)
    if p_abs.startswith("\\\\"):
        return "file://" + quote(p_abs[2:].replace("\\", "/"))
    return Path(p_abs).as_uri()

def _basename_no_ext(path):
    return os.path.splitext(os.path.basename(path))[0]

def _ensure_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    if img.mode == "P" or img.mode != "RGB":
        return img.convert("RGB")
    return img

def _is_unc(p: str) -> bool:
    try:
        return os.path.abspath(p).startswith("\\\\")
    except:
        return False

def _auto_workers(files, out_dir):
    if not files:
        return 1
    if _is_unc(out_dir) or any(_is_unc(f) for f in files):
        return 1
    cores = os.cpu_count() or 4
    return max(1, min(len(files), cores // 2))

# === File pickers ===
def choose_input():
    global input_files
    files = filedialog.askopenfilenames(title="Select images",
                                        filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp")])
    if files:
        input_files = list(files)
        input_summary.set(f"{len(files)} selected (first: {os.path.basename(files[0])})")
        print(f"[Input] {len(files)} files selected\n")

def choose_output():
    folder = filedialog.askdirectory(title="Select output folder")
    if folder:
        output_dir.set(folder)
        print(f"[Output] Folder: {folder}\n")

# === JPEG Conversion ===
def save_jpeg():
    if not input_files or not output_dir.get():
        messagebox.showerror("Missing input", "Select image(s) and output folder first.")
        return

    os.makedirs(output_dir.get(), exist_ok=True)
    progress_max.set(len(input_files))
    progress_var.set(0)

    prog.config(maximum=len(input_files))

    elapsed_total.set("")

    def update_progress(val):
        progress_var.set(val)

    def worker():
        from PIL import ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        t0 = time.perf_counter()
        converted = 0
        errors = 0

        for i, src in enumerate(input_files, 1):
            try:
                if not os.path.exists(src):
                    print(f"[JPEG] Skipped missing: {src}")
                    errors += 1
                    continue

                base = _basename_no_ext(src).rstrip(". ")
                safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in base)
                out_path = os.path.join(output_dir.get(), f"{safe}.jpg")

                with Image.open(src) as im:
                    try: im.seek(0)
                    except: pass
                    im = _ensure_rgb(im)
                    im.save(out_path, "JPEG", quality=95, optimize=True, progressive=True, subsampling=0)

                print(f"[JPEG] Saved: {out_path}")
                converted += 1
            except Exception as e:
                errors += 1
                print(f"[JPEG] Error: {src} => {e}")

            # ✅ Schedule progress update in main thread
            root.after(0, update_progress, i)

        dt = time.perf_counter() - t0
        print(f"[JPEG] Done. {converted} converted, {errors} errors, {dt:.2f}s elapsed\n")

        # Write logs
        with open(get_log_file_path(), "w", encoding="utf-8") as f:
            f.write(log_file_stream.getvalue())

        root.after(0, lambda: messagebox.showinfo("tif converted to jpeg", f"Converted: {converted}\nErrors: {errors}"))

    ThreadPoolExecutor(max_workers=1).submit(worker)


# === DeepZoom ===
DZ_TILE_SIZE = 256
DZ_OVERLAP = 1
DZ_IMAGE_QUALITY = 0.9
DZ_FILTER = Image.LANCZOS

def _dz_make_creator():
    return ImageCreator(
        tile_size=DZ_TILE_SIZE,
        tile_overlap=DZ_OVERLAP,
        tile_format="jpg",
        image_quality=DZ_IMAGE_QUALITY,
        resize_filter=DZ_FILTER
    )

def _dz_convert_one(src, outdir, creator: ImageCreator):
    base = _basename_no_ext(src)
    src_uri = path_to_file_uri(src)
    dzi_path = os.path.join(outdir, f"{base}.dzi")
    t0 = time.perf_counter()
    creator.create(src_uri, dzi_path)
    return (src, time.perf_counter() - t0)

def create_deepzoom():
    if not HAS_DEEPZOOM:
        messagebox.showerror("Missing", "Install 'deepzoom' package")
        return
    if not input_files or not output_dir.get():
        messagebox.showerror("Missing input", "Select files and output folder.")
        return

    os.makedirs(output_dir.get(), exist_ok=True)
    progress_max.set(len(input_files))
    progress_var.set(0)

    prog.config(maximum=len(input_files))

    def worker():
        t0 = time.perf_counter()
        creator = _dz_make_creator()
        success = 0
        for i, src in enumerate(input_files, 1):
            try:
                _, dt = _dz_convert_one(src, output_dir.get(), creator)
                print(f"[DZI] Created: {_basename_no_ext(src)}.dzi ({dt:.2f}s)")
                success += 1
            except Exception as e:
                print(f"[DZI] Error: {src} => {e}")
            root.after(0, lambda val=i: progress_var.set(val))

        dt_total = time.perf_counter() - t0
        print(f"[DZI] Done. Created: {success}, Elapsed: {dt_total:.2f}s\n")
        with open(get_log_file_path(), "w", encoding="utf-8") as f:
            f.write(log_file_stream.getvalue())
        root.after(0, lambda: messagebox.showinfo("DZI creation done", f"Created: {success}, Time: {dt_total:.2f}s"))

    ThreadPoolExecutor(max_workers=1).submit(worker)


# === UI ===
row = 0
tk.Button(root, text="Select Input Image(s)", command=choose_input).grid(row=row, column=0)
tk.Entry(root, textvariable=input_summary, width=60).grid(row=row, column=1)
row += 1
tk.Button(root, text="Choose Output Folder", command=choose_output).grid(row=row, column=0)
tk.Entry(root, textvariable=output_dir, width=60).grid(row=row, column=1)
row += 1
tk.Button(root, text="Save JPEG(s)", command=save_jpeg).grid(row=row, column=0)
tk.Label(root, textvariable=status_jpeg).grid(row=row, column=1, sticky="w")
row += 1
tk.Button(root, text="Create DeepZoom (.dzi)", command=create_deepzoom).grid(row=row, column=0)
tk.Label(root, textvariable=status_dz).grid(row=row, column=1, sticky="w")
row += 1

# Progress
prog = ttk.Progressbar(root, orient="horizontal", mode="determinate", length=400,
                       variable=progress_var)

prog.grid(row=row, column=0, columnspan=2, pady=8)
row += 1

# Console
console = scrolledtext.ScrolledText(root, height=15, bg="black", fg="lime", insertbackground="white")
console.grid(row=row, column=0, columnspan=2, padx=8, pady=6, sticky="nsew")
sys.stdout = ConsoleRedirect(console)

root.columnconfigure(1, weight=1)
root.rowconfigure(row, weight=1)

redir = ConsoleRedirect(console)
sys.stdout = redir
redir.suspend_logging = True

print("""


Welcome to the DeepZoom Image Converter. 
This software converts tifs to jpegs and
jpegs to dzi.

DeepZoom settings: tile=256, overlap=1, quality=0.9, filter=LANCZOS.

For more information, refer to the documentation
on the GitHub page or email Ethan Oleson: 
eoleson (at) uark (dot) edu. 

░▒▓███████▓▒░░▒▓████████▓▒░▒▓████████▓▒░▒▓███████▓▒░▒▓████████▓▒░░▒▓██████▓▒░ ░▒▓██████▓▒░░▒▓██████████████▓▒░  
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░     ░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░ 
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░   ░▒▓██▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░ 
░▒▓█▓▒░░▒▓█▓▒░▒▓██████▓▒░ ░▒▓██████▓▒░ ░▒▓███████▓▒░  ░▒▓██▓▒░  ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░ 
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░      ░▒▓█▓▒░      ░▒▓██▓▒░    ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░ 
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░      ░▒▓█▓▒░     ░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░ 
░▒▓███████▓▒░░▒▓████████▓▒░▒▓████████▓▒░▒▓█▓▒░     ░▒▓████████▓▒░░▒▓██████▓▒░ ░▒▓██████▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░ 
                                                                                                                
=== DeepZoom Converter Ready ===
""")
redir.suspend_logging = False
root.mainloop()