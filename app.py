#!/usr/bin/env python3
import os, uuid, subprocess, json, threading
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['CONVERTED_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'converted')
app.secret_key = os.urandom(24)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CONVERTED_FOLDER'], exist_ok=True)
ALLOWED_EXTENSIONS = {'mp4','avi','mkv','mov','wmv','flv','webm','3gp','m4v','ts','mpg','mpeg'}
conversions = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def get_duration(filepath):
    try:
        cmd = ['ffprobe','-v','quiet','-print_format','json','-show_format',filepath]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return float(json.loads(r.stdout).get('format',{}).get('duration',0))
    except: pass
    return 0

def convert_video(input_path, output_path, task_id, bitrate='320k', fmt='mp3', sample_rate='44100', bit_depth='16'):
    try:
        conversions[task_id] = {'status':'converting','progress':0}
        duration = get_duration(input_path)
        codec_map = {
            'mp3': ['-acodec','libmp3lame','-ab',bitrate,'-ar',sample_rate,'-ac','2'],
            'flac': ['-acodec','flac','-ar',sample_rate,'-ac','2'],
            'wav': ['-acodec','pcm_s16le','-ar',sample_rate,'-ac','2'],
            'aac': ['-acodec','aac','-ab',bitrate,'-ar',sample_rate,'-ac','2'],
            'ogg': ['-acodec','libvorbis','-ab',bitrate,'-ar',sample_rate,'-ac','2'],
            'opus': ['-acodec','libopus','-ab',bitrate,'-ar','48000','-ac','2'],
            'm4a': ['-acodec','aac','-ab',bitrate,'-ar',sample_rate,'-ac','2'],
        }
        codec_args = codec_map.get(fmt, codec_map['mp3'])
        cmd = ['ffmpeg','-i',input_path,'-vn'] + codec_args + ['-y','-progress','pipe:1',output_path]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        for line in process.stdout:
            if 'out_time_ms=' in line:
                try:
                    t = int(line.split('=')[1].strip()) / 1000000
                    if duration > 0:
                        conversions[task_id]['progress'] = min(int((t/duration)*100),99)
                except: pass
        process.wait()
        if process.returncode == 0 and os.path.exists(output_path):
            size = os.path.getsize(output_path)
            conversions[task_id] = {'status':'completed','progress':100,'output_path':output_path,'file_size':size,'filename':os.path.basename(output_path)}
        else:
            conversions[task_id] = {'status':'error','message':'Conversion failed'}
    except Exception as e:
        conversions[task_id] = {'status':'error','message':str(e)}
    finally:
        try:
            if os.path.exists(input_path): os.remove(input_path)
        except: pass

def convert_url_to_audio(url, output_path, task_id, bitrate='320k', fmt='mp3'):
    try:
        conversions[task_id] = {'status':'downloading','progress':0}
        cmd = ['yt-dlp','--extract-audio','--audio-format',fmt,'--audio-quality','0','--output',output_path.rsplit('.',1)[0]+'.%(ext)s','--no-playlist','--newline',url]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        for line in process.stdout:
            if '%' in line:
                try:
                    p = float(line.split('%')[0].strip().split()[-1])
                    conversions[task_id]['progress'] = min(int(p),99)
                    conversions[task_id]['status'] = 'downloading' if p < 100 else 'converting'
                except: pass
        process.wait()
        actual = output_path
        if not os.path.exists(actual):
            base = output_path.rsplit('.',1)[0]
            for ext in ['.mp3','.m4a','.opus','.ogg','.flac','.wav','.webm','.aac']:
                if os.path.exists(base+ext):
                    actual = base+ext; break
        if process.returncode == 0 and os.path.exists(actual):
            conversions[task_id] = {'status':'completed','progress':100,'output_path':actual,'file_size':os.path.getsize(actual),'filename':os.path.basename(actual)}
        else:
            conversions[task_id] = {'status':'error','message':'Download failed'}
    except Exception as e:
        conversions[task_id] = {'status':'error','message':str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return jsonify({'error':'No file'}),400
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename): return jsonify({'error':'Invalid file'}),400
    bitrate = request.form.get('bitrate','320k')
    fmt = request.form.get('format','mp3')
    sample_rate = request.form.get('sample_rate','44100')
    bit_depth = request.form.get('bit_depth','16')
    task_id = str(uuid.uuid4())[:8]
    filename = secure_filename(file.filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{task_id}_{filename}')
    file.save(input_path)
    output_name = f'{task_id}_{os.path.splitext(filename)[0]}.{fmt}'
    output_path = os.path.join(app.config['CONVERTED_FOLDER'], output_name)
    thread = threading.Thread(target=convert_video, args=(input_path,output_path,task_id,bitrate,fmt,sample_rate,bit_depth))
    thread.daemon = True; thread.start()
    return jsonify({'task_id':task_id,'message':'Converting'})

@app.route('/convert-url', methods=['POST'])
def convert_url():
    data = request.get_json()
    url = data.get('url','').strip()
    if not url: return jsonify({'error':'No URL'}),400
    bitrate = data.get('bitrate','320k')
    fmt = data.get('format','mp3')
    task_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(app.config['CONVERTED_FOLDER'], f'audio_{task_id}.{fmt}')
    thread = threading.Thread(target=convert_url_to_audio, args=(url,output_path,task_id,bitrate,fmt))
    thread.daemon = True; thread.start()
    return jsonify({'task_id':task_id,'message':'Processing'})

@app.route('/progress/<task_id>')
def get_progress(task_id):
    if task_id not in conversions: return jsonify({'status':'unknown'}),404
    return jsonify(conversions[task_id])

@app.route('/download/<task_id>')
def download_file(task_id):
    if task_id not in conversions: return jsonify({'error':'Not found'}),404
    task = conversions[task_id]
    if task['status'] != 'completed': return jsonify({'error':'Not ready'}),400
    path = task['output_path']
    fname = task.get('filename', os.path.basename(path))
    if '_' in fname: fname = '_'.join(fname.split('_')[1:])
    return send_file(path, as_attachment=True, download_name=fname)

@app.route('/history')
def history():
    items = []
    for tid, task in conversions.items():
        if task['status'] == 'completed':
            items.append({'task_id':tid,'filename':task.get('filename',''),'file_size':task.get('file_size',0)})
    return jsonify(items)

@app.route('/delete/<task_id>', methods=['POST'])
def delete_file(task_id):
    if task_id in conversions:
        task = conversions[task_id]
        try:
            if 'output_path' in task and os.path.exists(task['output_path']):
                os.remove(task['output_path'])
        except: pass
        del conversions[task_id]
    return jsonify({'ok': True})

@app.route('/cleanup', methods=['POST'])
def cleanup():
    cleaned = 0
    for folder in [app.config['UPLOAD_FOLDER'], app.config['CONVERTED_FOLDER']]:
        for f in os.listdir(folder):
            try: os.remove(os.path.join(folder,f)); cleaned += 1
            except: pass
    conversions.clear()
    return jsonify({'cleaned':cleaned})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 7860))
    print(f"🎵 Video2MP3 running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
