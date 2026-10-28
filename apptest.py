import os
import uuid
import re
import glob
import subprocess
import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp
import edge_tts
from PIL import Image, ImageDraw, ImageFont

app = FastAPI(title="ChiriwAi.Copy - Store Package System (Port 8005)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = "static/outputs"
TEMP_DIR = "static/temp"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password TEXT,
                    credits INTEGER DEFAULT 0
                )''')
    conn.commit()
    conn.close()

init_db()

class AuthRequest(BaseModel):
    username: str
    password: str

class ModifyCreditRequest(BaseModel):
    username: str
    amount: int

class RenderRequest(BaseModel):
    username: str
    url: str
    duration_mins: int
    script_text: str
    aspect_ratio: str
    voice_type: str = "male_deep"
    playback_speed: float = 1.0

# --- AUTH API ---
@app.post("/api/register")
def register(req: AuthRequest):
    try:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute("INSERT INTO users (username, password, credits) VALUES (?, ?, 0)", (req.username, req.password))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "สมัครสมาชิก ChiriwAi.Copy สำเร็จ! กรุณาเลือกซื้อแพ็กเกจโปรแกรม 300 เครดิต"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="ชื่อผู้ใช้นี้ถูกใช้งานแล้ว")

@app.post("/api/login")
def login(req: AuthRequest):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT credits FROM users WHERE username = ? AND password = ?", (req.username, req.password))
    user = c.fetchone()
    conn.close()
    if user:
        return {"status": "success", "username": req.username, "credits": user[0]}
    raise HTTPException(status_code=400, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

# API สำหรับแอดมินเพิ่มเครดิต
@app.post("/api/admin/add-credits")
def add_credits(req: ModifyCreditRequest):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = ?", (req.username,))
    if not c.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="ไม่พบชื่อผู้ใช้นี้ในระบบ")

    c.execute("UPDATE users SET credits = credits + ? WHERE username = ?", (req.amount, req.username))
    conn.commit()
    c.execute("SELECT credits FROM users WHERE username = ?", (req.username,))
    new_credits = c.fetchone()[0]
    conn.close()
    return {"status": "success", "message": f"เติมเครดิตให้ {req.username} สำเร็จ! เครดิตรวม: {new_credits}", "credits": new_credits}

# API สำหรับแอดมินลด/ลบเครดิต
@app.post("/api/admin/remove-credits")
def remove_credits(req: ModifyCreditRequest):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT credits FROM users WHERE username = ?", (req.username,))
    user = c.fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="ไม่พบชื่อผู้ใช้นี้ในระบบ")

    current_credits = user[0]
    new_credits = max(0, current_credits - req.amount)

    c.execute("UPDATE users SET credits = ? WHERE username = ?", (new_credits, req.username))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"ลบเครดิตของ {req.username} สำเร็จ! เครดิตคงเหลือ: {new_credits}", "credits": new_credits}

# --- VIDEO UTILS API ---
def parse_vtt_subtitles(vtt_file_path: str) -> str:
    text_lines = []
    seen = set()
    try:
        with open(vtt_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or '-->' in line or line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:') or re.match(r'^\d+$', line):
                    continue
                clean_line = re.sub(r'<[^>]*>', '', line)
                clean_line = re.sub(r'&[g|l]t;', '', clean_line)
                clean_line = re.sub(r'>>|>', '', clean_line)
                clean_line = re.sub(r'\([^)]*\)', '', clean_line)
                clean_line = re.sub(r'\[[^\]]*\]', '', clean_line)
                clean_line = clean_line.strip()
                if len(clean_line) > 2 and clean_line not in seen:
                    seen.add(clean_line)
                    text_lines.append(clean_line)
    except Exception as e:
        print("VTT Parse error:", e)
    return " ".join(text_lines)

@app.post("/api/analyze")
async def analyze_video(req: dict):
    try:
        url = req.get('url')
        video_title = "ตอนสำคัญของเรื่อง"
        extracted_script = ""
        video_duration_secs = 60.0
        
        ydl_opts_info = {'quiet': True, 'no_warnings': True}
        try:
            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                info = ydl.extract_info(url, download=False)
                video_title = info.get('title', video_title)
                video_duration_secs = float(info.get('duration', 60.0))
        except Exception as e:
            print("Info extract error:", e)

        sub_temp_prefix = os.path.join(TEMP_DIR, f"sub_{str(uuid.uuid4())[:8]}")
        ydl_opts_sub = {
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['th', 'th-TH', 'en', 'en-US'],
            'outtmpl': sub_temp_prefix,
            'quiet': True,
            'no_warnings': True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts_sub) as ydl:
                ydl.download([url])
        except Exception as e:
            print("Subtitle download warning:", e)

        sub_files = glob.glob(sub_temp_prefix + "*")
        target_sf = None
        for sf in sub_files:
            if any(ext in sf for ext in ['.vtt', '.srt']):
                if 'th' in sf:
                    target_sf = sf
                    break
                elif not target_sf:
                    target_sf = sf

        if target_sf:
            parsed_text = parse_vtt_subtitles(target_sf)
            if parsed_text and len(parsed_text) > 10:
                extracted_script = parsed_text

        if not extracted_script or len(extracted_script) < 20:
            try:
                with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                    info = ydl.extract_info(url, download=False)
                    description = info.get('description', '')
                    if description and len(description) > 20:
                        extracted_script = f"เนื้อหาจากวิดีโอ {video_title}: {description}"
            except Exception:
                pass

        for sf in sub_files:
            try: os.remove(sf)
            except: pass

        if not extracted_script or len(extracted_script) < 10:
            extracted_script = f"สรุปเนื้อหาสำคัญจากวิดีโอเรื่อง {video_title}"

        return {
            "status": "success",
            "title": video_title,
            "script": extracted_script[:1000000],
            "duration_secs": video_duration_secs,
            "duration_mins": max(1, round(video_duration_secs / 60))
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ไม่สามารถดึงข้อมูลจากลิงก์ได้: {str(e)}")

@app.post("/api/extract-frames")
async def extract_frames(req: dict):
    try:
        url = req.get('url')
        job_id = str(uuid.uuid4())[:8]
        temp_video = os.path.join(TEMP_DIR, f"extract_{job_id}.mp4")
        
        ydl_opts = { 'outtmpl': temp_video, 'quiet': True }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            duration = float(info.get('duration', 60.0))

        frame_urls = []
        timestamps = [duration * 0.15, duration * 0.35, duration * 0.55, duration * 0.75]
        
        for i, ts in enumerate(timestamps):
            frame_name = f"frame_{job_id}_{i}.jpg"
            frame_path = os.path.join(TEMP_DIR, frame_name)
            
            cmd = ['ffmpeg', '-y', '-ss', str(ts), '-i', temp_video, '-vframes', '1', '-q:v', '2', frame_path]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if os.path.exists(frame_path):
                static_frame_path = os.path.join(OUTPUT_DIR, frame_name)
                os.replace(frame_path, static_frame_path)
                frame_urls.append(f"/static/outputs/{frame_name}")

        try: os.remove(temp_video)
        except: pass

        return { "status": "success", "frames": frame_urls }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ไม่สามารถสกัดฉากได้: {str(e)}")

def hex_to_rgb(hex_str: str):
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))

@app.post("/api/generate-custom-thumbnail")
async def generate_custom_thumbnail(req: dict):
    try:
        frame_path = req.get('frame_path')
        if frame_path:
            local_path = frame_path.lstrip('/')
            if os.path.exists(local_path):
                bg_img = Image.open(local_path).convert('RGB')
            else:
                bg_img = Image.new('RGB', (1280, 720), color=(15, 23, 42))
        else:
            bg_img = Image.new('RGB', (1280, 720), color=(15, 23, 42))

        bg_img = bg_img.resize((1280, 720))
        overlay = Image.new('RGBA', (1280, 720), (5, 10, 20, 120))
        bg_img = bg_img.convert('RGBA')
        bg_img = Image.alpha_composite(bg_img, overlay).convert('RGB')
        draw = ImageDraw.Draw(bg_img)

        try: font_title = ImageFont.truetype("tahoma.ttf", 60)
        except:
            try: font_title = ImageFont.truetype("arial.ttf", 60)
            except: font_title = ImageFont.load_default()

        rgb_text = hex_to_rgb(req.get('text_color', '#ffffff'))
        rgb_outline = hex_to_rgb(req.get('outline_color', '#000000'))

        def draw_text_with_outline(draw_obj, xy, text, font, fill_color, outline_color, width=5):
            x, y = xy
            for adj_x in range(-width, width + 1):
                for adj_y in range(-width, width + 1):
                    if adj_x != 0 or adj_y != 0:
                        draw_obj.text((x + adj_x, y + adj_y), text, font=font, fill=outline_color)
            draw_obj.text((x, y), text, font=font, fill=fill_color)

        if req.get('top_text'):
            draw_text_with_outline(draw, (80, 80), req['top_text'], font_title, fill_color=rgb_text, outline_color=rgb_outline, width=5)
        if req.get('bottom_text'):
            draw_text_with_outline(draw, (80, 520), req['bottom_text'], font_title, fill_color=rgb_text, outline_color=rgb_outline, width=5)

        thumb_id = str(uuid.uuid4())[:8]
        thumb_path = os.path.join(OUTPUT_DIR, f"thumb_{thumb_id}.png")
        bg_img.save(thumb_path)

        return { "status": "success", "thumbnail_url": f"/static/outputs/thumb_{thumb_id}.png" }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- RENDER API ---
@app.post("/api/render")
async def render_video(req: RenderRequest):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute("SELECT credits FROM users WHERE username = ?", (req.username,))
    user = c.fetchone()
    
    if not user:
        conn.close()
        raise HTTPException(status_code=400, detail="ไม่พบข้อมูลผู้ใช้งาน กรุณาเข้าสู่ระบบใหม่")
    
    current_credits = user[0]
    if current_credits < 300 and req.username != "adminchiriw":
        conn.close()
        raise HTTPException(status_code=403, detail="บัญชีของคุณยังไม่ได้ซื้อแพ็กเกจ ChiriwAi.Copy (ต้องมีอย่างน้อย 300 เครดิต)")
    
    conn.close()

    try:
        job_id = str(uuid.uuid4())[:8]
        raw_video = os.path.join(TEMP_DIR, f"raw_full_{job_id}.mp4")
        tts_audio = os.path.join(TEMP_DIR, f"voice_{job_id}.mp3")
        final_video_name = f"recap_{job_id}.mp4"
        final_video = os.path.join(OUTPUT_DIR, final_video_name)

        ydl_opts = { 
            'outtmpl': raw_video, 
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}}
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([req.url])

        if not os.path.exists(raw_video):
            raise HTTPException(status_code=400, detail="ไม่สามารถดาวน์โหลดวิดีโอต้นฉบับได้")

        if req.voice_type == "female_soft":
            voices_to_test = ["th-TH-PremwadeeNeural", "th-TH-AcharaNeural", "th-TH-KanyaNeural"]
        else:
            voices_to_test = ["th-TH-NiwatNeural", "th-TH-SurawutNeural"]

        script_content = req.script_text.strip() or "สรุปเนื้อหาสำคัญจากวิดีโอ"

        audio_created = False
        for v_name in voices_to_test:
            try:
                communicate = edge_tts.Communicate(script_content, v_name)
                await communicate.save(tts_audio)
                if os.path.exists(tts_audio) and os.path.getsize(tts_audio) > 1000:
                    audio_created = True
                    break
            except Exception:
                continue

        if not audio_created:
            try:
                fallback_comm = edge_tts.Communicate("กำลังสรุปเนื้อหาวิดีโอ", "th-TH-NiwatNeural")
                await fallback_comm.save(tts_audio)
                audio_created = True
            except:
                subprocess.run(['ffmpeg', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '5', '-q:a', '9', tts_audio], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if not os.path.exists(tts_audio) or os.path.getsize(tts_audio) == 0:
            raise HTTPException(status_code=500, detail="ไม่สามารถสร้างไฟล์เสียงพากย์ได้")

        speed = max(0.8, min(2.5, req.playback_speed))
        if req.aspect_ratio == '9:16':
            scale_filter = f"[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS/{speed}[v]"
        else:
            scale_filter = f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS/{speed}[v]"
        
        audio_filter = f"atempo={speed}"

        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-i', raw_video,
            '-i', tts_audio,
            '-filter_complex', f"{scale_filter};[1:a]{audio_filter}[a]",
            '-map', '[v]',
            '-map', '[a]',
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            final_video
        ]

        result = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"FFmpeg Error: {result.stderr.decode('utf-8', errors='ignore')[-200:]}")

        for f_path in [raw_video, tts_audio]:
            try:
                if os.path.exists(f_path): os.remove(f_path)
            except:
                pass

        return { 
            "status": "success", 
            "message": "เรนเดอร์วิดีโอสำเร็จโดย ChiriwAi.Copy!", 
            "video_url": f"/static/outputs/{final_video_name}" 
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def read_root():
    return FileResponse("indextest.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apptest:app", host="0.0.0.0", port=8005, reload=True)