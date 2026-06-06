import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, request, render_template, send_file, after_this_request
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB upload limit

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def natural_key(text: str):
    """
    300x250-2.jpg dosyasını 300x250-10.jpg'den önce sıralar.
    """
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def parse_durations(value: str):
    """
    Tek değer: 2.5
    Çoklu değer: 2,3,2.5
    Çoklu değer girilirse frame sırasına göre uygulanır.
    Liste görsel sayısından kısa kalırsa son değer tekrar edilir.
    """
    if not value:
        return [2.0]

    cleaned = value.replace(";", ",").replace(" ", "")
    durations = []

    for item in cleaned.split(","):
        if not item:
            continue
        try:
            seconds = float(item.replace(",", "."))
        except ValueError:
            continue

        if seconds <= 0:
            continue

        durations.append(seconds)

    return durations or [2.0]


def duration_for_index(durations, index: int):
    if index < len(durations):
        return durations[index]
    return durations[-1]


def image_dimensions(path: Path):
    with Image.open(path) as img:
        return img.size


def normalize_to_png(input_path: Path, output_path: Path):
    """
    EXIF yönünü düzeltir, görseli kayıpsız PNG olarak ara dosyaya çevirir.
    Böylece GIF üretiminde boyut/orientation sürprizi olmaz.
    """
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        img.save(output_path, "PNG")


def run_ffmpeg_concat(frames, durations, output_gif: Path):
    """
    FFmpeg palettegen + paletteuse yöntemi:
    GIF formatının 256 renk sınırı içinde alınabilecek en temiz sonucu verir.
    """
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("FFmpeg bulunamadı. Server ortamında ffmpeg kurulu olmalı.")

    workdir = output_gif.parent / f"frames_{output_gif.stem}"
    workdir.mkdir(parents=True, exist_ok=True)

    normalized_frames = []
    for i, frame_path in enumerate(frames, start=1):
        png_path = workdir / f"frame_{i:05d}.png"
        normalize_to_png(frame_path, png_path)
        normalized_frames.append(png_path)

    concat_file = workdir / "frames.txt"
    with concat_file.open("w", encoding="utf-8") as f:
        for i, frame in enumerate(normalized_frames):
            safe_path = frame.as_posix().replace("'", r"'\''")
            f.write(f"file '{safe_path}'\n")
            f.write(f"duration {duration_for_index(durations, i)}\n")

        # FFmpeg concat demuxer'da son frame süresinin uygulanması için son dosya tekrar yazılır.
        last_path = normalized_frames[-1].as_posix().replace("'", r"'\''")
        f.write(f"file '{last_path}'\n")

    command = [
        ffmpeg_path,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-filter_complex",
        "split[s0][s1];[s0]palettegen=max_colors=256:stats_mode=diff[p];[s1][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle",
        "-loop", "0",
        str(output_gif),
    ]

    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/make-gif", methods=["POST"])
def make_gif():
    uploaded_files = request.files.getlist("images")
    durations = parse_durations(request.form.get("durations", "2"))
    only_multiple = request.form.get("only_multiple", "on") == "on"

    if not uploaded_files:
        return render_template("index.html", error="Görsel seçilmedi.")

    job_dir = Path(tempfile.mkdtemp(prefix="gif_job_"))
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    grouped = {}
    skipped = []
    total_saved = 0

    try:
        for idx, file in enumerate(uploaded_files, start=1):
            original_name = file.filename or f"image_{idx}"
            suffix = Path(original_name).suffix.lower()

            if suffix not in ALLOWED_EXTENSIONS:
                skipped.append(f"{original_name} | desteklenmeyen format")
                continue

            clean_name = secure_filename(Path(original_name).name)
            if not clean_name:
                clean_name = f"image_{idx}{suffix}"

            saved_path = input_dir / f"{idx:05d}_{clean_name}"
            file.save(saved_path)

            try:
                dimensions = image_dimensions(saved_path)
            except (UnidentifiedImageError, OSError):
                skipped.append(f"{original_name} | okunamadı")
                saved_path.unlink(missing_ok=True)
                continue

            grouped.setdefault(dimensions, []).append(
                {
                    "path": saved_path,
                    "original_name": Path(original_name).name,
                    "dimensions": dimensions,
                }
            )
            total_saved += 1

        if not grouped:
            return render_template("index.html", error="İşlenebilir görsel bulunamadı.")

        manifest_lines = []
        created_gifs = []

        for (width, height), items in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
            if only_multiple and len(items) < 2:
                skipped.append(f"{width}x{height} | tek görsel olduğu için GIF yapılmadı")
                continue

            items_sorted = sorted(items, key=lambda item: natural_key(item["original_name"]))
            frame_paths = [item["path"] for item in items_sorted]

            output_gif = output_dir / f"{width}x{height}.gif"
            run_ffmpeg_concat(frame_paths, durations, output_gif)
            created_gifs.append(output_gif)

            manifest_lines.append(f"{output_gif.name}")
            manifest_lines.append(f"Boyut: {width}x{height}")
            manifest_lines.append(f"Görsel adedi: {len(items_sorted)}")
            manifest_lines.append("Sıralama:")
            for item in items_sorted:
                manifest_lines.append(f" - {item['original_name']}")
            manifest_lines.append("")

        if not created_gifs:
            return render_template(
                "index.html",
                error="GIF üretilemedi. Tek görseller atlandıysa 'tek görselli grupları da üret' seçeneğini deneyebilirsin.",
            )

        if skipped:
            manifest_lines.append("Atlananlar:")
            manifest_lines.extend([f" - {line}" for line in skipped])

        manifest_path = output_dir / "manifest.txt"
        manifest_path.write_text("\n".join(manifest_lines), encoding="utf-8")

        zip_path = job_dir / "gif_outputs.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for gif in created_gifs:
                zf.write(gif, arcname=gif.name)
            zf.write(manifest_path, arcname="manifest.txt")

        @after_this_request
        def cleanup(response):
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
            except Exception:
                pass
            return response

        return send_file(
            zip_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name="gif_outputs.zip",
        )

    except Exception as exc:
        shutil.rmtree(job_dir, ignore_errors=True)
        return render_template("index.html", error=str(exc))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
