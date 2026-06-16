#!/usr/bin/env python3
import os, uuid, subprocess, threading
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CONVERTED_FOLDER'] = 'converted'
app.secret_key = os.urandom(24)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CONVERTED_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'mp4','avi','mkv','mov','wmv','flv','webm','3gp','m4v'}
conversions = {}

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def convert_to_mp3(inp, out, tid, br='320k'):
    try:
        conversions[tid] = {'status':'converting','progress':0}
        p = subprocess.Popen(['ffmpeg','-i',inp,'-vn','-acodec','libmp3lame','-ab',br,'-ar','44100','-ac','2','-y',out], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.wait()
        if p.returncode == 0 and os.path.exists(out):
            conversions[tid] = {'status':'completed','progress':100,'output_path':out,'file_size':os.path.getsize(out)}
        else:
            conversions[tid] = {'status':'error','message':'Conversion failed'}
    except Exception as e:
        conversions[tid] = {'status':'error','message':str(e)}
    finally:
        if os.path.exists(inp):
            try: os.remove(inp)
            except: pass

def download_url(url, out, tid, br='320k', fmt='mp3'):
    try:
        conversions[tid] = {'status':'downloading','progress':0}
        cmd = [
            'yt-dlp',
            '--proxy', 'socks5://127.0.0.1:40000',
            '--extract-audio', '--audio-format', fmt, '--audio-quality', '0',
            '--no-playlist', '--ignore-errors', '--no-mtime', '--no-check-certificates',
            '--force-ipv4', '--socket-timeout', '120', '--retries', '10',
            '--fragment-retries', '10', '--retry-sleep', '3',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            '--referer', 'https://www.youtube.com/',
            '--output', out.rsplit('.',1)[0] + '.%(ext)s',
            url
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        full = ""
        for line in proc.stdout:
            full += line
            if '%' in line:
                try:
                    pct = float(line.split('%')[0].strip().split()[-1])
                    conversions[tid]['progress'] = min(int(pct), 99)
                except: pass
        proc.wait()

        actual = out
        if not os.path.exists(actual):
            base = out.rsplit('.',1)[0]
            for ext in ['.mp3','.m4a','.opus','.ogg','.flac','.wav','.aac']:
                if os.path.exists(base+ext):
                    actual = base+ext; break

        if proc.returncode == 0 and os.path.exists(actual):
            conversions[tid] = {'status':'completed','progress':100,'output_path':actual,'file_size':os.path.getsize(actual)}
        else:
            lower = full.lower()
            if "sign in to confirm" in lower or "not a bot" in lower:
                msg = "YouTube bot detection. WARP proxy may not be active. Try again."
            elif "proxy" in lower or "socks" in lower:
                msg = "WARP proxy not running. Server may need restart."
            elif "private" in lower or "unavailable" in lower:
                msg = "Video is private, removed, or region-locked."
            else:
                msg = "Download failed. Try a different video or try again."
            conversions[tid] = {'status':'error','message':msg}
    except Exception as e:
        conversions[tid] = {'status':'error','message':str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return jsonify({'error':'No file'}),400
    f = request.files['file']
    if f.filename == '' or not allowed_file(f.filename): return jsonify({'error':'Invalid file'}),400
    br = request.form.get('bitrate','320k')
    tid = str(uuid.uuid4())[:8]
    fn = secure_filename(f.filename)
    inp = os.path.join(app.config['UPLOAD_FOLDER'], f'{tid}_{fn}')
    f.save(inp)
    out = os.path.join(app.config['CONVERTED_FOLDER'], f'{tid}_{os.path.splitext(fn)[0]}.mp3')
    threading.Thread(target=convert_to_mp3, args=(inp,out,tid,br), daemon=True).start()
    return jsonify({'task_id':tid})

@app.route('/convert-url', methods=['POST'])
def convert_url_route():
    data = request.get_json()
    url = data.get('url','').strip()
    if not url: return jsonify({'error':'No URL'}),400
    br = data.get('bitrate','320k')
    fmt = data.get('format','mp3')
    tid = str(uuid.uuid4())[:8]
    out = os.path.join(app.config['CONVERTED_FOLDER'], f'audio_{tid}.{fmt}')
    threading.Thread(target=download_url, args=(url,out,tid,br,fmt), daemon=True).start()
    return jsonify({'task_id':tid})

@app.route('/progress/<tid>')
def get_progress(tid):
    return jsonify(conversions.get(tid, {'status':'unknown'}))

@app.route('/download/<tid>')
def download_file(tid):
    t = conversions.get(tid)
    if not t or t.get('status') != 'completed': return jsonify({'error':'Not ready'}),400
    return send_file(t['output_path'], as_attachment=True)

@app.route('/history')
def history():
    return jsonify([{'task_id':k,'filename':os.path.basename(v.get('output_path','')),'file_size':v.get('file_size',0)} for k,v in conversions.items() if v.get('status')=='completed'])

@app.route('/delete/<tid>', methods=['POST'])
def delete_file(tid):
    if tid in conversions:
        try:
            p = conversions[tid].get('output_path')
            if p and os.path.exists(p): os.remove(p)
        except: pass
        del conversions[tid]
    return jsonify({'ok':True})

@app.route('/cleanup', methods=['POST'])
def cleanup():
    c = 0
    for d in [app.config['UPLOAD_FOLDER'], app.config['CONVERTED_FOLDER']]:
        for f in os.listdir(d):
            try: os.remove(os.path.join(d,f)); c+=1
            except: pass
    conversions.clear()
    return jsonify({'cleaned':c})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
