### This version should handle issues with .tif to .jpg conversion - EWO Dec. 2025 

import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from pathlib import Path
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Trust very large images
Image.MAX_IMAGE_PIXELS = 50_000_000_000  # or None for unlimited

# DeepZoom backend
try:
    from deepzoom import ImageCreator
    HAS_DEEPZOOM = True
except Exception:
    HAS_DEEPZOOM = False

# ---------- GUI ----------
root = tk.Tk()
root.title("DeepZoom Image Converter — Quality Preserved (Auto Workers)")
root.geometry("980x650")

# ===== State =====
input_files = []                 # list[str]
input_summary = tk.StringVar(value="")
output_dir = tk.StringVar(value="")
status_jpeg = tk.StringVar(value="")
status_dz = tk.StringVar(value="")
elapsed_total = tk.StringVar(value="")

# progress
progress_var = tk.IntVar(value=0)
progress_max = tk.IntVar(value=100)

# ===== Console Redirect =====
class ConsoleRedirect:
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.text_widget.configure(state='disabled')
    def write(self, message):
        self.text_widget.configure(state='normal')
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)
        self.text_widget.configure(state='disabled')
    def flush(self):
        pass

# ===== Helpers =====
def path_to_file_uri(p: str) -> str:
    """Convert Windows paths (drive or UNC) to file:// URI."""
    p_abs = os.path.abspath(p)
    if p_abs.startswith("\\\\"):  # UNC
        without_leading = p_abs[2:].replace("\\", "/")
        return "file://" + quote(without_leading)
    return Path(p_abs).as_uri()

def _basename_no_ext(path):
    return os.path.splitext(os.path.basename(path))[0]

def _summarize_file_selection(files):
    if not files:
        return ""
    return files[0] if len(files) == 1 else f"{len(files)} files selected (first: {files[0]})"

def _ensure_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    if img.mode == "P":
        return img.convert("RGB")
    if img.mode != "RGB":
        return img.convert("RGB")
    return img

def _set_busy(busy: bool):
    for child in root.winfo_children():
        if isinstance(child, tk.Button):
            child.config(state="disabled" if busy else "normal")

def _is_unc(p: str) -> bool:
    try:
        return os.path.abspath(p).startswith("\\\\")
    except Exception:
        return False

def _auto_workers(files, out_dir) -> int:
    """
    Auto-choose worker count:
    - If any input/output is UNC (network share): 1 worker
    - Else: half of CPU cores, capped by number of files, min 1
    """
    if not files:
        return 1
    if _is_unc(out_dir) or any(_is_unc(f) for f in files):
        w = 1
    else:
        cores = os.cpu_count() or 4
        w = max(1, cores // 2)
        w = min(w, len(files))
    print(f"[Auto Workers] Using {w} worker(s) "
          f"(UNC={'Yes' if (_is_unc(out_dir) or any(_is_unc(f) for f in files)) else 'No'}, "
          f"CPU={os.cpu_count()})")
    return w

# ===== File pickers =====
def choose_input():
    global input_files
    files = filedialog.askopenfilenames(
        title="Select input image(s)",
        filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"), ("All files", "*.*")]
    )
    if not files:
        return
    input_files = list(files)
    input_summary.set(_summarize_file_selection(input_files))
    print(f"[Select Input] {len(input_files)} file(s) selected:")
    for f in input_files:
        print("  -", f)
    print()

def choose_output():
    folder = filedialog.askdirectory(title="Choose output folder")
    if folder:
        output_dir.set(folder)
        print(f"[Choose Output] Selected folder: {folder}\n")

# ===== JPEG conversion =====
def save_jpeg():
    if not input_files or not output_dir.get():
        messagebox.showerror("Missing input", "Select input image(s) and an output folder first.")
        return

    os.makedirs(output_dir.get(), exist_ok=True)
    total = len(input_files)
    progress_max.set(total)
    progress_var.set(0)
    elapsed_total.set("")
    _set_busy(True)

    def worker():
        t0 = time.perf_counter()
        converted = 0
        errors = 0
        for i, src in enumerate(input_files, 1):
            t_file = time.perf_counter()
            try:
                if not os.path.exists(src):
                    print(f"[Save JPEG] Skipped (not found): {src}")
                    errors += 1
                else:
                    base = _basename_no_ext(src).rstrip(". ")

                    # Sanitize base to remove bad characters
                    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in base)
                    out_path = os.path.join(output_dir.get(), f"{safe}.jpg")

                    print("[DEBUG] Saving:", out_path)

                    from PIL import ImageFile
                    ImageFile.LOAD_TRUNCATED_IMAGES = True  # Allow broken TIFFs

                    with Image.open(src) as im:
                        try:
                            im.seek(0)  # Handle multi-page TIFFs
                        except Exception:
                            pass

                        im = _ensure_rgb(im)

                        im.save(out_path, "JPEG",
                                quality=95, optimize=True,
                                progressive=True, subsampling=0)

                    converted += 1
                    dt = time.perf_counter() - t_file
                    print(f"[Save JPEG] Saved: {out_path}  ({dt:.2f}s)")
            except Exception as e:
                errors += 1
                print(f"[Save JPEG] Error converting {src}: {repr(e)}")
            root.after(0, lambda val=i: progress_var.set(val))

        dt_total = time.perf_counter() - t0
        print(f"\n[Save JPEG] Done. Converted: {converted}, Errors: {errors}, Elapsed: {dt_total:.2f}s\n")
        status_jpeg.set(f"Saved {converted} JPEG(s)")
        root.after(0, lambda: elapsed_total.set(f"Last run elapsed: {dt_total:.2f}s"))
        root.after(0, lambda: _set_busy(False))
        root.after(0, lambda: messagebox.showinfo("Done",
                    f"Converted {converted} file(s) to JPEG.\nErrors: {errors}\nElapsed: {dt_total:.2f}s"))

    ThreadPoolExecutor(max_workers=1).submit(worker)



# ===== DeepZoom (preserve your original settings) =====
# tile_size=256, tile_overlap=1, tile_format="jpg", image_quality=0.9, resize_filter="antialias" (LANCZOS)
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
    """Convert a single file to DZI using your original settings."""
    if not os.path.exists(src):
        return ("skip", src, 0.0, "not found")

    base = _basename_no_ext(src)
    dzi_path = os.path.join(outdir, f"{base}.dzi")  # guarantee .dzi
    src_uri = path_to_file_uri(src)

    start = time.perf_counter()
    creator.create(src_uri, dzi_path)
    dt = time.perf_counter() - start
    return ("ok", src, dt, dzi_path)

def create_deepzoom():
    if not HAS_DEEPZOOM:
        messagebox.showerror(
            "DeepZoom not available",
            "Install 'deepzoom' to enable DeepZoom conversion."
        )
        return
    if not input_files or not output_dir.get():
        messagebox.showerror("Missing input", "Select input image(s) and an output folder first.")
        return

    os.makedirs(output_dir.get(), exist_ok=True)

    # progress 
    total = len(input_files)
    progress_max.set(total)
    progress_var.set(0)
    elapsed_total.set("")
    _set_busy(True)


    workers = _auto_workers(input_files, output_dir.get())

    def run_parallel():
        t0 = time.perf_counter()
        created = 0
        skipped = 0
        errors = 0
        print(f"[DeepZoom] Settings: tile_size={DZ_TILE_SIZE}, overlap={DZ_OVERLAP}, "
              f"format=jpg, image_quality={DZ_IMAGE_QUALITY}, filter=LANCZOS")
        print(f"[DeepZoom] Workers (auto): {workers}\n")

        
        def job(src):
            try:
                creator = _dz_make_creator()
                return _dz_convert_one(src, output_dir.get(), creator)
            except Exception as e:
                return ("err", src, 0.0, str(e))

        done_count = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(job, src) for src in input_files]
            for fut in as_completed(futures):
                status, src, dt, info = fut.result()
                done_count += 1
                if status == "ok":
                    created += 1
                    print(f"[DeepZoom] Created: {info}  ({dt:.2f}s)")
                elif status == "skip":
                    skipped += 1
                    print(f"[DeepZoom] Skipped (not found): {src}")
                else:
                    errors += 1
                    print(f"[DeepZoom] Error: {src}\n  -> {info}")
                root.after(0, lambda val=done_count: progress_var.set(val))

        dt_total = time.perf_counter() - t0
        print(f"\n[DeepZoom] Done. Created: {created}, Skipped: {skipped}, Errors: {errors}, "
              f"Elapsed: {dt_total:.2f}s\n")
        status_dz.set(f"Created {created} DZI(s)")
        root.after(0, lambda: elapsed_total.set(f"Last run elapsed: {dt_total:.2f}s"))
        root.after(0, lambda: _set_busy(False))
        root.after(0, lambda: messagebox.showinfo(
            "DeepZoom Complete",
            f"Created: {created}\nSkipped: {skipped}\nErrors: {errors}\nElapsed: {dt_total:.2f}s"
        ))

    ThreadPoolExecutor(max_workers=1).submit(run_parallel)

# ===== Layout =====
row = 0
btn_in = tk.Button(root, text="Select Input Image(s)", command=choose_input)
btn_in.grid(row=row, column=0, padx=8, pady=6, sticky="ew")
entry_in = tk.Entry(root, textvariable=input_summary, width=70)
entry_in.grid(row=row, column=1, padx=8, pady=6, sticky="ew")
row += 1

btn_out = tk.Button(root, text="Choose Output Folder", command=choose_output)
btn_out.grid(row=row, column=0, padx=8, pady=6, sticky="ew")
entry_out = tk.Entry(root, textvariable=output_dir, width=70)
entry_out.grid(row=row, column=1, padx=8, pady=6, sticky="ew")
row += 1

btn_jpeg = tk.Button(root, text="Save JPEG(s)", command=save_jpeg)
btn_jpeg.grid(row=row, column=0, padx=8, pady=10, sticky="ew")
lbl_jpeg_text = tk.Label(root, text="Converts .tif to jpeg, for use with SEM", anchor="w")
lbl_jpeg_text.grid(row=row, column=1, padx=8, pady=10, sticky="w")
row += 1

btn_dz = tk.Button(root, text="Create DeepZoom (.dzi) for Selection", command=create_deepzoom)
btn_dz.grid(row=row, column=0, padx=8, pady=10, sticky="ew")
lbl_dz_text = tk.Label(root, text="Creates .dzi from a .jpg.", anchor="w")
lbl_dz_text.grid(row=row, column=1, padx=8, pady=10, sticky="w")
row += 1

# Progress + elapsed
ttk.Label(root, text="Progress:").grid(row=row, column=0, sticky="w", padx=8)
prog = ttk.Progressbar(root, orient="horizontal", mode="determinate",
                       maximum=progress_max.get(), variable=progress_var, length=400)
prog.grid(row=row, column=1, sticky="w", padx=8, pady=2)
def _sync_prog_max(*_):
    prog.config(maximum=progress_max.get())
progress_max.trace_add("write", _sync_prog_max)
elapsed_lbl = ttk.Label(root, textvariable=elapsed_total)
elapsed_lbl.grid(row=row, column=1, sticky="e", padx=8)
row += 1

# Console
tk.Label(root, text="Console Output:").grid(row=row, column=0, columnspan=2, sticky="w", padx=8)
row += 1
console_box = scrolledtext.ScrolledText(root, height=16, wrap='word', bg="#111", fg="#ffd700", insertbackground="#0f0")
console_box.grid(row=row, column=0, columnspan=2, padx=8, pady=6, sticky="nsew")
sys.stdout = ConsoleRedirect(console_box)

# Resizing
root.columnconfigure(0, weight=0)
root.columnconfigure(1, weight=1)
root.rowconfigure(row, weight=1)
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

root.mainloop()