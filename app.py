#!/usr/bin/env python3
import os, uuid, subprocess, json, threading, re, io, time
import requests as req
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from bs4 import BeautifulSoup

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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}

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
            conversions[task_id] = {'status':'error','message':'Audio conversion failed'}
    except Exception as e:
        conversions[task_id] = {'status':'error','message':str(e)}
    finally:
        try:
            if os.path.exists(input_path): os.remove(input_path)
        except: pass

def download_from_url(url, save_path, task_id):
    """Download file with progress."""
    try:
        h = HEADERS.copy()
        h['Accept'] = '*/*'
        r = req.get(url, headers=h, stream=True, timeout=180, verify=False, allow_redirects=True)
        total = int(r.headers.get('content-length', 0))
        downloaded = 0
        with open(save_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        conversions[task_id]['progress'] = min(int((downloaded / total) * 95), 95)
        ok = os.path.exists(save_path) and os.path.getsize(save_path) > 1000
        if ok:
            print(f"[download] OK: {os.path.getsize(save_path)} bytes", flush=True)
        else:
            print(f"[download] Failed: file too small or missing", flush=True)
        return ok
    except Exception as e:
        print(f"[download] Error: {e}", flush=True)
        return False

# ====================================================
# YOUTUBE: pytubefix
# ====================================================
def youtube_pytubefix(url, task_id):
    """Download YouTube audio using pytubefix."""
    try:
        from pytubefix import YouTube
        print("[pytubefix] Starting download...", flush=True)
        yt = YouTube(url, use_oauth=False, allow_oauth_cache=False)
        print(f"[pytubefix] Title: {yt.title}", flush=True)
        
        # Get audio stream
        stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
        if not stream:
            stream = yt.streams.filter(progressive=True).order_by('abr').desc().first()
        
        if not stream:
            print("[pytubefix] No stream found", flush=True)
            return None
        
        print(f"[pytubefix] Stream: {stream.mime_type} {stream.abr}", flush=True)
        
        # Download
        temp_path = os.path.join(app.config['CONVERTED_FOLDER'], f'{task_id}_yt_temp.{stream.subtype}')
        
        # Use progressive download
        stream.download(output_path=app.config['CONVERTED_FOLDER'], filename=f'{task_id}_yt_temp.{stream.subtype}')
        
        if os.path.exists(temp_path) and os.path.getsize(temp_path) > 1000:
            print(f"[pytubefix] Downloaded: {os.path.getsize(temp_path)} bytes", flush=True)
            return temp_path
        
        # Try alternate filename
        for f in os.listdir(app.config['CONVERTED_FOLDER']):
            if f.startswith(f'{task_id}_yt_temp'):
                fp = os.path.join(app.config['CONVERTED_FOLDER'], f)
                if os.path.getsize(fp) > 1000:
                    print(f"[pytubefix] Found: {fp}", flush=True)
                    return fp
        
        print("[pytubefix] Download failed", flush=True)
        return None
    except Exception as e:
        print(f"[pytubefix] Error: {e}", flush=True)
        return None

# ====================================================
# YOUTUBE: Custom scraper (extract from page HTML)
# ====================================================
def youtube_scraper(url, task_id):
    """Scrape YouTube page for direct audio URL."""
    try:
        print("[yt-scraper] Fetching page...", flush=True)
        
        h = HEADERS.copy()
        h['Cookie'] = 'CONSENT=YES+1'
        
        r = req.get(url, headers=h, timeout=20, verify=False)
        html = r.text
        
        # Extract video ID
        vid = None
        m = re.search(r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
        if m:
            vid = m.group(1)
        
        if not vid:
            return None
        
        print(f"[yt-scraper] Video ID: {vid}", flush=True)
        
        # Try to extract from ytInitialPlayerResponse
        match = re.search(r'var ytInitialPlayerResponse\s*=\s*({.+?})\s*;', html)
        if not match:
            match = re.search(r'ytInitialPlayerResponse\s*=\s*({.+?})\s*;', html)
        
        if match:
            try:
                data = json.loads(match.group(1))
                streaming = data.get('streamingData', {})
                
                # Get adaptive formats (audio only)
                adaptive = streaming.get('adaptiveFormats', [])
                
                best_audio = None
                for fmt in adaptive:
                    if fmt.get('mimeType','').startswith('audio/'):
                        if 'url' in fmt:
                            if best_audio is None or fmt.get('bitrate',0) > best_audio.get('bitrate',0):
                                best_audio = fmt
                
                if best_audio and 'url' in best_audio:
                    print(f"[yt-scraper] Found audio: {best_audio.get('mimeType')} {best_audio.get('bitrate')}", flush=True)
                    return best_audio['url']
                
                # Try formats (has both audio+video)
                formats = streaming.get('formats', [])
                for fmt in formats:
                    if 'url' in fmt:
                        print(f"[yt-scraper] Found stream: {fmt.get('mimeType')}", flush=True)
                        return fmt['url']
                        
            except json.JSONDecodeError as e:
                print(f"[yt-scraper] JSON error: {e}", flush=True)
        
        # Try to get from video page directly
        print("[yt-scraper] Trying embed page...", flush=True)
        embed_url = f'https://www.youtube.com/embed/{vid}'
        r2 = req.get(embed_url, headers=h, timeout=15, verify=False)
        match2 = re.search(r'"adaptiveFormats":\s*(\[.+?\])', r2.text)
        if match2:
            try:
                formats = json.loads(match2.group(1))
                for fmt in formats:
                    if fmt.get('mimeType','').startswith('audio/') and 'url' in fmt:
                        print(f"[yt-scraper] Embed found audio", flush=True)
                        return fmt['url']
            except: pass
        
        print("[yt-scraper] No direct URL found", flush=True)
        return None
    except Exception as e:
        print(f"[yt-scraper] Error: {e}", flush=True)
        return None

# ====================================================
# TWITTER: Custom scraper
# ====================================================
def twitter_scraper(url, task_id):
    """Scrape Twitter/X page for direct video URL."""
    try:
        print("[twitter-scraper] Fetching page...", flush=True)
        
        h = HEADERS.copy()
        
        # Get the page
        r = req.get(url, headers=h, timeout=20, verify=False, allow_redirects=True)
        html = r.text
        
        # Method 1: Look for video URL in page source
        # Twitter embeds video URLs in JSON data
        video_urls = []
        
        # Pattern: look for .mp4 URLs
        mp4_matches = re.findall(r'https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*', html)
        for u in mp4_matches:
            u = u.split('"')[0].split("'")[0].split('?')[0]
            if 'video.twimg.com' in u or 'pbs.twimg.com' in u:
                video_urls.append(u)
        
        # Pattern: look for video.twimg.com URLs
        video_matches = re.findall(r'https?://video\.twimg\.com/[^\s"\'<>]+', html)
        for u in video_matches:
            u = u.split('"')[0].split("'")[0]
            if u not in video_urls:
                video_urls.append(u)
        
        if video_urls:
            # Get highest quality
            best = video_urls[-1]  # Usually last is highest quality
            print(f"[twitter-scraper] Found video URL: {best[:100]}", flush=True)
            return best
        
        # Method 2: Look in embedded JSON
        json_match = re.search(r'data-testid="video_player".*?"source":\s*"(https?://[^"]+)"', html, re.DOTALL)
        if json_match:
            print(f"[twitter-scraper] Found in JSON", flush=True)
            return json_match.group(1).replace('\\u002F', '/')
        
        # Method 3: Try to find video source tags
        soup = BeautifulSoup(html, 'lxml')
        video_tags = soup.find_all('video')
        for v in video_tags:
            src = v.get('src')
            if src:
                print(f"[twitter-scraper] Found video tag: {src[:100]}", flush=True)
                return src
            sources = v.find_all('source')
            for s in sources:
                src = s.get('src')
                if src:
                    print(f"[twitter-scraper] Found source: {src[:100]}", flush=True)
                    return src
        
        # Method 4: Try syndication API
        tweet_id = None
        m = re.search(r'(?:status|statuses)/(\d+)', url)
        if m:
            tweet_id = m.group(1)
        
        if tweet_id:
            print(f"[twitter-scraper] Trying syndication for ID: {tweet_id}", flush=True)
            synd_url = f'https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=abc'
            try:
                r2 = req.get(synd_url, headers=HEADERS, timeout=15, verify=False)
                if r2.status_code == 200:
                    data = r2.json()
                    video = data.get('video', {})
                    if video:
                        variants = video.get('variants', [])
                        best = None
                        for v in variants:
                            if v.get('type') == 'video/mp4':
                                if best is None or v.get('bitrate',0) > best.get('bitrate',0):
                                    best = v
                        if best and 'src' in best:
                            print(f"[twitter-scraper] Syndication found: {best['src'][:100]}", flush=True)
                            return best['src']
            except Exception as e:
                print(f"[twitter-scraper] Syndication failed: {e}", flush=True)
        
        print("[twitter-scraper] No video found", flush=True)
        return None
    except Exception as e:
        print(f"[twitter-scraper] Error: {e}", flush=True)
        return None

# ====================================================
# COBALT API
# ====================================================
def cobalt_download(url, audio_only=True):
    """Use Cobalt API to get direct download URL."""
    try:
        apis = [
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
            'url': url,
            'downloadMode': 'audio' if audio_only else 'auto',
            'audioFormat': 'mp3',
        }
        
        for api in apis:
            try:
                print(f"[cobalt] Trying: {api}", flush=True)
                r = req.post(api, json=body, headers=headers, timeout=30, verify=False)
                if r.status_code == 200:
                    data = r.json()
                    dl = data.get('url') or data.get('streamUrl')
                    if not dl and 'urls' in data and data['urls']:
                        dl = data['urls'][0] if isinstance(data['urls'], list) else data['urls']
                    if dl:
                        print(f"[cobalt] Got URL", flush=True)
                        return dl
            except:
                continue
        return None
    except:
        return None

# ====================================================
# MAIN URL CONVERTER
# ====================================================
def is_youtube(u):
    return bool(re.search(r'(youtube\.com|youtu\.be)', u))

def is_twitter(u):
    return bool(re.search(r'(twitter\.com|x\.com|t\.co)', u))

def convert_url_to_audio(url, output_path, task_id, bitrate='320k', fmt='mp3'):
    try:
        conversions[task_id] = {'status':'downloading','progress':0}
        direct_url = None
        temp_path = None
        
        # ============ YOUTUBE ============
        if is_youtube(url):
            print(f"[youtube] Processing: {url}", flush=True)
            
            # Method 1: pytubefix
            print("[youtube] Method 1: pytubefix...", flush=True)
            conversions[task_id]['status'] = 'downloading'
            temp_path = youtube_pytubefix(url, task_id)
            if temp_path and os.path.exists(temp_path):
                print(f"[youtube] pytubefix success!", flush=True)
                conversions[task_id]['status'] = 'converting'
                conversions[task_id]['progress'] = 95
                convert_audio(temp_path, output_path, task_id, bitrate, fmt)
                return
            
            # Method 2: Custom scraper
            print("[youtube] Method 2: custom scraper...", flush=True)
            conversions[task_id]['status'] = 'downloading'
            conversions[task_id]['progress'] = 10
            direct_url = youtube_scraper(url, task_id)
            if direct_url:
                temp_path = os.path.join(app.config['CONVERTED_FOLDER'], f'{task_id}_scraper.webm')
                if download_from_url(direct_url, temp_path, task_id):
                    conversions[task_id]['status'] = 'converting'
                    conversions[task_id]['progress'] = 95
                    convert_audio(temp_path, output_path, task_id, bitrate, fmt)
                    return
            
            # Method 3: Cobalt API
            print("[youtube] Method 3: cobalt...", flush=True)
            conversions[task_id]['progress'] = 20
            direct_url = cobalt_download(url, audio_only=True)
            if direct_url:
                temp_path = os.path.join(app.config['CONVERTED_FOLDER'], f'{task_id}_cobalt.webm')
                if download_from_url(direct_url, temp_path, task_id):
                    conversions[task_id]['status'] = 'converting'
                    conversions[task_id]['progress'] = 95
                    convert_audio(temp_path, output_path, task_id, bitrate, fmt)
                    return
            
            # Method 4: yt-dlp android_vr
            print("[youtube] Method 4: yt-dlp...", flush=True)
            conversions[task_id]['progress'] = 25
            output_base = output_path.rsplit('.',1)[0]
            cmd = [
                'yt-dlp','-x','--audio-format',fmt,'--audio-quality','0',
                '-o',output_base+'.%(ext)s','--no-playlist','--newline',
                '--no-check-certificates','--geo-bypass','--force-ipv4',
                '--extractor-retries','3','--retries','3',
                '--extractor-args','youtube:player_client=android_vr,mweb',
                '--user-agent','com.google.android.apps.youtube.vr.oculus/1.56.21',
                url
            ]
            my_env = os.environ.copy()
            my_env['CURL_CA_BUNDLE'] = ''
            my_env['PYTHONHTTPSVERIFY'] = '0'
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=my_env)
            for line in process.stdout:
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
                if os.path.exists(test) and os.path.getsize(test) > 1000:
                    conversions[task_id] = {'status':'completed','progress':100,'output_path':test,'file_size':os.path.getsize(test),'filename':os.path.basename(test)}
                    return
            
            conversions[task_id] = {'status':'error','message':'YouTube blocked this video. Download the video on your phone and use the Upload tab to convert it.'}
            return
        
        # ============ TWITTER / X ============
        elif is_twitter(url):
            print(f"[twitter] Processing: {url}", flush=True)
            
            # Method 1: Custom scraper
            print("[twitter] Method 1: custom scraper...", flush=True)
            conversions[task_id]['status'] = 'downloading'
            direct_url = twitter_scraper(url, task_id)
            if direct_url:
                temp_path = os.path.join(app.config['CONVERTED_FOLDER'], f'{task_id}_tweet.mp4')
                if download_from_url(direct_url, temp_path, task_id):
                    conversions[task_id]['status'] = 'converting'
                    conversions[task_id]['progress'] = 95
                    convert_audio(temp_path, output_path, task_id, bitrate, fmt)
                    return
            
            # Method 2: Cobalt
            print("[twitter] Method 2: cobalt...", flush=True)
            direct_url = cobalt_download(url, audio_only=False)
            if direct_url:
                temp_path = os.path.join(app.config['CONVERTED_FOLDER'], f'{task_id}_tweet.mp4')
                if download_from_url(direct_url, temp_path, task_id):
                    conversions[task_id]['status'] = 'converting'
                    conversions[task_id]['progress'] = 95
                    convert_audio(temp_path, output_path, task_id, bitrate, fmt)
                    return
            
            # Method 3: yt-dlp
            print("[twitter] Method 3: yt-dlp...", flush=True)
            output_base = output_path.rsplit('.',1)[0]
            cmd = [
                'yt-dlp','-x','--audio-format',fmt,'--audio-quality','0',
                '-o',output_base+'.%(ext)s','--no-playlist','--newline',
                '--no-check-certificates','--geo-bypass','--force-ipv4',
                '--retries','3','--user-agent',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0',
                url
            ]
            my_env = os.environ.copy()
            my_env['CURL_CA_BUNDLE'] = ''
            my_env['PYTHONHTTPSVERIFY'] = '0'
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=my_env)
            for line in process.stdout:
                pass
            process.wait()
            for ext in ['.mp3','.m4a','.opus','.ogg','.flac','.wav','.webm','.aac','.mp4','.mkv']:
                test = output_base+ext
                if os.path.exists(test) and os.path.getsize(test) > 1000:
                    conversions[task_id] = {'status':'completed','progress':100,'output_path':test,'file_size':os.path.getsize(test),'filename':os.path.basename(test)}
                    return
            
            conversions[task_id] = {'status':'error','message':'Twitter/X download failed. Download the video on your phone and use the Upload tab.'}
            return
        
        # ============ OTHER (TikTok, Instagram, Vimeo, etc.) ============
        else:
            print(f"[other] Processing: {url}", flush=True)
            
            # Cobalt
            direct_url = cobalt_download(url, audio_only=True)
            if direct_url:
                temp_path = os.path.join(app.config['CONVERTED_FOLDER'], f'{task_id}_other.webm')
                if download_from_url(direct_url, temp_path, task_id):
                    conversions[task_id]['status'] = 'converting'
                    conversions[task_id]['progress'] = 95
                    convert_audio(temp_path, output_path, task_id, bitrate, fmt)
                    return
            
            # yt-dlp
            output_base = output_path.rsplit('.',1)[0]
            cmd = [
                'yt-dlp','-x','--audio-format',fmt,'--audio-quality','0',
                '-o',output_base+'.%(ext)s','--no-playlist','--newline',
                '--no-check-certificates','--geo-bypass','--force-ipv4',
                '--legacy-server-connect','--retries','3',
                '--user-agent','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0',
                url
            ]
            my_env = os.environ.copy()
            my_env['CURL_CA_BUNDLE'] = ''
            my_env['PYTHONHTTPSVERIFY'] = '0'
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, env=my_env)
            for line in process.stdout:
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
                if os.path.exists(test) and os.path.getsize(test) > 1000:
                    conversions[task_id] = {'status':'completed','progress':100,'output_path':test,'file_size':os.path.getsize(test),'filename':os.path.basename(test)}
                    return
            
            conversions[task_id] = {'status':'error','message':'Download failed. Check the URL or upload the file directly.'}
            return
        
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
    print("  pytubefix: Enabled", flush=True)
    print("  custom scrapers: Enabled", flush=True)
    print("  cobalt API: Enabled", flush=True)
    print("="*50)
    app.run(host='0.0.0.0', port=port, debug=False)
