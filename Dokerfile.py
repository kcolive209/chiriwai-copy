FROM python:3.10-slim

# ติดตั้ง FFmpeg สำหรับตัดต่อวิดีโอ
RUN apt-get update && apt-get install -y ffmpeg curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ติดตั้งไลบรารี Python ที่ต้องใช้
RUN pip install --no-cache-dir fastapi uvicorn yt-dlp edge-tts pillow pydantic

# คัดลอกไฟล์ทั้งหมดในเครื่องขึ้นคลาวด์
COPY . /app

# เปิดพอร์ตใช้งาน
EXPOSE 7860

# สั่งรันแอปพลิเคชัน
CMD ["uvicorn", "apptest:app", "--host", "0.0.0.0", "--port", "7860"]