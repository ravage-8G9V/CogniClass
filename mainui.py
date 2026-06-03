import os
import time
import threading
import cv2
import numpy as np
import requests
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, Response, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel

import attend  # backend face recognition module
import audio_module
import analytics

# =====================================================
# CONFIG & STATE
# =====================================================
TEACHERS = {
    "teacher1": {"password": "pass123", "name": "Dr. Ramesh"},
    "teacher2": {"password": "pass456", "name": "Prof. Anjali"},
}
ADMINS = {
    "admin": {
        "password": "admin123"
    }
}
SUBJECTS = ["Math", "Physics", "CS", "Electronics", "Other"]

BASE_DIR = os.getcwd()
SESSION_ROOT = os.path.join(BASE_DIR, "sessions")
os.makedirs(SESSION_ROOT, exist_ok=True)

SESSION_DB = os.path.join(SESSION_ROOT, "sessions.json")
if not os.path.exists(SESSION_DB):
    with open(SESSION_DB, "w") as f:
        json.dump([], f)

LAPTOP_SERVER_URL = "http://localhost:8000"

session_state = {
    "logged_in": False,
    "teacher": "",
    "username": "",
    "subject": "",
    "start": None,
    "end": None,
    "folder": "",
    "audio_path": "",
}

# Global variables for Camera & Background Workers
latest_frame_bytes = None
frame_lock = threading.Lock()
camera_active = False
camera_thread = None
global_cap = None

attendance_active = False
attendance_thread = None
attendance_status_text = "Inactive"

capture_active = False
capture_thread = None
capture_status_text = "Inactive"

# =====================================================
# HELPERS
# =====================================================
def load_sessions():
    if not os.path.exists(SESSION_DB):
        return []
    try:
        with open(SESSION_DB, "r") as f:
            data = json.load(f)
        valid_sessions = []
        for s in data:
            folder = s.get("folder")
            if folder and os.path.exists(folder):
                valid_sessions.append(s)
        with open(SESSION_DB, "w") as f:
            json.dump(valid_sessions, f, indent=4)
        return valid_sessions
    except Exception as e:
        print("SESSION LOAD ERROR:", e)
        with open(SESSION_DB, "w") as f:
            json.dump([], f)
        return []

def save_session(entry):
    data = load_sessions()
    data.append(entry)
    with open(SESSION_DB, "w") as f:
        json.dump(data, f, indent=4)

def get_selected_folder(label):
    sessions = load_sessions()
    for s in sessions:
        s_label = f"{s['subject']} | {s['time']}"
        if s_label == label:
            return s["folder"]
    return None

# =====================================================
# CAMERA PREVIEW THREAD
# =====================================================
def camera_worker():
    global global_cap, camera_active, latest_frame_bytes
    print("[CAM] Preview thread started")
    
    global_cap = cv2.VideoCapture(0)
    if not global_cap.isOpened():
        print("[CAM] Error: Camera could not be opened")
        camera_active = False
        return
        
    global_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    
    while camera_active:
        ret, frame = global_cap.read()
        if not ret:
            time.sleep(0.01)
            continue
            
        try:
            # Resize frame for efficient tunnel bandwidth
            small = cv2.resize(frame, (400, 300))
            _, jpeg = cv2.imencode('.jpg', small)
            with frame_lock:
                latest_frame_bytes = jpeg.tobytes()
        except Exception as e:
            print(f"[CAM] Encoding error: {e}")
            
        time.sleep(0.04)  # ~25 FPS
        
    if global_cap is not None:
        global_cap.release()
        global_cap = None
    with frame_lock:
        latest_frame_bytes = None
    print("[CAM] Preview thread stopped")

def start_camera():
    global camera_active, camera_thread
    if not camera_active:
        camera_active = True
        camera_thread = threading.Thread(target=camera_worker, daemon=True)
        camera_thread.start()

def stop_camera():
    global camera_active
    camera_active = False

# =====================================================
# ATTENDANCE WORKER THREAD
# =====================================================
def attendance_worker(folder):
    global attendance_active, attendance_status_text, latest_frame_bytes
    print("[ATTENDANCE] Worker started")
    attendance_active = True
    
    try:
        for frame, status in attend.run_attendance(folder):
            if not attendance_active:
                break
                
            attendance_status_text = status
            
            if frame is not None:
                try:
                    # Convert RGB to BGR for OpenCV encoding
                    small = cv2.resize(frame, (400, 300))
                    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
                    _, jpeg = cv2.imencode('.jpg', bgr)
                    with frame_lock:
                        latest_frame_bytes = jpeg.tobytes()
                except Exception as e:
                    print(f"[ATTENDANCE] Frame update error: {e}")
    except Exception as e:
        print(f"[ATTENDANCE] Thread exception: {e}")
        attendance_status_text = f"❌ Error: {str(e)}"
    finally:
        attend.stop_attendance()
        attendance_active = False
        with frame_lock:
            latest_frame_bytes = None
        print("[ATTENDANCE] Worker stopped")

# =====================================================
# FACE REGISTRATION CAPTURE THREAD
# =====================================================
def capture_worker(usn, name, course, password):
    global capture_active, capture_status_text, latest_frame_bytes
    print("[CAPTURE] Worker started")
    capture_active = True
    capture_status_text = "Starting capture loop..."
    
    try:
        # Pass cap=None to let attend.py open the device itself
        for frame, status_msg in attend.capture_face_stream(usn, name, course, password, cap=None):
            if not capture_active:
                break
                
            capture_status_text = status_msg
            
            if frame is not None:
                try:
                    small = cv2.resize(frame, (400, 300))
                    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
                    _, jpeg = cv2.imencode('.jpg', bgr)
                    with frame_lock:
                        latest_frame_bytes = jpeg.tobytes()
                except Exception as e:
                    print(f"[CAPTURE] Frame update error: {e}")
                    
        if capture_active and "✅" in capture_status_text:
            capture_status_text = "⏳ Training face recognition model... Please wait..."
            attend.encode_faces()
            capture_status_text = "✅ Registered student & model trained successfully!"
    except Exception as e:
        print(f"[CAPTURE] Thread exception: {e}")
        capture_status_text = f"❌ Error: {str(e)}"
    finally:
        capture_active = False
        with frame_lock:
            latest_frame_bytes = None
        print("[CAPTURE] Worker stopped")

# =====================================================
# TRANSCRIPTION CACHING
# =====================================================
def get_session_transcription(folder):
    if not folder:
        return None, "❌ Invalid session folder"
        
    audio_path = os.path.join(folder, "audio", "lecture.wav")
    transcription_path = os.path.join(folder, "audio", "transcription.txt")
    
    if os.path.exists(transcription_path):
        try:
            print(f"[INFO] Using cached transcription from {transcription_path}")
            with open(transcription_path, "r", encoding="utf-8") as f:
                cached_text = f.read().strip()
                if cached_text:
                    return cached_text, None
        except Exception as e:
            print(f"[WARNING] Failed to read cached transcription: {e}")
            
    if not os.path.exists(audio_path):
        return None, "❌ Audio file not found"
        
    print(f"[INFO] Transcription cache miss. Querying server...")
    try:
        with open(audio_path, "rb") as audio_file:
            response = requests.post(
                f"{LAPTOP_SERVER_URL}/transcribe",
                files={"file": audio_file},
                timeout=600
            )
            
        if response.status_code != 200 or not response.text.strip():
            return None, f"❌ Server transcription error: {response.status_code}"
            
        text = response.json().get("transcription", "").strip()
        if not text:
            return None, "❌ Empty transcription returned from server"
            
        try:
            with open(transcription_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[SUCCESS] Transcription cached at {transcription_path}")
        except Exception as e:
            print(f"[WARNING] Failed to cache transcription: {e}")
            
        return text, None
    except Exception as e:
        return None, f"❌ Request to server failed: {str(e)}"

# =====================================================
# FASTAPI APP ROUTER
# =====================================================
app = FastAPI(title="GMU Smart Classroom Dashboard")

# HTML Template serving
@app.get("/", response_class=HTMLResponse)
def get_root():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()

# Dynamic Image Polling Route
@app.get("/latest_frame")
def get_latest_frame():
    global latest_frame_bytes
    with frame_lock:
        frame_bytes = latest_frame_bytes
        
    if frame_bytes is None:
        # Return a nice dark placeholder image
        img = np.zeros((300, 400, 3), dtype=np.uint8)
        cv2.rectangle(img, (10, 10), (390, 290), (90, 31, 34), 2)
        cv2.putText(img, "Camera Offline", (110, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (229, 195, 101), 2, cv2.LINE_AA)
        cv2.putText(img, "Start Preview or Attendance", (70, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1, cv2.LINE_AA)
        _, jpeg = cv2.imencode('.jpg', img)
        frame_bytes = jpeg.tobytes()
        
    return Response(content=frame_bytes, media_type="image/jpeg")

# File Download Endpoint
@app.get("/download/{filename}")
def download_file(filename: str):
    # Walk the directory tree safely to prevent directory traversal
    for root, dirs, files in os.walk(BASE_DIR):
        if filename in files:
            file_path = os.path.join(root, filename)
            if os.path.abspath(file_path).startswith(os.path.abspath(BASE_DIR)):
                return FileResponse(file_path, filename=filename)
    raise HTTPException(status_code=404, detail="File not found")

# =====================================================
# REST API DEFINITIONS
# =====================================================
class TeacherLogin(BaseModel):
    username: str
    password: str

class StudentLogin(BaseModel):
    usn: str
    password: str

class RegisterStudent(BaseModel):
    usn: str
    name: str
    course: str
    password: str

class DeleteStudent(BaseModel):
    usn: str

class SessionConfig(BaseModel):
    subject: str
    duration: int

class AudioConfig(BaseModel):
    device: str
    duration: int

class StudentAction(BaseModel):
    usn: str

class NotesAction(BaseModel):
    session: str

class AIAction(BaseModel):
    session: str
    mode: str
    prompt: str = ""

class PDFGenAction(BaseModel):
    text: str

# Auth endpoints
@app.post("/api/login")
def login_teacher(data: TeacherLogin):
    if data.username in TEACHERS and TEACHERS[data.username]["password"] == data.password:
        session_state["logged_in"] = True
        session_state["teacher"] = TEACHERS[data.username]["name"]
        session_state["username"] = data.username
        return {"success": True, "name": TEACHERS[data.username]["name"]}
    return {"success": False, "message": "❌ Invalid teacher credentials"}

@app.post("/api/student_login")
def login_student(data: StudentLogin):
    registry = attend.load_registry()
    usn_upper = data.usn.upper()
    if usn_upper not in registry:
        return {"success": False, "message": "❌ USN not registered"}
    if registry[usn_upper].get("password") != data.password:
        return {"success": False, "message": "❌ Incorrect password"}
    return {"success": True}

@app.post("/api/admin_login")
def login_admin(data: TeacherLogin):
    if data.username in ADMINS and ADMINS[data.username]["password"] == data.password:
        return {"success": True}
    return {"success": False, "message": "❌ Invalid admin credentials"}

# Session endpoints
@app.post("/api/start_session")
def start_session_api(config: SessionConfig):
    now = datetime.now()
    end = now + timedelta(minutes=config.duration)
    folder = os.path.join(SESSION_ROOT, now.strftime("%Y-%m-%d"), f"{config.subject}_{now.strftime('%H%M')}")
    os.makedirs(folder, exist_ok=True)
    os.makedirs(os.path.join(folder, "audio"), exist_ok=True)

    session_state["audio_path"] = os.path.join(folder, "audio", "lecture.wav")
    session_state["subject"] = config.subject
    session_state["start"] = now
    session_state["end"] = end
    session_state["folder"] = folder

    save_session({
        "subject": config.subject,
        "teacher": session_state["teacher"],
        "folder": folder,
        "time": now.strftime("%Y-%m-%d %H:%M")
    })

    info = (
        f"Subject  : {config.subject}\n"
        f"Teacher  : {session_state['teacher']}\n"
        f"Start    : {now.strftime('%H:%M')}\n"
        f"End      : {end.strftime('%H:%M')}\n"
        f"Folder   : {folder}"
    )
    return {"success": True, "data": info}

# Live Attendance
@app.post("/api/start_attendance")
def start_attendance_api():
    global attendance_active, attendance_thread
    if not session_state["folder"]:
        return {"success": False, "message": "❌ Start session first"}
        
    stop_camera()  # Stop preview to release device lock
    time.sleep(0.5)
    
    attendance_active = True
    attendance_thread = threading.Thread(target=attendance_worker, args=(session_state["folder"],), daemon=True)
    attendance_thread.start()
    return {"success": True, "message": "▶ Attendance process started"}

@app.post("/api/stop_attendance")
def stop_attendance_api():
    global attendance_active
    attendance_active = False
    time.sleep(0.3)
    return {"success": True, "message": "⏹ Attendance stopped successfully\n✅ Database updated"}

@app.get("/api/attendance_status")
def get_attendance_status():
    global attendance_active, attendance_status_text
    return {"running": attendance_active, "status": attendance_status_text}

# Audio Devices & Recording
@app.get("/api/audio_devices")
def get_audio_devices():
    return {"devices": audio_module.get_input_devices()}

@app.post("/api/start_recording")
def start_recording_api(config: AudioConfig):
    if not session_state["audio_path"]:
        return {"success": False, "message": "❌ Start session first"}
    try:
        device_index = int(config.device.split(" - ")[0])
        status = audio_module.start_recording(session_state["audio_path"], device_index, config.duration)
        return {"success": True, "message": status}
    except Exception as e:
        return {"success": False, "message": f"❌ Error: {str(e)}"}

@app.post("/api/stop_recording")
def stop_recording_api():
    status = audio_module.stop_recording()
    return {"success": True, "message": status}

# Student Workspace Actions
@app.post("/api/load_attendance")
def load_attendance_api(action: StudentAction):
    usn = action.usn.upper()
    sessions = load_sessions()
    result = ""
    for s in sessions:
        attendance_file = os.path.join(s["folder"], "attendance.csv")
        if not os.path.exists(attendance_file):
            continue
        with open(attendance_file, "r") as f:
            lines = f.readlines()[1:]
            for line in lines:
                row = line.strip().split(",")
                if len(row) < 4:
                    continue
                saved_usn, name, time, status = row[0], row[1], row[2], row[3]
                if saved_usn == usn:
                    result += (
                        f"Subject : {s['subject']}\n"
                        f"Date    : {s['time']}\n"
                        f"Name    : {name}\n"
                        f"Time    : {time}\n"
                        f"Status  : {status}\n"
                        f"{'─' * 36}\n"
                    )
    return {"data": result if result else "❌ No attendance database records found"}

@app.get("/api/load_sessions")
def load_sessions_api():
    sessions = load_sessions()
    session_list = [{"label": f"{s['subject']} | {s['time']}", "folder": s["folder"]} for s in sessions]
    return {"sessions": session_list}

@app.post("/api/generate_notes")
def generate_notes_api(action: NotesAction):
    folder = get_selected_folder(action.session)
    if not folder:
        return {"success": False, "data": "❌ No session selected"}
    text, err = get_session_transcription(folder)
    if err:
        return {"success": False, "data": err}
    return {"success": True, "data": text}

@app.post("/api/download_pdf")
def download_pdf_api(action: PDFGenAction):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    
    if not action.text:
        return {"success": False}
        
    pdf_path = "AI_Notes.pdf"
    doc = SimpleDocTemplate(pdf_path)
    styles = getSampleStyleSheet()
    content = [Paragraph("Audio Transcription Notes", styles['Title']), Spacer(1, 20)]
    for line in action.text.split("\n"):
        if line.strip():
            content.append(Paragraph(line, styles['BodyText']))
            content.append(Spacer(1, 10))
    doc.build(content)
    return {"success": True, "filename": pdf_path}

@app.post("/api/generate_ai_content")
def generate_ai_content_api(action: AIAction):
    folder = get_selected_folder(action.session)
    if not folder:
        return {"success": False, "data": "❌ No session selected"}
    text, err = get_session_transcription(folder)
    if err:
        return {"success": False, "data": err}
        
    try:
        ai_response = requests.post(
            f"{LAPTOP_SERVER_URL}/generate_notes",
            json={
                "text": text,
                "mode": action.mode,
                "custom_prompt": action.prompt or ""
            },
            timeout=600
        )
        if ai_response.status_code != 200:
            return {"success": False, "data": f"❌ AI Server error: {ai_response.status_code}"}
        return {"success": True, "data": ai_response.json()["notes"]}
    except Exception as e:
        return {"success": False, "data": f"❌ Server connection failed: {str(e)}"}

@app.post("/api/download_ai_pdf")
def download_ai_pdf_api(action: PDFGenAction):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    
    if not action.text:
        return {"success": False}
        
    pdf_path = "AI_Generated_Notes.pdf"
    doc = SimpleDocTemplate(pdf_path)
    styles = getSampleStyleSheet()
    content = [Paragraph("AI Generated Study Notes", styles['Title']), Spacer(1, 20)]
    for line in action.text.split("\n"):
        if line.strip():
            content.append(Paragraph(line, styles['BodyText']))
            content.append(Spacer(1, 10))
    doc.build(content)
    return {"success": True, "filename": pdf_path}

@app.post("/api/generate_ppt")
def generate_ppt_api(action: NotesAction):
    folder = get_selected_folder(action.session)
    if not folder:
        return {"success": False, "message": "❌ No session selected"}
    text, err = get_session_transcription(folder)
    if err:
        return {"success": False, "message": err}
        
    try:
        ppt_response = requests.post(
            f"{LAPTOP_SERVER_URL}/generate_ppt",
            json={"text": text},
            stream=True,
            timeout=600
        )
        if ppt_response.status_code != 200:
            return {"success": False, "message": f"❌ PPT generation failed: {ppt_response.status_code}"}
            
        output_filename = "Lecture_Presentation.pptx"
        output_path = os.path.join(folder, output_filename)
        with open(output_path, "wb") as f:
            for chunk in ppt_response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        return {"success": True, "message": f"✅ PowerPoint presentation generated!", "filename": output_filename}
    except Exception as e:
        return {"success": False, "message": f"❌ Request failed: {str(e)}"}

@app.post("/api/generate_quiz")
def generate_quiz_api(action: NotesAction):
    folder = get_selected_folder(action.session)
    if not folder:
        return {"success": False, "message": "❌ No session selected"}
    text, err = get_session_transcription(folder)
    if err:
        return {"success": False, "message": err}
        
    try:
        quiz_response = requests.post(
            f"{LAPTOP_SERVER_URL}/generate_quiz",
            json={"text": text},
            timeout=600
        )
        if quiz_response.status_code != 200:
            return {"success": False, "message": f"❌ Quiz generation failed: {quiz_response.status_code}"}
            
        data = quiz_response.json()
        questions = data.get("questions", [])
        if len(questions) < 5:
            return {"success": False, "message": "❌ Received less than 5 questions from server."}
            
        quiz_state = []
        for i in range(5):
            q = questions[i]
            quiz_state.append({
                "question": q["question"],
                "options": q["options"],
                "correct_index": q["correct_index"]
            })
            
        return {"success": True, "message": "📝 Quiz ready!", "quiz_state": quiz_state}
    except Exception as e:
        return {"success": False, "message": f"❌ Request failed: {str(e)}"}

# Face Registration Controllers
@app.post("/api/start_preview")
def start_preview_api():
    stop_camera()
    time.sleep(0.5)
    start_camera()
    return {"success": True, "message": "🎥 Preview active"}

@app.post("/api/stop_preview")
def stop_preview_api():
    stop_camera()
    return {"success": True, "message": "⏹ Preview stopped"}

@app.post("/api/register_student")
def register_student_api(data: RegisterStudent):
    global capture_active, capture_thread
    registry = attend.load_registry()
    usn_upper = data.usn.upper()
    
    if usn_upper in registry:
        return {"success": False, "message": f"❌ USN already exists: {usn_upper}"}
        
    stop_camera()
    time.sleep(0.5)
    
    capture_active = True
    capture_thread = threading.Thread(
        target=capture_worker, 
        args=(usn_upper, data.name.upper(), data.course.upper(), data.password), 
        daemon=True
    )
    capture_thread.start()
    return {"success": True, "message": "📸 Registration capture session initialized"}

@app.get("/api/capture_status")
def get_capture_status():
    global capture_active, capture_status_text
    return {"running": capture_active, "status": capture_status_text}

@app.post("/api/delete_student")
def delete_student_api(data: DeleteStudent):
    try:
        result = attend.delete_student(data.usn)
        attend.encode_faces()
        return {"success": True, "message": f"{result}\n✅ Model retrained"}
    except Exception as e:
        return {"success": False, "message": f"❌ Error: {str(e)}"}

# Insights Analytics Route
@app.get("/api/analytics", response_class=HTMLResponse)
def get_analytics():
    return analytics.generate_insights_html()

# =====================================================
# STARTUP RUNNER
# =====================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mainui:app", host="0.0.0.0", port=7860, reload=False)