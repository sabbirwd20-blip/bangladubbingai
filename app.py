import os
import uuid
import time
import threading
import subprocess
import shutil
import json
from pathlib import Path

import requests
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename


# =========================================================
# BanglaDub AI Backend
# Bengali AI Video Dubbing
# =========================================================

ROOT = Path(__file__).resolve().parent

JOBS = ROOT / "jobs"
OUTPUTS = ROOT / "outputs"
FRONTEND = ROOT / "frontend"

JOBS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = Flask(
    __name__,
    static_folder=str(FRONTEND),
    static_url_path=""
)

# Maximum upload size
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "3072"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# =========================================================
# ElevenLabs Configuration
# =========================================================

API = "https://api.elevenlabs.io/v1"

API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()

# IMPORTANT:
# Bengali (bn) is supported by Dubbing v1,
# but not by Dubbing v2.
#
# BanglaDub AI is a Bengali dubbing app,
# therefore Bengali jobs always use Dubbing v1.
MODEL = "dubbing_v1"

TARGET_LANGUAGE = "bn"


# =========================================================
# Supported video formats
# =========================================================

ALLOWED = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".mpeg",
    ".mpg"
}


# =========================================================
# Utility functions
# =========================================================

def job_dir(job_id):
    folder = JOBS / job_id
    folder.mkdir(exist_ok=True)
    return folder


def write_json(path, data):
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def read_json(path):
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def update(job_id, **changes):
    path = job_dir(job_id) / "status.json"

    if path.exists():
        data = read_json(path)
    else:
        data = {
            "id": job_id
        }

    data.update(changes)

    write_json(path, data)


def api_headers():
    return {
        "xi-api-key": API_KEY
    }


def api_error(message, status=500, code="error"):
    return jsonify({
        "ok": False,
        "error": str(message),
        "code": code
    }), status


# =========================================================
# CORS
# =========================================================

@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"] = os.getenv(
        "CORS_ORIGIN",
        "*"
    )

    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type"
    )

    response.headers["Access-Control-Allow-Methods"] = (
        "GET,POST,OPTIONS"
    )

    response.headers["Access-Control-Expose-Headers"] = (
        "Content-Disposition,Content-Length"
    )

    return response


# =========================================================
# Health Check
# =========================================================

@app.get("/api/health")
def health():

    return jsonify({
        "ok": True,
        "api_key_configured": bool(API_KEY),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "model": MODEL,
        "target_language": TARGET_LANGUAGE,
        "database_required": False
    })


# =========================================================
# Create Job
# =========================================================

@app.route("/api/jobs", methods=["POST", "OPTIONS"])
def create_job():

    if request.method == "OPTIONS":
        return ("", 204)

    # API key check
    if not API_KEY:
        return api_error(
            "Backend-এ ELEVENLABS_API_KEY সেট করা হয়নি।",
            500,
            "missing_api_key"
        )

    # Video check
    if "video" not in request.files:
        return api_error(
            "ভিডিও ফাইল দিন।",
            400,
            "missing_video"
        )

    video = request.files["video"]

    filename = secure_filename(
        video.filename or ""
    )

    ext = Path(filename).suffix.lower()

    if ext not in ALLOWED:
        return api_error(
            "MP4/MOV/MKV/WebM/AVI/M4V/MPEG ভিডিও দিন।",
            400,
            "unsupported_format"
        )

    # FFmpeg check
    if not shutil.which("ffmpeg"):
        return api_error(
            "Server-এ FFmpeg পাওয়া যায়নি। Docker deployment ব্যবহার করুন।",
            500,
            "ffmpeg_missing"
        )

    # Create job ID
    job_id = uuid.uuid4().hex

    folder = job_dir(job_id)

    source = folder / (
        "source" + ext
    )

    # Save uploaded video
    video.save(source)

    # Initial status
    write_json(
        folder / "status.json",
        {
            "id": job_id,
            "status": "queued",
            "progress": 5,
            "message": "কাজ শুরু হচ্ছে…"
        }
    )

    # Source language
    source_language = (
        request.form.get("source_language")
        or "auto"
    ).strip()

    # Start background job
    thread = threading.Thread(
        target=create_dub,
        args=(
            job_id,
            source,
            source_language
        ),
        daemon=True
    )

    thread.start()

    return jsonify({
        "ok": True,
        "job_id": job_id
    }), 202


# =========================================================
# Main Dubbing Process
# =========================================================

def create_dub(
    job_id,
    source,
    source_language
):

    folder = job_dir(job_id)

    try:

        # -------------------------------------------------
        # STEP 1 — Uploading
        # -------------------------------------------------

        update(
            job_id,
            status="uploading",
            progress=10,
            message="ভিডিও AI সার্ভারে পাঠানো হচ্ছে…"
        )

        # -------------------------------------------------
        # STEP 2 — Create ElevenLabs Dubbing Project
        # -------------------------------------------------

        data = {
            "reference": (
                f"BanglaDub AI {job_id}"
            ),

            # IMPORTANT:
            # Bengali requires Dubbing v1
            "model_id": MODEL,

            # Bengali
            "target_language": TARGET_LANGUAGE
        }

        # If user selected a source language
        # other than Auto
        if (
            source_language
            and source_language != "auto"
        ):
            data["source_language"] = (
                source_language
            )

        # Upload source video
        with source.open("rb") as fh:

            response = requests.post(
                API + "/dubbing/project",
                headers=api_headers(),
                data=data,
                files={
                    "file": (
                        source.name,
                        fh,
                        "application/octet-stream"
                    )
                },
                timeout=600
            )

        # API error
        if not response.ok:

            raise RuntimeError(
                "ElevenLabs project error "
                f"{response.status_code}: "
                f"{response.text[:2000]}"
            )

        project = response.json()

        project_id = project.get(
            "project_id"
        )

        language_ids = (
            project.get("language_ids")
            or []
        )

        if not project_id:

            raise RuntimeError(
                "ElevenLabs project_id পাওয়া যায়নি।"
            )

        update(
            job_id,
            project_id=project_id,
            status="preparing",
            progress=20,
            message="ভিডিও বিশ্লেষণ হচ্ছে…"
        )

        # -------------------------------------------------
        # STEP 3 — Wait for Project Ready
        # -------------------------------------------------

        language_id = (
            language_ids[0]
            if language_ids
            else None
        )

        for n in range(360):

            response = requests.get(
                f"{API}/dubbing/project/{project_id}",
                headers=api_headers(),
                timeout=60
            )

            if not response.ok:

                raise RuntimeError(
                    "Project status error "
                    f"{response.status_code}: "
                    f"{response.text[:1200]}"
                )

            project_status = response.json()

            state = project_status.get(
                "status"
            )

            # Project failed
            if state == "failed":

                err = project_status.get(
                    "error"
                )

                if isinstance(err, dict):
                    detail = err.get(
                        "message"
                    )
                else:
                    detail = err

                raise RuntimeError(
                    detail
                    or "ভিডিও প্রস্তুত করা ব্যর্থ হয়েছে।"
                )

            # Ready
            if state == "ready":
                break

            progress = min(
                40,
                20 + n // 8
            )

            update(
                job_id,
                status="preparing",
                progress=progress,
                message=(
                    "ভিডিও বিশ্লেষণ হচ্ছে… "
                    f"({state or 'queued'})"
                )
            )

            time.sleep(5)

        else:

            raise RuntimeError(
                "ভিডিও প্রস্তুত হতে অতিরিক্ত সময় লাগছে।"
            )

        # -------------------------------------------------
        # STEP 4 — Create Bengali Target if Needed
        # -------------------------------------------------

        if not language_id:

            language_response = requests.post(

                f"{API}/dubbing/project/"
                f"{project_id}/language",

                headers={
                    **api_headers(),
                    "Content-Type":
                        "application/json"
                },

                json={
                    "target_language":
                        TARGET_LANGUAGE
                },

                timeout=60
            )

            if not language_response.ok:

                raise RuntimeError(
                    "Bengali target error "
                    f"{language_response.status_code}: "
                    f"{language_response.text[:1500]}"
                )

            language_data = (
                language_response.json()
            )

            language_id = (
                language_data.get(
                    "language_id"
                )
            )

        if not language_id:

            raise RuntimeError(
                "Bengali language_id পাওয়া যায়নি।"
            )

        update(
            job_id,
            language_id=language_id,
            status="dubbing",
            progress=45,
            message="বাংলা voice তৈরি হচ্ছে…"
        )

        # -------------------------------------------------
        # STEP 5 — Wait for Bengali Dubbing
        # -------------------------------------------------

        language = None

        for n in range(720):

            language_response = requests.get(

                f"{API}/dubbing/project/"
                f"{project_id}/language/"
                f"{language_id}",

                headers=api_headers(),

                timeout=60
            )

            if not language_response.ok:

                raise RuntimeError(
                    "Dub status error "
                    f"{language_response.status_code}: "
                    f"{language_response.text[:1500]}"
                )

            language = (
                language_response.json()
            )

            state = language.get(
                "status"
            )

            # Dubbing failed
            if state == "failed":

                err = language.get(
                    "error"
                )

                if isinstance(err, dict):

                    detail = (
                        err.get("message")
                        or err.get("detail")
                    )

                else:
                    detail = err

                raise RuntimeError(
                    detail
                    or "বাংলা dubbing তৈরি ব্যর্থ হয়েছে।"
                )

            # Dubbing completed
            if state == "completed":
                break

            progress = min(
                82,
                45 + n // 10
            )

            update(
                job_id,
                status="dubbing",
                progress=progress,
                message=(
                    "বাংলা dubbing তৈরি হচ্ছে… "
                    f"({state or 'queued'})"
                )
            )

            time.sleep(5)

        else:

            raise RuntimeError(
                "Dubbing সম্পন্ন হতে অতিরিক্ত সময় লাগছে।"
            )

        # -------------------------------------------------
        # STEP 6 — Get Audio URL
        # -------------------------------------------------

        outputs = (
            language.get("outputs")
            or {}
        )

        audio_url = outputs.get(
            "lossless_audio"
        )

        if not audio_url:

            raise RuntimeError(
                "Dubbing সম্পন্ন হলেও "
                "audio URL পাওয়া যায়নি।"
            )

        # -------------------------------------------------
        # STEP 7 — Download Bengali Audio
        # -------------------------------------------------

        audio = folder / "dubbed.flac"

        update(
            job_id,
            status="downloading",
            progress=85,
            message="বাংলা audio নেওয়া হচ্ছে…"
        )

        audio_response = requests.get(
            audio_url,
            timeout=600
        )

        audio_response.raise_for_status()

        audio.write_bytes(
            audio_response.content
        )

        if not audio.exists() or audio.stat().st_size < 1024:

            raise RuntimeError(
                "বাংলা audio download করা যায়নি।"
            )

        # -------------------------------------------------
        # STEP 8 — FFmpeg Render
        # -------------------------------------------------

        output = (
            OUTPUTS /
            f"{job_id}.mp4"
        )

        update(
            job_id,
            status="rendering",
            progress=92,
            message=(
                "ভিডিওর সঙ্গে বাংলা audio "
                "যুক্ত হচ্ছে…"
            )
        )

        ffmpeg = shutil.which(
            "ffmpeg"
        )

        if not ffmpeg:

            raise RuntimeError(
                "FFmpeg পাওয়া যায়নি।"
            )

        # Replace original audio with Bengali audio
        #
        # Video stream is copied without re-encoding.
        # Audio is encoded to AAC.
        #
        # shortest prevents unwanted extra duration.

        command = [

            ffmpeg,

            "-y",

            "-i",
            str(source),

            "-i",
            str(audio),

            "-map",
            "0:v:0",

            "-map",
            "1:a:0",

            "-c:v",
            "copy",

            "-c:a",
            "aac",

            "-b:a",
            "192k",

            "-shortest",

            str(output)
        ]

        process = subprocess.run(

            command,

            stdout=subprocess.PIPE,

            stderr=subprocess.PIPE,

            text=True,

            timeout=3600
        )

        if process.returncode != 0:

            raise RuntimeError(
                "FFmpeg render failed:\n"
                + process.stderr[-4000:]
            )

        # Check output
        if (
            not output.exists()
            or output.stat().st_size < 1024
        ):

            raise RuntimeError(
                "MP4 output তৈরি হয়নি।"
            )

        # -------------------------------------------------
        # STEP 9 — Completed
        # -------------------------------------------------

        update(
            job_id,

            status="completed",

            progress=100,

            message="বাংলা ভিডিও প্রস্তুত!",

            download=(
                f"/api/jobs/{job_id}/download"
            )
        )

    except Exception as exc:

        # Save readable error
        update(
            job_id,
            status="error",
            progress=0,
            message=str(exc)
        )

    finally:

        # -------------------------------------------------
        # Cleanup temporary files
        # -------------------------------------------------

        try:

            if folder.exists():

                for item in folder.iterdir():

                    if item.name == "status.json":
                        continue

                    if item.is_dir():

                        shutil.rmtree(
                            item,
                            ignore_errors=True
                        )

                    else:

                        item.unlink(
                            missing_ok=True
                        )

        except Exception:
            pass


# =========================================================
# Job Status
# =========================================================

@app.get("/api/jobs/<job_id>")
def job_status(job_id):

    status_file = (
        JOBS /
        job_id /
        "status.json"
    )

    output = (
        OUTPUTS /
        f"{job_id}.mp4"
    )

    # Status exists
    if status_file.exists():

        data = read_json(
            status_file
        )

        # Completed but output missing
        if (
            data.get("status")
            == "completed"
            and not output.exists()
        ):

            data.update(
                status="error",
                progress=0,
                message=(
                    "ভিডিওটি server storage "
                    "থেকে পাওয়া যাচ্ছে না। "
                    "আবার চেষ্টা করুন।"
                )
            )

        return jsonify(data)

    # Output exists
    if output.exists():

        return jsonify({

            "ok": True,

            "id": job_id,

            "status": "completed",

            "progress": 100,

            "message":
                "বাংলা ভিডিও প্রস্তুত!",

            "download":
                f"/api/jobs/{job_id}/download"

        })

    return api_error(
        "Job পাওয়া যায়নি।",
        404,
        "job_not_found"
    )


# =========================================================
# Download Final MP4
# =========================================================

@app.get("/api/jobs/<job_id>/download")
def download(job_id):

    output = (
        OUTPUTS /
        f"{job_id}.mp4"
    )

    if not output.exists():

        return api_error(
            "ভিডিও এখনও প্রস্তুত হয়নি "
            "বা server থেকে মুছে গেছে।",
            404,
            "file_not_found"
        )

    return send_from_directory(

        str(OUTPUTS),

        output.name,

        as_attachment=True,

        download_name=
            "BanglaDubAI_Bengali.mp4",

        mimetype="video/mp4"
    )


# =========================================================
# Frontend
# =========================================================

@app.get("/")
def index():

    return send_from_directory(
        FRONTEND,
        "index.html"
    )


@app.get("/<path:path>")
def static_files(path):

    candidate = FRONTEND / path

    if candidate.is_file():

        return send_from_directory(
            FRONTEND,
            path
        )

    return send_from_directory(
        FRONTEND,
        "index.html"
    )


# =========================================================
# Error Handlers
# =========================================================

@app.errorhandler(404)
def not_found(_):

    return api_error(
        "API endpoint পাওয়া যায়নি।",
        404,
        "not_found"
    )


@app.errorhandler(413)
def too_large(_):

    return api_error(
        "ভিডিও ফাইলের আকার সীমার বেশি।",
        413,
        "file_too_large"
    )


@app.errorhandler(500)
def server_error(_):

    return api_error(
        "Server error হয়েছে।",
        500,
        "server_error"
    )


# =========================================================
# Local Development
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "5000"
            )
        )
    )
