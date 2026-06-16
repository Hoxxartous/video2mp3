#!/usr/bin/env python3
import os, uuid, subprocess, json, threading, re, time
import requests as req
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = '/etc/ssl/certs/ca-certificates.crt'
os.environ['SSL_CERT_FILE'] = '/etc/ssl/certs/ca-certificates.crt'
os.environ['PYTHONHTTPSVERIFY'] = '0'

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['CONVERTED_FOLDER'] = os.path.join(BASE_DIR, 'converted')
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

def convert_audio(input_path, output_path, task_id, bitrate='320k', fmt='mp3', sample_rate='44100'):
    """Convert any audio/video file to target format using ffmpeg."""
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
            conversions[task_id] = {'status':'completed','progress':100,'output_path':output_path,'file_size':os.path.getsize(output_path),'filename':os.path.basename(output_path)}
        else:
            conversions[task_id] = {'status':'error','message':'Conversion failed'}
    except Exception as e:
        conversions[task_id] = {'status':'error','message':str(e)}
    finally:
        try:
            if os.path.exists(input_path): os.remove(input_path)
        except: pass

def download_file(url, save_path, task_id):
    """Download a file with progress tracking."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': '*/*',
        }
        r = req.get(url, headers=headers, stream=True, timeout=120, verify=False)
        total = int(r.headers.get('content-length', 0))
        downloaded = 0
        with open(save_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024*64):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = min(int((downloaded / total) * 95), 95)
                        conversions[task_id]['progress'] = pct
        
        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            print(f"[download] OK: {os.path.getsize(save_path)} bytes", flush=True)
            return True
        return False
    except Exception as e:
        print(f"[download] Error: {e}", flush=True)
        return False

def get_cobalt_url(video_url, audio_only=True):
    """Use Cobalt API to get direct download URL."""
    try:
        cobalt_urls = [
            'https://api.cobalt.tools/',
            'https://cobalt-api.hyper.lol/',
            'https://api.cobalt.best/',
        ]
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Video2MP3/1.0',
        }
        body = {
            'url': video_url,
            'downloadMode': 'audio' if audio_only else 'auto',
            'audioFormat': 'mp3',
        }
        
        for cobalt_url in cobalt_urls:
            try:
                print(f"[cobalt] Trying: {cobalt_url}", flush=True)
                r = req.post(cobalt_url, json=body, headers=headers, timeout=30, verify=False)
                print(f"[cobalt] Status: {r.status_code}", flush=True)
                print(f"[cobalt] Response: {r.text[:500]}", flush=True)
                
                if r.status_code == 200:
                    data = r.json()
                    # Cobalt returns either 'url' or 'streamUrl' or 'urls'
                    download_url = data.get('url') or data.get('streamUrl')
                    if not download_url and 'urls' in data and data['urls']:
                        download_url = data['urls'][0] if isinstance(data['urls'], list) else data['urls']
                    if download_url:
                        print(f"[cobalt] Got URL: {download_url[:100]}...", flush=True)
                        return download_url
            except Exception as e:
                print(f"[cobalt] Failed: {e}", flush=True)
                continue
        return None
    except Exception as e:
        print(f"[cobalt] Error: {e}", flush=True)
        return None

def get_youtube_native_url(video_url):
    """Try to get YouTube direct URL using Invidious instances."""
    try:
        # Extract video ID
        video_id = None
        patterns = [
            r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'(?:embed/)([a-zA-Z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, video_url)
            if match:
                video_id = match.group(1)
                break
        
        if not video_id:
            return None
        
        print(f"[invidious] Video ID: {video_id}", flush=True)
        
        # Try multiple Invidious instances
        instances = [
            f'https://vid.puffyan.us/api/v1/videos/{video_id}',
            f'https://invidious.fdn.fr/api/v1/videos/{video_id}',
            f'https://yt.artemislena.eu/api/v1/videos/{video_id}',
            f'https://invidious.nerdvpn.de/api/v1/videos/{video_id}',
            f'https://inv.nadeko.net/api/v1/videos/{video_id}',
        ]
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        }
        
        for instance_url in instances:
            try:
                print(f"[invidious] Trying: {instance_url}", flush=True)
                r = req.get(instance_url, headers=headers, timeout=15, verify=False)
                if r.status_code == 200:
                    data = r.json()
                    # Get adaptive formats (audio only is better quality)
                    formats = data.get('adaptiveFormats', [])
                    
                    # Find best audio format
                    best_audio = None
                    for fmt in formats:
                        if fmt.get('type','').startswith('audio/'):
                            if best_audio is None or fmt.get('bitrate',0) > best_audio.get('bitrate',0):
                                best_audio = fmt
                    
                    if best_audio and 'url' in best_audio:
                        print(f"[invidious] Got audio URL: {best_audio['url'][:100]}...", flush=True)
                        return best_audio['url']
                    
                    # Fallback: get format streams (has audio+video)
                    fmts = data.get('formatStreams', [])
                    if fmts:
                        print(f"[invidious] Got stream URL: {fmts[-1]['url'][:100]}...", flush=True)
                        return fmts[-1]['url']
            except Exception as e:
                print(f"[invidious] Failed: {e}", flush=True)
                continue
        return None
    except Exception as e:
        print(f"[invidious] Error: {e}", flush=True)
        return None

def get_twitter_native_url(tweet_url):
    """Try to get Twitter direct URL using alternative APIs."""
    try:
        apis = [
            f'https://api.fxtwitter.com/status/{tweet_url.split("/")[-1].split("?")[0]}',
        ]
        
        # Extract tweet ID
        match = re.search(r'(?:status|statuses)/(\d+)', tweet_url)
        if not match:
            return None
        tweet_id = match.group(1)
        
        print(f"[twitter] Tweet ID: {tweet_id}", flush=True)
        
        # Try fxtwitter/vxtwitter
        fix_apis = [
            f'https://api.fxtwitter.com/status/{tweet_id}',
            f'https://api.vxtwitter.com/status/{tweet_id}',
        ]
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        }
        
        for api_url in fix_apis:
            try:
                print(f"[twitter] Trying: {api_url}", flush=True)
                r = req.get(api_url, headers=headers, timeout=15, verify=False)
                if r.status_code == 200:
                    data = r.json()
                    tweet = data.get('tweet', data)
                    
                    # Check for media
                    media = tweet.get('media', {})
                    videos = media.get('videos', [])
                    
                    if videos:
                        best = videos[0]
                        video_url = best.get('url')
                        if video_url:
                            print(f"[twitter] Got URL: {video_url[:100]}...", flush=True)
                            return video_url
            except Exception as e:
                print(f"[twitter] Failed: {e}", flush=True)
                continue
        return None
    except Exception as e:
        print(f"[twitter] Error: {e}", flush=True)
        return None

def is_youtube(url):
    return bool(re.search(r'(youtube\.com|youtu\.be)', url))

def is_twitter(url):
    return bool(re.search(r'(twitter\.com|x\.com)', url))

def convert_url_to_audio(url, output_path, task_id, bitrate='320k', fmt='mp3'):
    """Download and convert URL to audio using native methods for YouTube/Twitter."""
    try:
        conversions[task_id] = {'status':'downloading','progress':0}
        
        direct_url = None
        method = ''
        
        # ============ YOUTUBE ============
        if is_youtube(url):
            print(f"[youtube] Processing: {url}", flush=True)
            
            # Method 1: Invidious (direct audio stream)
            print("[youtube] Trying Invidious...", flush=True)
            conversions[task_id]['status'] = 'downloading'
            direct_url = get_youtube_native_url(url)
            if direct_url:
                method = 'invidious'
            
            # Method 2: Cobalt API
            if not direct_url:
                print("[youtube] Trying Cobalt API...", flush=True)
                direct_url = get_cobalt_url(url, audio_only=True)
                if direct_url:
                    method = 'cobalt'
            
            # Method 3: yt-dlp with android_vr client (fallback)
            if not direct_url:
                print("[youtube] Trying yt-dlp fallback...", flush=True)
                method = 'yt-dlp-youtube'
                output_base = output_path.rsplit('.',1)[0]
                cmd = [
                    'yt-dlp','-x','--audio-format',fmt,'--audio-quality','0',
                    '-o',output_base+'.%(ext)s','--no-playlist','--newline',
                    '--no-check-certificates','--geo-bypass','--force-ipv4',
                    '--legacy-server-connect','--extractor-retries','3','--retries','3',
                    '--extractor-args','youtube:player_client=android_vr,mweb',
                    '--user-agent','com.google.android.apps.youtube.vr.oculus/1.56.21 (Linux; U; Android 12; en_US)',
                    url
                ]
                my_env = os.environ.copy()
                my_env['CURL_CA_BUNDLE'] = ''
                my_env['PYTHONHTTPSVERIFY'] = '0'
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=my_env)
                for line in process.stdout:
                    print(f"[yt-dlp] {line.strip()}", flush=True)
                    if '[download]' in line and '%' in line:
                        try:
                            for p in line.split():
                                if '%' in p:
                                    conversions[task_id]['progress'] = min(int(float(p.replace('%',''))),99)
                                    break
                        except: pass
                process.wait()
                for ext in ['.mp3','.m4a','.opus','.ogg','.flac','.wav','.webm','.aac','.mp4','.mkv']:
                    test = output_base+ext
                    if os.path.exists(test):
                        conversions[task_id] = {'status':'completed','progress':100,'output_path':test,'file_size':os.path.getsize(test),'filename':os.path.basename(test)}
                        print(f"[yt-dlp] OK: {test}", flush=True)
                        return
                conversions[task_id] = {'status':'error','message':'YouTube blocked this video from server. Try uploading the video file instead.'}
                return
        
        # ============ TWITTER / X ============
        elif is_twitter(url):
            print(f"[twitter] Processing: {url}", flush=True)
            
            # Method 1: fxtwitter/vxtwitter
            print("[twitter] Trying fxtwitter...", flush=True)
            direct_url = get_twitter_native_url(url)
            if direct_url:
                method = 'fxtwitter'
            
            # Method 2: Cobalt API
            if not direct_url:
                print("[twitter] Trying Cobalt API...", flush=True)
                direct_url = get_cobalt_url(url, audio_only=False)
                if direct_url:
                    method = 'cobalt'
            
            # Method 3: yt-dlp fallback
            if not direct_url:
                print("[twitter] Trying yt-dlp...", flush=True)
                method = 'yt-dlp-twitter'
                output_base = output_path.rsplit('.',1)[0]
                cmd = [
                    'yt-dlp','-x','--audio-format',fmt,'--audio-quality','0',
                    '-o',output_base+'.%(ext)s','--no-playlist','--newline',
                    '--no-check-certificates','--geo-bypass','--force-ipv4',
                    '--retries','3','--user-agent',
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0',
                    url
                ]
                my_env = os.environ.copy()
                my_env['CURL_CA_BUNDLE'] = ''
                my_env['PYTHONHTTPSVERIFY'] = '0'
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=my_env)
                for line in process.stdout:
                    print(f"[yt-dlp] {line.strip()}", flush=True)
                process.wait()
                for ext in ['.mp3','.m4a','.opus','.ogg','.flac','.wav','.webm','.aac','.mp4','.mkv']:
                    test = output_base+ext
                    if os.path.exists(test):
                        conversions[task_id] = {'status':'completed','progress':100,'output_path':test,'file_size':os.path.getsize(test),'filename':os.path.basename(test)}
                        return
                conversions[task_id] = {'status':'error','message':'Twitter/X download failed. Try uploading the video file instead.'}
                return
        
        # ============ OTHER PLATFORMS (TikTok, Instagram, Vimeo, etc.) ============
        else:
            print(f"[other] Processing: {url}", flush=True)
            
            # Method 1: Cobalt API
            direct_url = get_cobalt_url(url, audio_only=True)
            if direct_url:
                method = 'cobalt'
            
            # Method 2: yt-dlp
            if not direct_url:
                print("[other] Trying yt-dlp...", flush=True)
                method = 'yt-dlp'
                output_base = output_path.rsplit('.',1)[0]
                cmd = [
                    'yt-dlp','-x','--audio-format',fmt,'--audio-quality','0',
                    '-o',output_base+'.%(ext)s','--no-playlist','--newline',
                    '--no-check-certificates','--geo-bypass','--force-ipv4',
                    '--legacy-server-connect','--retries','3',
                    '--user-agent','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0',
                    url
                ]
                my_env = os.environ.copy()
                my_env['CURL_CA_BUNDLE'] = ''
                my_env['PYTHONHTTPSVERIFY'] = '0'
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=my_env)
                for line in process.stdout:
                    print(f"[yt-dlp] {line.strip()}", flush=True)
                    if '[download]' in line and '%' in line:
                        try:
                            for p in line.split():
                                if '%' in p:
                                    conversions[task_id]['progress'] = min(int(float(p.replace('%',''))),99)
                                    break
                        except: pass
                process.wait()
                for ext in ['.mp3','.m4a','.opus','.ogg','.flac','.wav','.webm','.aac','.mp4','.mkv']:
                    test = output_base+ext
                    if os.path.exists(test):
                        conversions[task_id] = {'status':'completed','progress':100,'output_path':test,'file_size':os.path.getsize(test),'filename':os.path.basename(test)}
                        return
                conversions[task_id] = {'status':'error','message':'Download failed. Check the URL.'}
                return
        
        # ============ DOWNLOAD DIRECT URL ============
        if direct_url:
            print(f"[{method}] Downloading from direct URL...", flush=True)
            conversions[task_id]['status'] = 'downloading'
            
            # Determine temp extension
            temp_ext = '.mp4'
            if method == 'invidious':
                temp_ext = '.webm'  # Invidious usually gives webm
            temp_path = output_path.rsplit('.',1)[0] + '_temp' + temp_ext
            
            if download_file(direct_url, temp_path, task_id):
                print(f"[{method}] Downloaded. Converting to {fmt}...", flush=True)
                conversions[task_id]['status'] = 'converting'
                conversions[task_id]['progress'] = 95
                convert_audio(temp_path, output_path, task_id, bitrate, fmt)
            else:
                conversions[task_id] = {'status':'error','message':f'Download failed via {method}. Try uploading the video file directly.'}
        else:
            conversions[task_id] = {'status':'error','message':'Could not get download URL. Try uploading the video file directly.'}
            
    except Exception as e:
        print(f"[error] {e}", flush=True)
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
    output_path = os.path.join(app.config['CONVERTED_FOLDER'], f'{task_id}_{os.path.splitext(filename)[0]}.{fmt}')
    thread = threading.Thread(target=convert_audio, args=(input_path,output_path,task_id,bitrate,fmt,sample_rate))
    thread.daemon = True; thread.start()
    return jsonify({'task_id':task_id,'message':'Converting'})

@app.route('/convert-url', methods=['POST'])
def convert_url():
    data = request.get_json()
    url = data.get('url','').strip()
    if not url: return jsonify({'error':'No URL'}),400
    if not url.startswith('http'): url = 'https://' + url
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
def download_file_route(task_id):
    if task_id not in conversions: return jsonify({'error':'Not found'}),404
    task = conversions[task_id]
    if task['status'] != 'completed': return jsonify({'error':'Not ready'}),400
    path = task['output_path']
    fname = task.get('filename', os.path.basename(path))
    if '_' in fname:
        parts = fname.split('_')
        if len(parts) > 1: fname = '_'.join(parts[1:])
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
    port = int(os.environ.get('PORT', 10000))
    print("="*50)
    print("  Video2MP3 Pro")
    print(f"  Port: {port}")
    print("="*50)
    try:
        r = subprocess.run(['yt-dlp','--version'], capture_output=True, text=True)
        print(f"  yt-dlp: {r.stdout.strip()}", flush=True)
    except: print("  yt-dlp: NOT FOUND", flush=True)
    try:
        subprocess.run(['ffmpeg','-version'], capture_output=True)
        print("  ffmpeg: OK", flush=True)
    except: print("  ffmpeg: NOT FOUND", flush=True)
    print("  cobalt API: Enabled", flush=True)
    print("  invidious API: Enabled", flush=True)
    print("  fxtwitter API: Enabled", flush=True)
    print("="*50)
    app.run(host='0.0.0.0', port=port, debug=False)
