#!/usr/bin/env python3
import os, uuid, subprocess, threading, re, requests
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

def check_warp():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(('127.0.0.1', 40000))
        s.close()
        return True
    except:
        return False

def detect_platform(url):
    u = url.lower()
    if 'youtube.com' in u or 'youtu.be' in u: return 'youtube'
    elif 'twitter.com' in u or 'x.com' in u: return 'twitter'
    elif 'instagram.com' in u: return 'instagram'
    elif 'tiktok.com' in u: return 'tiktok'
    elif 'facebook.com' in u or 'fb.watch' in u: return 'facebook'
    return 'other'

def convert_to_mp3(inp, out, tid, br='320k'):
    try:
        conversions[tid] = {'status':'converting','progress':0}
        p = subprocess.Popen(['ffmpeg','-i',inp,'-vn','-acodec','libmp3lame','-ab',br,'-ar','44100','-ac','2','-y',out], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p.wait()
        if p.returncode == 0 and os.path.exists(out):
            conversions[tid] = {'status':'completed','progress':100,'output_path':out,'file_size':os.path.getsize(out)}
        else:
            conversions[tid] = {'status':'error','message':'MP3 conversion failed'}
    except Exception as e:
        conversions[tid] = {'status':'error','message':str(e)}
    finally:
        if os.path.exists(inp):
            try: os.remove(inp)
            except: pass

def download_twitter(url, out, tid):
    try:
        conversions[tid] = {'status':'downloading','progress':0}
        headers = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}
        tweet_id = None
        m = re.search(r'/status/(\d+)', url)
        if m: tweet_id = m.group(1)
        if not tweet_id: raise Exception("Could not extract tweet ID from URL")

        video_url = None

        # Method 1: fxtwitter API
        try:
            r = requests.get(f"https://api.fxtwitter.com/status/{tweet_id}", headers=headers, timeout=15)
            if r.status_code == 200:
                data = r.json()
                videos = data.get('tweet',{}).get('media',{}).get('videos',[])
                if videos:
                    best = None
                    best_br = 0
                    for v in videos:
                        for var in v.get('variants',[]):
                            if var.get('content_type') == 'video/mp4':
                                br = var.get('bitrate',0)
                                if br > best_br:
                                    best_br = br
                                    best = var.get('url')
                        if not best and v.get('url'): best = v['url']
                    video_url = best
        except: pass

        # Method 2: vxtwitter API
        if not video_url:
            try:
                r = requests.get(f"https://api.vxtwitter.com/status/{tweet_id}", headers=headers, timeout=15)
                if r.status_code == 200:
                    for m in r.json().get('media_extended',[]):
                        if m.get('type') == 'video' and m.get('url'):
                            video_url = m['url']
                            break
            except: pass

        # Method 3: d.fxtwitter redirect
        if not video_url:
            try:
                fx = url.replace('twitter.com','d.fxtwitter.com').replace('x.com','d.fxtwitter.com')
                r = requests.head(fx, headers=headers, timeout=15, allow_redirects=True)
                if r.status_code == 200 and 'video' in r.headers.get('content-type',''):
                    video_url = r.url
            except: pass

        if not video_url: raise Exception("No video found in this tweet. It may be text-only, private, or deleted.")

        with requests.get(video_url, headers=headers, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(out, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk: f.write(chunk)

        if os.path.exists(out) and os.path.getsize(out) > 10000:
            conversions[tid]['progress'] = 100
            return out
        raise Exception("Downloaded file too small")
    except Exception as e:
        raise Exception(f"Twitter/X: {str(e)}")

def download_instagram(url, out, tid):
    try:
        conversions[tid] = {'status':'downloading','progress':0}
        headers = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'}
        video_url = None
        shortcode = None
        m = re.search(r'/(p|reel|reels|tv)/([A-Za-z0-9_-]+)', url)
        if m: shortcode = m.group(2)

        # Method 1: DDInstagram
        if shortcode:
            try:
                r = requests.get(url.replace('instagram.com','ddinstagram.com'), headers=headers, timeout=15, allow_redirects=True)
                mp4s = re.findall(r'https?://[^"\'<>\s]+\.mp4[^"\'<>\s]*', r.text)
                if mp4s: video_url = mp4s[0]
            except: pass

        # Method 2: Instagram JSON API
        if not video_url and shortcode:
            try:
                r = requests.get(f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis", headers=headers, timeout=15)
                if r.status_code == 200:
                    video_url = r.json().get('graphql',{}).get('shortcode_media',{}).get('video_url')
            except: pass

        # Method 3: og:video meta tag
        if not video_url:
            try:
                r = requests.get(url, headers=headers, timeout=15)
                m = re.search(r'property="og:video"[^>]+content="([^"]+)"', r.text)
                if m: video_url = m.group(1)
                if not video_url:
                    m = re.search(r'<meta[^>]+content="([^"]+\.mp4[^"]*)"', r.text)
                    if m: video_url = m.group(1)
            except: pass

        if video_url:
            with requests.get(video_url, headers=headers, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(out, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk: f.write(chunk)
            if os.path.exists(out) and os.path.getsize(out) > 10000:
                conversions[tid]['progress'] = 100
                return out

        raise Exception("Instagram requires login for most content. Try uploading the video file directly.")
    except Exception as e:
        raise Exception(f"Instagram: {str(e)}")

def download_ytdlp(url, out, tid, br='320k', fmt='mp3'):
    try:
        conversions[tid] = {'status':'downloading','progress':0}
        cmd = ['yt-dlp','--extract-audio','--audio-format',fmt,'--audio-quality','0',
               '--no-playlist','--ignore-errors','--no-mtime','--no-check-certificates',
               '--force-ipv4','--socket-timeout','120','--retries','10','--fragment-retries','10',
               '--retry-sleep','3','--user-agent','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
               '--referer','https://www.youtube.com/','--output',out.rsplit('.',1)[0]+'.%(ext)s',url]
        if check_warp():
            cmd.insert(1,'--proxy')
            cmd.insert(2,'socks5://127.0.0.1:40000')

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        full = ""
        for line in proc.stdout:
            full += line
            if '%' in line:
                try:
                    pct = float(line.split('%')[0].strip().split()[-1])
                    conversions[tid]['progress'] = min(int(pct),99)
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
                msg = "Bot detection triggered. Try again in a minute."
            elif "login" in lower or "sign in" in lower:
                msg = "This content requires login. Try uploading the video file directly."
            elif "private" in lower or "unavailable" in lower:
                msg = "Video is private, removed, or region-locked."
            else:
                msg = "Download failed. Try a different video or try again later."
            conversions[tid] = {'status':'error','message':msg}
    except Exception as e:
        conversions[tid] = {'status':'error','message':str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return jsonify({'error':'No file selected'}),400
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename): return jsonify({'error':'Invalid file'}),400
    br = request.form.get('bitrate','320k')
    tid = str(uuid.uuid4())[:8]
    fn = secure_filename(file.filename)
    inp = os.path.join(app.config['UPLOAD_FOLDER'], f'{tid}_{fn}')
    file.save(inp)
    out = os.path.join(app.config['CONVERTED_FOLDER'], f'{tid}_{os.path.splitext(fn)[0]}.mp3')
    threading.Thread(target=convert_to_mp3, args=(inp,out,tid,br), daemon=True).start()
    return jsonify({'task_id':tid})

@app.route('/convert-url', methods=['POST'])
def convert_url_route():
    data = request.get_json()
    url = data.get('url','').strip()
    if not url: return jsonify({'error':'No URL provided'}),400
    br = data.get('bitrate','320k')
    fmt = data.get('format','mp3')
    tid = str(uuid.uuid4())[:8]
    out = os.path.join(app.config['CONVERTED_FOLDER'], f'audio_{tid}.{fmt}')

    def process():
        try:
            plat = detect_platform(url)
            if plat == 'twitter':
                tmp = os.path.join(app.config['UPLOAD_FOLDER'], f'{tid}_tw.mp4')
                download_twitter(url, tmp, tid)
                convert_to_mp3(tmp, out, tid, br)
            elif plat == 'instagram':
                tmp = os.path.join(app.config['UPLOAD_FOLDER'], f'{tid}_ig.mp4')
                try:
                    download_instagram(url, tmp, tid)
                    convert_to_mp3(tmp, out, tid, br)
                except:
                    download_ytdlp(url, out, tid, br, fmt)
            else:
                download_ytdlp(url, out, tid, br, fmt)
        except Exception as e:
            conversions[tid] = {'status':'error','message':str(e)}

    threading.Thread(target=process, daemon=True).start()
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
    print(f"🎵 Video2MP3 Pro on port {port}")
    print(f"🌐 WARP: {'✅' if check_warp() else '❌'}")
    app.run(host='0.0.0.0', port=port, debug=False)
