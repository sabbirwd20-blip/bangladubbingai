import os, uuid, time, threading, subprocess, shutil, json
from pathlib import Path
import requests
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / 'jobs'
OUTPUTS = ROOT / 'outputs'
FRONTEND = ROOT / 'frontend'
JOBS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(FRONTEND), static_url_path='')
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_UPLOAD_MB', '3072')) * 1024 * 1024

API = 'https://api.elevenlabs.io/v1'
API_KEY = os.getenv('ELEVENLABS_API_KEY', '').strip()
MODEL = os.getenv('DUBBING_MODEL', 'dubbing_v2')
ALLOWED = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.mpeg', '.mpg'}


def job_dir(job_id):
    p = JOBS / job_id
    p.mkdir(exist_ok=True)
    return p


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def update(job_id, **changes):
    p = job_dir(job_id) / 'status.json'
    data = read_json(p) if p.exists() else {'id': job_id}
    data.update(changes)
    write_json(p, data)


def api_headers():
    return {'xi-api-key': API_KEY}


def api_error(message, status=500, code='error'):
    return jsonify({'ok': False, 'error': str(message), 'code': code}), status


@app.after_request
def cors(response):
    response.headers['Access-Control-Allow-Origin'] = os.getenv('CORS_ORIGIN', '*')
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
    response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition,Content-Length'
    return response


@app.get('/api/health')
def health():
    return jsonify({
        'ok': True,
        'api_key_configured': bool(API_KEY),
        'ffmpeg': bool(shutil.which('ffmpeg')),
        'model': MODEL,
        'database_required': False
    })


@app.route('/api/jobs', methods=['POST', 'OPTIONS'])
def create_job():
    if request.method == 'OPTIONS':
        return ('', 204)
    if not API_KEY:
        return api_error('Backend-এ ELEVENLABS_API_KEY সেট করা হয়নি।', 500, 'missing_api_key')
    if 'video' not in request.files:
        return api_error('ভিডিও ফাইল দিন।', 400, 'missing_video')

    video = request.files['video']
    ext = Path(secure_filename(video.filename or '')).suffix.lower()
    if ext not in ALLOWED:
        return api_error('MP4/MOV/MKV/WebM/AVI/M4V/MPEG ভিডিও দিন।', 400, 'unsupported_format')

    if not shutil.which('ffmpeg'):
        return api_error('Server-এ FFmpeg পাওয়া যায়নি। Docker deployment ব্যবহার করুন।', 500, 'ffmpeg_missing')

    job_id = uuid.uuid4().hex
    folder = job_dir(job_id)
    source = folder / ('source' + ext)
    video.save(source)
    write_json(folder / 'status.json', {
        'id': job_id,
        'status': 'queued',
        'progress': 5,
        'message': 'কাজ শুরু হচ্ছে…'
    })

    source_language = (request.form.get('source_language') or 'auto').strip()
    threading.Thread(target=create_dub, args=(job_id, source, source_language), daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id}), 202


def create_dub(job_id, source, source_language):
    folder = job_dir(job_id)
    try:
        update(job_id, status='uploading', progress=10, message='ভিডিও AI সার্ভারে পাঠানো হচ্ছে…')

        data = {
            'reference': f'BanglaDub AI {job_id}',
            'model_id': MODEL,
            'target_language': 'bn'
        }
        if source_language and source_language != 'auto':
            data['source_language'] = source_language

        with source.open('rb') as fh:
            response = requests.post(
                API + '/dubbing/project',
                headers=api_headers(),
                data=data,
                files={'file': (source.name, fh, 'application/octet-stream')},
                timeout=600
            )
        if not response.ok:
            raise RuntimeError(f'ElevenLabs project error {response.status_code}: {response.text[:1200]}')

        project = response.json()
        project_id = project.get('project_id')
        language_ids = project.get('language_ids') or []
        if not project_id:
            raise RuntimeError('ElevenLabs project_id পাওয়া যায়নি।')

        update(job_id, project_id=project_id, status='preparing', progress=20, message='ভিডিও বিশ্লেষণ হচ্ছে…')

        # The create-project endpoint can queue the first target directly.
        # Wait for source preparation. Official statuses include queued, preparing, ready, failed.
        for n in range(360):
            r = requests.get(f'{API}/dubbing/project/{project_id}', headers=api_headers(), timeout=60)
            if not r.ok:
                raise RuntimeError(f'Project status error {r.status_code}: {r.text[:800]}')
            project_status = r.json()
            state = project_status.get('status')
            if state == 'failed':
                err = project_status.get('error')
                detail = err.get('message') if isinstance(err, dict) else err
                raise RuntimeError(detail or 'ভিডিও প্রস্তুত করা ব্যর্থ হয়েছে।')
            if state == 'ready':
                break
            update(job_id, status='preparing', progress=min(40, 20 + n // 8), message=f'ভিডিও বিশ্লেষণ হচ্ছে… ({state or "queued"})')
            time.sleep(5)
        else:
            raise RuntimeError('ভিডিও প্রস্তুত হতে অতিরিক্ত সময় লাগছে।')

        # target_language on create normally gives us the language id.
        language_id = language_ids[0] if language_ids else None
        if not language_id:
            lr = requests.post(
                f'{API}/dubbing/project/{project_id}/language',
                headers={**api_headers(), 'Content-Type': 'application/json'},
                json={'target_language': 'bn'},
                timeout=60
            )
            if not lr.ok:
                raise RuntimeError(f'Bengali target error {lr.status_code}: {lr.text[:1000]}')
            language_id = lr.json().get('language_id')
        if not language_id:
            raise RuntimeError('Bengali language_id পাওয়া যায়নি।')

        update(job_id, language_id=language_id, status='dubbing', progress=45, message='বাংলা voice তৈরি হচ্ছে…')

        for n in range(720):
            lr = requests.get(
                f'{API}/dubbing/project/{project_id}/language/{language_id}',
                headers=api_headers(), timeout=60
            )
            if not lr.ok:
                raise RuntimeError(f'Dub status error {lr.status_code}: {lr.text[:1000]}')
            language = lr.json()
            state = language.get('status')
            if state == 'failed':
                err = language.get('error')
                detail = err.get('message') if isinstance(err, dict) else err
                raise RuntimeError(detail or 'বাংলা dubbing তৈরি ব্যর্থ হয়েছে।')
            if state == 'completed':
                break
            update(job_id, status='dubbing', progress=min(82, 45 + n // 10), message=f'বাংলা dubbing তৈরি হচ্ছে… ({state or "queued"})')
            time.sleep(5)
        else:
            raise RuntimeError('Dubbing সম্পন্ন হতে অতিরিক্ত সময় লাগছে।')

        audio_url = (language.get('outputs') or {}).get('lossless_audio')
        if not audio_url:
            raise RuntimeError('Dubbing সম্পন্ন হলেও audio URL পাওয়া যায়নি।')

        audio = folder / 'dubbed.flac'
        update(job_id, status='downloading', progress=85, message='বাংলা audio নেওয়া হচ্ছে…')
        ar = requests.get(audio_url, timeout=600)
        ar.raise_for_status()
        audio.write_bytes(ar.content)

        output = OUTPUTS / f'{job_id}.mp4'
        update(job_id, status='rendering', progress=92, message='ভিডিওর সঙ্গে বাংলা audio যুক্ত হচ্ছে…')
        ffmpeg = shutil.which('ffmpeg')
        cmd = [
            ffmpeg, '-y', '-i', str(source), '-i', str(audio),
            '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-shortest', str(output)
        ]
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3600)
        if process.returncode != 0:
            raise RuntimeError('FFmpeg render failed: ' + process.stderr[-2500:])
        if not output.exists() or output.stat().st_size < 1024:
            raise RuntimeError('MP4 output তৈরি হয়নি।')

        update(job_id, status='completed', progress=100, message='বাংলা ভিডিও প্রস্তুত!', download=f'/api/jobs/{job_id}/download')

    except Exception as exc:
        update(job_id, status='error', progress=0, message=str(exc))
    finally:
        # Keep status.json for the client; remove temporary upload/audio files.
        try:
            if folder.exists():
                for item in folder.iterdir():
                    if item.name != 'status.json':
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink(missing_ok=True)
        except Exception:
            pass


@app.get('/api/jobs/<job_id>')
def job_status(job_id):
    status_file = JOBS / job_id / 'status.json'
    output = OUTPUTS / f'{job_id}.mp4'
    if status_file.exists():
        data = read_json(status_file)
        if data.get('status') == 'completed' and not output.exists():
            data.update(status='error', progress=0, message='ভিডিওটি server storage থেকে পাওয়া যাচ্ছে না। আবার চেষ্টা করুন।')
        return jsonify(data)
    if output.exists():
        return jsonify({'ok': True, 'id': job_id, 'status': 'completed', 'progress': 100, 'message': 'বাংলা ভিডিও প্রস্তুত!', 'download': f'/api/jobs/{job_id}/download'})
    return api_error('Job পাওয়া যায়নি।', 404, 'job_not_found')


@app.get('/api/jobs/<job_id>/download')
def download(job_id):
    output = OUTPUTS / f'{job_id}.mp4'
    if not output.exists():
        return api_error('ভিডিও এখনও প্রস্তুত হয়নি বা server থেকে মুছে গেছে।', 404, 'file_not_found')
    return send_from_directory(str(OUTPUTS), output.name, as_attachment=True, download_name='BanglaDubAI_Bengali.mp4', mimetype='video/mp4')


@app.get('/')
def index():
    return send_from_directory(FRONTEND, 'index.html')


@app.get('/<path:path>')
def static_files(path):
    candidate = FRONTEND / path
    if candidate.is_file():
        return send_from_directory(FRONTEND, path)
    return send_from_directory(FRONTEND, 'index.html')


@app.errorhandler(404)
def not_found(_):
    return api_error('API endpoint পাওয়া যায়নি।', 404, 'not_found')


@app.errorhandler(413)
def too_large(_):
    return api_error('ভিডিও ফাইলের আকার সীমার বেশি।', 413, 'file_too_large')


@app.errorhandler(500)
def server_error(_):
    return api_error('Server error হয়েছে।', 500, 'server_error')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')))
