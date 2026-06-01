import gradio as gr
import os
from datetime import datetime, timedelta
import attend  # your backend module
import audio_module
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import requests
import analytics


DEVICE_LIST = audio_module.get_input_devices()


def to_upper(text):
    if text:
        return text.upper()
    return text


# =====================================================
# CONFIG
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
import json

SESSION_DB = os.path.join(SESSION_ROOT, "sessions.json")
if not os.path.exists(SESSION_DB):
    with open(SESSION_DB, "w") as f:
        json.dump([], f)

# =====================================================
# AI NODE CONFIG  (update IP here only)
# =====================================================
LAPTOP_SERVER_URL = "http://localhost:8000"  # Update this to your laptop's IP (e.g. "http://192.168.1.100:8000")
# =====================================================
# STATE
# =====================================================

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


def load_sessions():
    if not os.path.exists(SESSION_DB):
        return []

    try:
        with open(SESSION_DB, "r") as f:
            data = json.load(f)

        # remove invalid/deleted folders automatically
        valid_sessions = []

        for s in data:
            folder = s.get("folder")

            if folder and os.path.exists(folder):
                valid_sessions.append(s)

        # auto-clean corrupted entries
        with open(SESSION_DB, "w") as f:
            json.dump(valid_sessions, f, indent=4)

        return valid_sessions

    except Exception as e:
        print("SESSION LOAD ERROR:", e)

        # reset corrupted json
        with open(SESSION_DB, "w") as f:
            json.dump([], f)

        return []


def save_session(entry):
    data = load_sessions()
    data.append(entry)
    with open(SESSION_DB, "w") as f:
        json.dump(data, f, indent=4)


def get_sessions_dropdown():
    sessions = load_sessions()
    if not sessions:
        return gr.update(choices=[], value=None)
    choices = [f"{s['subject']} | {s['time']}" for s in sessions]
    return gr.update(choices=choices, value=choices[0])


def get_selected_folder(selected):
    sessions = load_sessions()
    for s in sessions:
        label = f"{s['subject']} | {s['time']}"
        if label == selected:
            return s["folder"]
    return None


# =====================================================
# NAVIGATION
# =====================================================

def go_to(section):
    return (
        gr.update(visible=(section == "student")),
        gr.update(visible=(section == "teacher")),
        gr.update(visible=(section == "register")),
    )


def reset_all_panels(selected_section):
    session_state["logged_in"] = False
    session_state["teacher"] = ""
    session_state["username"] = ""

    return (
        gr.update(visible=(selected_section == "student")),
        gr.update(visible=(selected_section == "teacher")),
        gr.update(visible=(selected_section == "register")),
        gr.update(visible=(selected_section == "insights")),

        gr.update(visible=False),  # home_banner

        gr.update(visible=False),  # student_dashboard
        gr.update(visible=False),  # session_panel
        gr.update(visible=False),  # register_dashboard

        "",
        "",
        "",
        "",
        "",
        ""
    )


# =====================================================
# TEACHER FUNCTIONS
# =====================================================

def login(username, password):
    if username in TEACHERS and TEACHERS[username]["password"] == password:
        session_state["logged_in"] = True
        session_state["teacher"] = TEACHERS[username]["name"]
        session_state["username"] = username
        return "Login success", gr.update(visible=False), gr.update(visible=True)
    return "Invalid login", gr.update(visible=True), gr.update(visible=False)


def teacher_logout():
    session_state["logged_in"] = False
    session_state["teacher"] = ""
    session_state["username"] = ""
    return (
        "🔒 Logged out successfully",
        gr.update(visible=True),
        gr.update(visible=False),
        "",
        ""
    )


def admin_login(username, password):
    if username in ADMINS and ADMINS[username]["password"] == password:
        return (
            "✅ Admin Login Success",
            gr.update(visible=False),
            gr.update(visible=True)
        )
    return (
        "❌ Invalid Admin Login",
        gr.update(visible=True),
        gr.update(visible=False)
    )


def admin_logout():
    return (
        "🔒 Logged out successfully",
        gr.update(visible=True),
        gr.update(visible=False),
        "",
        ""
    )


def start_session(subject, duration):
    now = datetime.now()
    end = now + timedelta(minutes=int(duration))

    folder = os.path.join(
        SESSION_ROOT,
        now.strftime("%Y-%m-%d"),
        f"{subject}_{now.strftime('%H%M')}"
    )
    os.makedirs(folder, exist_ok=True)
    audio_folder = os.path.join(folder, "audio")
    os.makedirs(audio_folder, exist_ok=True)

    session_state["audio_path"] = os.path.join(audio_folder, "lecture.wav")
    session_state["subject"] = subject
    session_state["start"] = now
    session_state["end"] = end
    session_state["folder"] = folder

    save_session({
        "subject": subject,
        "teacher": session_state["teacher"],
        "folder": folder,
        "time": now.strftime("%Y-%m-%d %H:%M")
    })

    info = (
        f"Subject  : {subject}\n"
        f"Teacher  : {session_state['teacher']}\n"
        f"Start    : {now.strftime('%H:%M')}\n"
        f"End      : {end.strftime('%H:%M')}\n"
        f"Folder   : {folder}"
    )
    return "✅ Session Started", info


def run_attendance_ui(duration):
    if not session_state["folder"]:
        yield None, "❌ Start session first"
        return
    for frame, status in attend.run_attendance(session_state["folder"]):
        yield frame, status


def stop_attendance_ui():
    attend.stop_attendance()
    return (None, "🛑 Attendance stopped manually\n✅ CSV Saved")


# =====================================================
# NOTES & AI FUNCTIONS
# =====================================================

def generate_notes(selected_session):
    folder = get_selected_folder(selected_session)
    if not folder:
        return "❌ No session selected"

    audio_path = os.path.join(folder, "audio", "lecture.wav")
    if not os.path.exists(audio_path):
        return "❌ Audio file not found"

    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            f"{LAPTOP_SERVER_URL}/transcribe",
            files={"file": audio_file},
            timeout=600
        )

    if response.status_code != 200 or not response.text.strip():
        return f"❌ Server error: {response.status_code}"

    return response.json()["transcription"]


def generate_ai_content(selected_session, mode, custom_prompt):
    folder = get_selected_folder(selected_session)
    if not folder:
        return "❌ No session selected"

    audio_path = os.path.join(folder, "audio", "lecture.wav")
    if not os.path.exists(audio_path):
        return "❌ Audio not found"

    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            f"{LAPTOP_SERVER_URL}/transcribe",
            files={"file": audio_file},
            timeout=600
        )

    if response.status_code != 200 or not response.text.strip():
        return f"❌ Transcription error: {response.status_code}"

    text = response.json()["transcription"]

    # Send request to laptop server to generate AI notes
    ai_response = requests.post(
        f"{LAPTOP_SERVER_URL}/generate_notes",
        json={
            "text": text,
            "mode": mode,
            "custom_prompt": custom_prompt or ""
        },
        timeout=600
    )

    if ai_response.status_code != 200:
        return f"❌ AI Generation error: {ai_response.status_code} - {ai_response.text}"

    return ai_response.json()["notes"]


def generate_pdf(notes_text):
    if not notes_text:
        return None
    pdf_path = "AI_Notes.pdf"
    doc = SimpleDocTemplate(pdf_path)
    styles = getSampleStyleSheet()
    content = [Paragraph("Audio Transcription", styles['Title']), Spacer(1, 20)]
    for line in notes_text.split("\n"):
        content.append(Paragraph(line, styles['BodyText']))
        content.append(Spacer(1, 10))
    doc.build(content)
    return pdf_path


def generate_ai_pdf(ai_text):
    if not ai_text:
        return None
    pdf_path = "AI_Generated_Notes.pdf"
    doc = SimpleDocTemplate(pdf_path)
    styles = getSampleStyleSheet()
    content = [Paragraph("AI Generated Notes", styles['Title']), Spacer(1, 20)]
    for line in ai_text.split("\n"):
        content.append(Paragraph(line, styles['BodyText']))
        content.append(Spacer(1, 10))
    doc.build(content)
    return pdf_path


def generate_lecture_ppt(selected_session):
    folder = get_selected_folder(selected_session)
    if not folder:
        return "❌ No session selected", None

    audio_path = os.path.join(folder, "audio", "lecture.wav")
    if not os.path.exists(audio_path):
        return "❌ Audio not found", None

    # Step 1: Transcribe the audio first (or reuse if already transcribed)
    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            f"{LAPTOP_SERVER_URL}/transcribe",
            files={"file": audio_file},
            timeout=600
        )

    if response.status_code != 200 or not response.text.strip():
        return f"❌ Transcription error: {response.status_code}", None

    text = response.json()["transcription"]

    # Step 2: Request PPT generation from the laptop server
    ppt_response = requests.post(
        f"{LAPTOP_SERVER_URL}/generate_ppt",
        json={"text": text},
        stream=True,
        timeout=600
    )

    if ppt_response.status_code != 200:
        return f"❌ PPT generation failed: {ppt_response.status_code}", None

    # Step 3: Save the received binary stream locally
    output_filename = "Lecture_Presentation.pptx"
    output_path = os.path.join(folder, output_filename)
    
    with open(output_path, "wb") as f:
        for chunk in ppt_response.iter_content(chunk_size=8192):
            f.write(chunk)

    return f"✅ PowerPoint presentation generated and saved to session folder!\n📁 File: {output_filename}", output_path


def load_quiz_ui(selected_session):
    folder = get_selected_folder(selected_session)
    if not folder:
        return (
            "❌ No session selected", [],
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False)
        )

    audio_path = os.path.join(folder, "audio", "lecture.wav")
    if not os.path.exists(audio_path):
        return (
            "❌ Audio not found", [],
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False)
        )

    # Get transcription
    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            f"{LAPTOP_SERVER_URL}/transcribe",
            files={"file": audio_file},
            timeout=600
        )

    if response.status_code != 200 or not response.text.strip():
        return (
            f"❌ Transcription error: {response.status_code}", [],
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False)
        )

    text = response.json()["transcription"]

    # Query Quiz
    quiz_response = requests.post(
        f"{LAPTOP_SERVER_URL}/generate_quiz",
        json={"text": text},
        timeout=600
    )

    if quiz_response.status_code != 200:
        return (
            f"❌ Quiz generation failed: {quiz_response.status_code}", [],
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False)
        )

    data = quiz_response.json()
    questions = data.get("questions", [])
    
    if len(questions) < 5:
        return (
            "❌ Error: Received less than 5 questions from server.", [],
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False), gr.update(visible=False), gr.update(visible=False),
            gr.update(visible=False)
        )

    # Store state: questions list with correct indexes
    quiz_state = []
    updates = []

    for i in range(5):
        q = questions[i]
        question_text = f"Q{i+1}: {q['question']}"
        options = q["options"]
        correct_idx = q["correct_index"]
        
        quiz_state.append({
            "question": q["question"],
            "options": options,
            "correct_index": correct_idx
        })

        updates.append(gr.update(
            label=question_text,
            choices=options,
            value=None,
            visible=True
        ))

    return (
        "📝 Quiz ready! Answer the 5 questions below and submit.",
        quiz_state,
        updates[0],
        updates[1],
        updates[2],
        updates[3],
        updates[4],
        gr.update(visible=True),  # show submit button
        gr.update(visible=False)  # hide old results box
    )


def submit_quiz_ui(q1, q2, q3, q4, q5, quiz_state):
    if not quiz_state or len(quiz_state) != 5:
        return "❌ Quiz state is invalid. Please generate the quiz again.", gr.update(visible=False)

    user_answers = [q1, q2, q3, q4, q5]
    score = 0
    
    html_card_details = ""

    for i in range(5):
        q = quiz_state[i]
        user_ans = user_answers[i]
        correct_idx = q["correct_index"]
        correct_ans = q["options"][correct_idx]
        
        is_correct = (user_ans == correct_ans)
        if is_correct:
            score += 1
            status_badge = '<span style="color:#2e7d32; font-weight:700;">✔ Correct</span>'
            border_style = 'border-left: 5px solid #2e7d32; background: rgba(46, 125, 50, 0.03);'
        else:
            status_badge = '<span style="color:#c62828; font-weight:700;">✘ Incorrect</span>'
            border_style = 'border-left: 5px solid #c62828; background: rgba(198, 40, 40, 0.03);'

        user_ans_label = user_ans if user_ans else '<span style="color:#666; font-style:italic;">No answer selected</span>'

        html_card_details += f"""
        <div style="margin-bottom: 20px; padding: 18px; border-radius: 12px; {border_style} box-shadow: 0 2px 8px rgba(0,0,0,0.02);">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 8px;">
                <h4 style="margin:0; font-size:1.05rem; font-weight:700; color:#5a1f22;">Question {i+1}</h4>
                {status_badge}
            </div>
            <p style="margin: 0 0 12px 0; font-size:0.95rem; font-weight:600; color:#331515;">{q['question']}</p>
            <div style="font-size:0.9rem; line-height:1.6;">
                <div><b>Your Answer:</b> {user_ans_label}</div>
                <div><b>Correct Answer:</b> <span style="color:#2e7d32; font-weight:600;">{correct_ans}</span></div>
            </div>
        </div>
        """

    score_color = '#2e7d32' if score >= 4 else ('#d8b958' if score >= 3 else '#c62828')
    scorecard_html = f"""
    <div style="font-family:'Outfit', 'Inter', sans-serif; padding: 10px 0;">
        <div style="text-align: center; margin-bottom: 30px; padding: 25px; background: rgba(229,195,101,0.08); border: 1.5px solid rgba(229,195,101,0.3); border-radius: 18px;">
            <h3 style="margin:0; font-size:1.4rem; font-weight:800; color:#5a1f22;">📊 Quiz Assessment Completed</h3>
            <div style="font-size: 3.5rem; font-weight: 800; color: {score_color}; margin: 15px 0;">
                {score} / 5
            </div>
            <p style="margin:0; font-size:1.05rem; font-weight:600; color:#331515;">
                {"🏆 Outstanding performance!" if score == 5 else ("👍 Well done! Keep it up." if score >= 3 else "📚 Time to review the lecture notes again.")}
            </p>
        </div>
        
        <div>
            {html_card_details}
        </div>
    </div>
    """

    return scorecard_html, gr.update(visible=True)


# =====================================================
# STUDENT FUNCTIONS
# =====================================================

def student_login(usn, password):
    registry = attend.load_registry()
    usn = usn.upper()
    if usn not in registry:
        return ("❌ Invalid USN", gr.update(visible=True), gr.update(visible=False))
    if registry[usn].get("password") != password:
        return ("❌ Incorrect Password", gr.update(visible=True), gr.update(visible=False))
    return ("✅ Login Success", gr.update(visible=False), gr.update(visible=True))


def student_logout():
    return (
        "🔒 Logged out",
        gr.update(visible=True),
        gr.update(visible=False),
        "",
        ""
    )


def load_student_attendance(usn):
    usn = usn.upper()
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
    return result if result else "❌ No attendance records found"


# =====================================================
# REGISTER FACE FUNCTIONS
# =====================================================

def validate_and_capture(usn, name, course, student_password):
    usn = usn.upper()
    name = name.upper()
    course = course.upper()
    registry = attend.load_registry()

    if not usn or not name or not course or not student_password:
        yield None, "❌ Fill all fields", usn, name, course, student_password
        return

    if usn in registry:
        yield None, f"❌ USN already exists: {usn}", usn, name, course, student_password
        return

    # Call the generator from attend.py
    last_frame = None
    msg = ""
    for frame, status_msg in attend.capture_face_stream(usn, name, course, student_password):
        last_frame = frame
        msg = status_msg
        # Yield the current progress frame and status, leaving inputs intact
        yield frame, status_msg, usn, name, course, student_password

    if last_frame is not None and "✅" in msg:
        yield last_frame, "⏳ Training face recognition model... Please wait...", usn, name, course, student_password
        try:
            attend.encode_faces()
            msg += "\n✅ Model trained successfully"
        except Exception as e:
            msg += f"\n⚠️ Model training error: {str(e)}"
        
        # Clear fields on success at the very end
        yield last_frame, msg, "", "", "", ""
    else:
        # Keep fields on failure
        yield last_frame, msg, usn, name, course, student_password


def start_preview_ui():
    for frame in attend.start_face_preview():
        if frame is None:
            yield None, "❌ Camera preview failed"
            break
        yield frame, "🎥 Preview active (Framing/Lighting check)"


def stop_preview_ui():
    attend.stop_face_preview()
    return None, "⏹ Preview stopped"


# =====================================================
# CUSTOM CSS  (theme, animations, fonts preserved)
# =====================================================

custom_css = """
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Inter:wght@400;500;600&display=swap');

body, gradio-app {
    font-family: 'Outfit', 'Inter', sans-serif !important;
    background: #fdfbf7 !important;
    color: #331515 !important;
}

.dark body, .dark gradio-app {
    background: #1a0a0b !important;
    color: #fdfbf7 !important;
}

/* ── LAYOUT ── */
.main-container {
    max-width: 1440px !important;
    margin: 20px auto !important;
    padding: 0 !important;
    background: rgba(255,255,255,0.8) !important;
    backdrop-filter: blur(12px) !important;
    border-radius: 24px !important;
    box-shadow: 0 8px 32px rgba(90,31,34,0.08) !important;
    border: 1px solid rgba(229,195,101,0.4) !important;
    overflow: hidden !important;
}

.dark .main-container {
    background: rgba(45,15,17,0.7) !important;
    border: 1px solid rgba(229,195,101,0.15) !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4) !important;
}

/* ── SIDEBAR ── */
.sidebar {
    background: #5a1f22 !important;
    border-radius: 0 !important;
    padding: 32px 20px 24px !important;
    display: flex !important;
    flex-direction: column !important;
    gap: 8px !important;
    min-height: 100vh !important;
    box-shadow: 4px 0 24px rgba(90,31,34,0.2) !important;
}

.dark .sidebar {
    background: #3d1416 !important;
}

.sidebar h3 {
    font-weight: 700 !important;
    font-size: 1.25rem !important;
    margin-bottom: 28px !important;
    text-align: center !important;
    background: linear-gradient(90deg, #e5c365, #fce594, #d8b958);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.3px !important;
}

.nav-btn {
    border: 1px solid rgba(229,195,101,0.15) !important;
    border-radius: 10px !important;
    padding: 13px 18px !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    text-align: left !important;
    transition: all 0.25s cubic-bezier(0.4,0,0.2,1) !important;
    background: transparent !important;
    color: rgba(252,229,148,0.85) !important;
    position: relative !important;
    overflow: hidden !important;
}

.nav-btn:hover {
    background: linear-gradient(90deg, #e5c365, #fce594, #d8b958) !important;
    color: #5a1f22 !important;
    transform: translateX(5px) !important;
    box-shadow: 0 4px 14px rgba(229,195,101,0.35) !important;
    border-color: transparent !important;
}

.sidebar-footer {
    font-size: 0.78rem;
    color: rgba(216,185,88,0.7);
    text-align: center;
    margin-top: auto;
    padding-top: 24px;
    border-top: 1px solid rgba(229,195,101,0.2);
    font-weight: 500;
    line-height: 1.6;
}

/* ── MAIN CONTENT ── */
.main-content {
    padding: 36px 44px !important;
    animation: fadeIn 0.45s ease-out forwards !important;
}

.dashboard-header {
    border-bottom: 1.5px solid rgba(90,31,34,0.12) !important;
    padding-bottom: 20px !important;
    margin-bottom: 32px !important;
}

.dark .dashboard-header {
    border-bottom: 1.5px solid rgba(229,195,101,0.15) !important;
}

.dashboard-header h1 {
    font-size: 2rem !important;
    font-weight: 800 !important;
    margin: 0 0 8px 0 !important;
    letter-spacing: -0.8px !important;
    color: #5a1f22 !important;
}

.dark .dashboard-header h1 {
    background: linear-gradient(90deg, #e5c365, #fce594, #d8b958);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.dashboard-header p {
    color: rgba(51,21,21,0.55) !important;
    font-size: 0.95rem !important;
    margin: 0 !important;
}

.dark .dashboard-header p {
    color: rgba(252,229,148,0.5) !important;
}

/* ── CARDS ── */
.custom-card {
    border-radius: 16px !important;
    border: 1px solid rgba(90,31,34,0.08) !important;
    padding: 24px 28px !important;
    margin-bottom: 20px !important;
    background: #ffffff !important;
    box-shadow: 0 2px 12px rgba(90,31,34,0.04) !important;
    transition: transform 0.25s ease, box-shadow 0.25s ease !important;
}

.custom-card:hover {
    transform: translateY(-3px) !important;
    box-shadow: 0 8px 24px rgba(90,31,34,0.08) !important;
}

.dark .custom-card {
    border: 1px solid rgba(229,195,101,0.12) !important;
    background: rgba(61,20,22,0.55) !important;
    box-shadow: 0 2px 12px rgba(0,0,0,0.2) !important;
}

.dark .custom-card:hover {
    box-shadow: 0 8px 28px rgba(0,0,0,0.35) !important;
}

/* ── SECTION HEADINGS inside cards ── */
.custom-card h3 {
    font-size: 1rem !important;
    font-weight: 700 !important;
    color: #5a1f22 !important;
    margin: 0 0 18px 0 !important;
    letter-spacing: 0.2px !important;
    text-transform: uppercase !important;
    opacity: 0.85 !important;
}

.dark .custom-card h3 {
    color: #e5c365 !important;
    opacity: 1 !important;
}

/* ── BUTTONS ── */
button.primary {
    background: #5a1f22 !important;
    border: 1px solid #7a2a2e !important;
    color: #fce594 !important;
    font-weight: 700 !important;
    border-radius: 10px !important;
    padding: 10px 20px !important;
    box-shadow: 0 3px 10px rgba(90,31,34,0.25) !important;
    transition: all 0.25s ease !important;
    font-size: 0.9rem !important;
}

button.primary:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 16px rgba(90,31,34,0.4) !important;
    background: #6b2529 !important;
    color: #ffffff !important;
}

button.secondary {
    background: linear-gradient(135deg, #e5c365 0%, #fce594 100%) !important;
    color: #5a1f22 !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    font-size: 0.9rem !important;
    transition: all 0.25s ease !important;
    box-shadow: 0 3px 10px rgba(229,195,101,0.25) !important;
}

.dark button.secondary {
    background: linear-gradient(135deg, #d8b958 0%, #e5c365 100%) !important;
    color: #3d1416 !important;
}

button.secondary:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 16px rgba(229,195,101,0.45) !important;
}

button.stop {
    background: #c62828 !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    box-shadow: 0 3px 10px rgba(198,40,40,0.25) !important;
    transition: all 0.25s ease !important;
}

button.stop:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 16px rgba(198,40,40,0.45) !important;
    background: #b71c1c !important;
}

/* ── INPUTS ── */
textarea, input[type="text"], input[type="password"], input[type="number"], select {
    border-radius: 9px !important;
    border: 1.5px solid rgba(229,195,101,0.5) !important;
    background-color: #fdfbf7 !important;
    color: #331515 !important;
    padding: 10px 14px !important;
    transition: all 0.2s ease !important;
    font-family: 'Outfit', sans-serif !important;
    font-size: 0.9rem !important;
}

textarea:focus, input:focus, select:focus {
    border-color: #5a1f22 !important;
    box-shadow: 0 0 0 3px rgba(90,31,34,0.12) !important;
    background-color: #ffffff !important;
    outline: none !important;
}

.dark textarea, .dark input[type="text"],
.dark input[type="password"], .dark input[type="number"], .dark select,
body.dark textarea, body.dark input, body.dark select,
[data-theme="dark"] textarea, [data-theme="dark"] input, [data-theme="dark"] select,
gradio-app.dark textarea, gradio-app.dark input, gradio-app.dark select {
    border: 1.5px solid rgba(122,42,46,0.6) !important;
    background-color: #2b0e10 !important;
    color: #fce594 !important;
}

.dark textarea:focus, .dark input:focus, .dark select:focus,
body.dark textarea:focus, body.dark input:focus, body.dark select:focus,
[data-theme="dark"] textarea:focus, [data-theme="dark"] input:focus, [data-theme="dark"] select:focus,
gradio-app.dark textarea:focus, gradio-app.dark input:focus, gradio-app.dark select:focus {
    border-color: #d8b958 !important;
    box-shadow: 0 0 0 3px rgba(229,195,101,0.18) !important;
    background-color: #1a080a !important;
}

/* ── STATUS TEXTBOX ── */
.status-box textarea {
    font-size: 0.85rem !important;
    color: #5a1f22 !important;
    background: rgba(229,195,101,0.08) !important;
    border-color: rgba(229,195,101,0.3) !important;
}

.dark .status-box textarea {
    color: #fce594 !important;
    background: rgba(90,31,34,0.25) !important;
}

/* ── ANIMATIONS ── */
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
}

.main-content { animation: fadeIn 0.45s ease-out forwards !important; }

/* ── INSTANT UPPERCASE INPUT ── */
.uppercase-input input,
.uppercase-input textarea {
    text-transform: uppercase !important;
}
"""

# =====================================================
# UI LAYOUT
# =====================================================

with gr.Blocks(
    title="GMU Smart app",
    css=custom_css,
    theme=gr.themes.Soft(
        font=[gr.themes.GoogleFont("Outfit"), gr.themes.GoogleFont("Inter"), "sans-serif"],
        primary_hue="amber",
        secondary_hue="rose",
        neutral_hue="stone",
        radius_size=gr.themes.sizes.radius_lg
    )
) as app:

    with gr.Row(elem_classes=["main-container"]):

        # ── SIDEBAR ──────────────────────────────────
        with gr.Column(scale=1, min_width=230, elem_classes=["sidebar"]):
            gr.Markdown("### GM UNIVERSITY")
            student_btn  = gr.Button("👨‍🎓  Student Portal",    elem_classes=["nav-btn"])
            teacher_btn  = gr.Button("👩‍🏫  Teacher Portal",    elem_classes=["nav-btn"])
            register_btn = gr.Button("🧾  Face Registration", elem_classes=["nav-btn"])
            insights_btn = gr.Button("📈  Executive Insights", elem_classes=["nav-btn"])
            gr.Markdown(
                "<div class='sidebar-footer'>Smart Classroom v2.0<br>Minimal Edition</div>"
            )

        # ── MAIN CONTENT ─────────────────────────────
        with gr.Column(scale=4, elem_classes=["main-content"]):

            # Header
            with gr.Group(visible=True) as home_banner:

                with gr.Group(elem_classes=["dashboard-header"]):

                    gr.HTML("""
                    <div style="
                    display:flex;
                    align-items:center;
                    gap:18px;
                    margin-bottom:10px;
                    ">
                        <div>
                            <h1 style="
                            margin:0;
                            font-size:38px;
                            font-weight:800;
                            ">
                            🎓 Smart Classroom Dashboard
                            </h1>

                            <p style="
                            margin-top:8px;
                            font-size:16px;
                            opacity:0.8;
                            ">
                            AI-Powered Attendance, Lecture Recording,
                            Audio Transcription, and Smart Note Generation Platform.
                            </p>
                        </div>
                    </div>
                    """)

                    gr.Markdown("""
                    <div style="
                    background:rgba(229,195,101,0.12);
                    border-left:5px solid #e5c365;
                    padding:14px 18px;
                    border-radius:12px;
                    margin-top:12px;
                    line-height:1.7;
                    font-size:15px;
                    ">

                    <b>📌 Quick Instructions</b><br><br>

                    • Teachers can start live attendance sessions and record lectures.<br>
                    • Students can access attendance reports and AI-generated lecture notes.<br>
                    • Admins can register and manage student face profiles securely.<br>
                    • Ensure camera and microphone permissions are enabled before starting sessions.<br>
                    • Use the sidebar to navigate between portals.

                    </div>
                    """)

            # ══════════════════════════════════════════
            # STUDENT PANEL
            # ══════════════════════════════════════════
            with gr.Group(visible=False) as student_panel:
                student_auth_status = gr.Textbox(
                    label="Status", interactive=False, elem_classes=["status-box"]
                )

                # LOGIN
                with gr.Group(visible=True) as student_login_panel:
                    with gr.Column(elem_classes=["custom-card"]):
                        gr.Markdown("### 👨‍🎓 Student Authentication")
                        with gr.Row():
                            with gr.Column():
                                student_usn_login = gr.Textbox(
                                    label="USN", placeholder="Enter your USN",
                                    elem_classes=["uppercase-input"]
                                )
                            with gr.Column():
                                student_password_login = gr.Textbox(
                                    label="Password", type="password",
                                    placeholder="Enter your password"
                                )
                        student_login_btn = gr.Button("Login", variant="primary")

                # DASHBOARD
                with gr.Group(visible=False) as student_dashboard:
                    with gr.Row(equal_height=True):
                        gr.Markdown("## 🎓 Student Dashboard")
                        student_logout_btn = gr.Button(
                            "Logout", variant="secondary", scale=0, min_width=100
                        )

                    with gr.Tabs():

                        # ATTENDANCE TAB
                        with gr.Tab("📋 Attendance"):
                            with gr.Column(elem_classes=["custom-card"]):
                                gr.Markdown("### 📋 Your Attendance Records")
                                load_attendance_btn = gr.Button(
                                    "Load Attendance", variant="primary"
                                )
                                attendance_info = gr.Textbox(
                                    label="Attendance Records",
                                    lines=12,
                                    show_copy_button=True
                                )

                        # NOTES TAB
                        with gr.Tab("🎙️ Lecture Notes"):
                            with gr.Column(elem_classes=["custom-card"]):
                                gr.Markdown("### 🎙️ Transcription")
                                with gr.Row(equal_height=True):
                                    session_list = gr.Dropdown(
                                        label="Select Session", scale=3
                                    )
                                    refresh_sessions_btn = gr.Button(
                                        "↻ Load Sessions",
                                        variant="secondary",
                                        scale=1,
                                        min_width=140
                                    )

                                notes_output = gr.Textbox(
                                    label="Transcribed Notes",
                                    lines=12,
                                    show_copy_button=True
                                )

                                with gr.Row():
                                    generate_notes_btn = gr.Button(
                                        "Generate Notes", variant="primary"
                                    )
                                    download_pdf_btn = gr.Button(
                                        "⬇ Download PDF", variant="secondary"
                                    )

                                pdf_output = gr.File(label="PDF Download")

                            # Advanced AI
                            with gr.Accordion("✨ Advanced AI Generation Tools", open=False):
                                with gr.Column(elem_classes=["custom-card"]):
                                    gr.Markdown("### ✨ AI Content Generator")
                                    with gr.Row(equal_height=True):
                                        ai_mode = gr.Dropdown(
                                            choices=[
                                                "Summary",
                                                "Detailed Notes",
                                                "Important Questions",
                                                "Key Points",
                                                "Explain Simply"
                                            ],
                                            value="Summary",
                                            label="AI Mode",
                                            scale=1
                                        )
                                        custom_prompt = gr.Textbox(
                                            label="Custom Prompt",
                                            placeholder="Ask anything specific about the lecture...",
                                            scale=2
                                        )

                                    generate_ai_btn = gr.Button(
                                        "Generate AI Content", variant="primary"
                                    )
                                    ai_output = gr.Textbox(
                                        label="AI Generated Content",
                                        lines=12,
                                        show_copy_button=True
                                    )
                                    with gr.Row():
                                        gr.Column()  # spacer
                                        download_ai_pdf_btn = gr.Button(
                                            "⬇ Download AI PDF",
                                            variant="secondary",
                                            scale=0,
                                            min_width=180
                                        )
                                    ai_pdf_output = gr.File(label="AI PDF Download")

                        # PPTX TAB
                        with gr.Tab("📊 Lecture PPTX"):
                            with gr.Column(elem_classes=["custom-card"]):
                                gr.Markdown("### 📊 AI PowerPoint Generator")
                                with gr.Row(equal_height=True):
                                    ppt_session_list = gr.Dropdown(
                                        label="Select Session", scale=3
                                    )
                                    refresh_ppt_sessions_btn = gr.Button(
                                        "↻ Load Sessions",
                                        variant="secondary",
                                        scale=1,
                                        min_width=140
                                    )
                                
                                generate_ppt_btn = gr.Button(
                                    "📊 Generate PowerPoint Presentation", variant="primary"
                                )
                                ppt_status = gr.Textbox(
                                    label="Generation Status",
                                    elem_classes=["status-box"]
                                )
                                ppt_file_output = gr.File(label="PowerPoint Download")

                        # QUIZ TAB
                        with gr.Tab("📝 Lecture Quiz"):
                            quiz_state = gr.State([])
                            with gr.Column(elem_classes=["custom-card"]):
                                gr.Markdown("### 📝 Lecture Assessment Quiz")
                                with gr.Row(equal_height=True):
                                    quiz_session_list = gr.Dropdown(
                                        label="Select Session", scale=3
                                    )
                                    refresh_quiz_sessions_btn = gr.Button(
                                        "↻ Load Sessions",
                                        variant="secondary",
                                        scale=1,
                                        min_width=140
                                    )

                                generate_quiz_btn = gr.Button(
                                    "📝 Generate Assessment Quiz", variant="primary"
                                )
                                quiz_status = gr.Textbox(
                                    label="Quiz Status",
                                    elem_classes=["status-box"]
                                )

                            with gr.Column(elem_classes=["custom-card"]):
                                q1 = gr.Radio(choices=[], label="Question 1", visible=False)
                                q2 = gr.Radio(choices=[], label="Question 2", visible=False)
                                q3 = gr.Radio(choices=[], label="Question 3", visible=False)
                                q4 = gr.Radio(choices=[], label="Question 4", visible=False)
                                q5 = gr.Radio(choices=[], label="Question 5", visible=False)
                                
                                submit_quiz_btn = gr.Button("📝 Submit Answers", variant="primary", visible=False)
                                
                            # Scorecard display
                            quiz_scorecard = gr.HTML(visible=False)

                    # ── Button bindings ──
                    refresh_sessions_btn.click(get_sessions_dropdown, [], session_list)
                    generate_notes_btn.click(generate_notes, [session_list], notes_output)
                    download_pdf_btn.click(generate_pdf, [notes_output], pdf_output)
                    generate_ai_btn.click(
                        generate_ai_content, [session_list, ai_mode, custom_prompt], ai_output
                    )
                    download_ai_pdf_btn.click(generate_ai_pdf, [ai_output], ai_pdf_output)

                    refresh_ppt_sessions_btn.click(get_sessions_dropdown, [], ppt_session_list)
                    generate_ppt_btn.click(
                        generate_lecture_ppt,
                        [ppt_session_list],
                        [ppt_status, ppt_file_output]
                    )

                    refresh_quiz_sessions_btn.click(get_sessions_dropdown, [], quiz_session_list)
                    generate_quiz_btn.click(
                        load_quiz_ui,
                        [quiz_session_list],
                        [quiz_status, quiz_state, q1, q2, q3, q4, q5, submit_quiz_btn, quiz_scorecard]
                    )
                    submit_quiz_btn.click(
                        submit_quiz_ui,
                        [q1, q2, q3, q4, q5, quiz_state],
                        [quiz_scorecard, quiz_scorecard]
                    )

                    student_login_btn.click(
                        student_login,
                        [student_usn_login, student_password_login],
                        [student_auth_status, student_login_panel, student_dashboard]
                    )
                    student_logout_btn.click(
                        student_logout, [],
                        [student_auth_status, student_login_panel, student_dashboard,
                         student_usn_login, student_password_login]
                    )
                    load_attendance_btn.click(
                        load_student_attendance, [student_usn_login], attendance_info
                    )

            # ══════════════════════════════════════════
            # TEACHER PANEL
            # ══════════════════════════════════════════
            with gr.Group(visible=False) as teacher_panel:
                status = gr.Textbox(
                    label="Status", interactive=False, elem_classes=["status-box"]
                )

                # LOGIN
                with gr.Group(visible=True) as login_panel:
                    with gr.Column(elem_classes=["custom-card"]):
                        gr.Markdown("### 👩‍🏫 Teacher Authentication")
                        with gr.Row():
                            with gr.Column():
                                username = gr.Textbox(
                                    label="Username", placeholder="Enter username"
                                )
                            with gr.Column():
                                password = gr.Textbox(
                                    label="Password", type="password",
                                    placeholder="Enter password"
                                )
                        login_btn = gr.Button("Login", variant="primary")

                # SESSION DASHBOARD
                with gr.Group(visible=False) as session_panel:
                    with gr.Row(equal_height=True):
                        gr.Markdown("## 👩‍🏫 Active Teacher Session")
                        logout_teacher_btn = gr.Button(
                            "Logout", variant="secondary", scale=0, min_width=100
                        )

                    with gr.Row():

                        # Left: Config + Camera
                        with gr.Column(scale=1, elem_classes=["custom-card"]):
                            gr.Markdown("### ⚙️ Session Configuration")
                            subject = gr.Dropdown(SUBJECTS, label="Subject")
                            duration = gr.Slider(5, 120, value=20, label="Session Duration (min)")
                            start_btn = gr.Button("▶ Start Session", variant="primary")
                            session_info = gr.Textbox(label="Session Info", lines=5)
                            gr.Markdown("### 📸 Live Camera")
                            attendance_camera = gr.Image(label="Live Attendance Camera")

                        # Right: Controls + Audio
                        with gr.Column(scale=1):
                            with gr.Column(elem_classes=["custom-card"]):
                                gr.Markdown("### 📋 Attendance Controls")
                                with gr.Row():
                                    attendance_btn = gr.Button(
                                        "▶ Take Attendance", variant="primary"
                                    )
                                    stop_attendance_btn = gr.Button(
                                        "⏹ Stop", variant="stop"
                                    )
                                attendance_status = gr.Textbox(
                                    label="Attendance Status",
                                    elem_classes=["status-box"]
                                )

                            with gr.Column(elem_classes=["custom-card"]):
                                gr.Markdown("### 🎙️ Audio Recording")
                                with gr.Row(equal_height=True):
                                    device_dropdown = gr.Dropdown(
                                        choices=DEVICE_LIST,
                                        value=DEVICE_LIST[0] if DEVICE_LIST else None,
                                        label="Input Device",
                                        scale=3
                                    )
                                    refresh_devices = gr.Button(
                                        "↻", variant="secondary",
                                        scale=0, min_width=52
                                    )
                                with gr.Row():
                                    start_record_btn = gr.Button(
                                        "▶ Start Recording", variant="primary"
                                    )
                                    stop_record_btn = gr.Button(
                                        "⏹ Stop Recording", variant="stop"
                                    )
                                audio_status = gr.Textbox(
                                    label="Audio Status",
                                    elem_classes=["status-box"]
                                )

                    # ── Button bindings ──
                    def refresh_device_list():
                        devices = audio_module.get_input_devices()
                        return gr.update(choices=devices, value=devices[0] if devices else None)

                    refresh_devices.click(refresh_device_list, [], device_dropdown)

                    def start_audio_recording(device_name, duration):
                        if not session_state["audio_path"]:
                            return "❌ Start session first"
                        if not device_name:
                            return "❌ Select device first"
                        device_index = int(device_name.split(" - ")[0])
                        return audio_module.start_recording(
                            session_state["audio_path"], device_index, int(duration)
                        )

                    start_record_btn.click(
                        start_audio_recording, [device_dropdown, duration], audio_status
                    )
                    stop_record_btn.click(audio_module.stop_recording, [], audio_status)

                    login_btn.click(
                        login, [username, password], [status, login_panel, session_panel]
                    )
                    logout_teacher_btn.click(
                        teacher_logout, [],
                        [status, login_panel, session_panel, username, password]
                    )
                    start_btn.click(
                        start_session, [subject, duration], [status, session_info]
                    )
                    attendance_btn.click(
                        run_attendance_ui, [duration], [attendance_camera, attendance_status]
                    )
                    stop_attendance_btn.click(
                        stop_attendance_ui, [], [attendance_camera, attendance_status]
                    )

            # ══════════════════════════════════════════
            # REGISTER PANEL
            # ══════════════════════════════════════════
            with gr.Group(visible=False) as register_panel:
                register_auth_status = gr.Textbox(
                    label="Status", interactive=False, elem_classes=["status-box"]
                )

                # ADMIN LOGIN
                with gr.Group(visible=True) as register_login_panel:
                    with gr.Column(elem_classes=["custom-card"]):
                        gr.Markdown("### 🔐 Admin Authentication")
                        with gr.Row():
                            with gr.Column():
                                admin_username = gr.Textbox(
                                    label="Admin Username",
                                    placeholder="Enter admin username"
                                )
                            with gr.Column():
                                admin_password = gr.Textbox(
                                    label="Password", type="password",
                                    placeholder="Enter password"
                                )
                        admin_login_btn = gr.Button("Login", variant="primary")

                # REGISTER DASHBOARD
                with gr.Group(visible=False) as register_dashboard:
                    with gr.Row(equal_height=True):
                        gr.Markdown("## 🧾 Register Student Face")
                        logout_btn = gr.Button(
                            "Logout", variant="secondary", scale=0, min_width=100
                        )

                    with gr.Row():

                        # Form
                        with gr.Column(scale=1, elem_classes=["custom-card"]):
                            gr.Markdown("### 📝 Student Information")
                            usn = gr.Textbox(label="USN", placeholder="e.g. 1AB23CS001", elem_classes=["uppercase-input"])
                            name = gr.Textbox(label="Name", placeholder="Full Name", elem_classes=["uppercase-input"])
                            course = gr.Textbox(label="Course", placeholder="Department / Course", elem_classes=["uppercase-input"])
                            student_password = gr.Textbox(
                                label="Password", type="password",
                                placeholder="Create a secure password"
                            )

                            start_capture_btn = gr.Button("📸 Capture & Register", variant="primary")
                            register_status = gr.Textbox(
                                label="Status", elem_classes=["status-box"]
                            )

                        # Camera
                        with gr.Column(scale=1, elem_classes=["custom-card"]):
                            gr.Markdown("### 📸 Live Camera Capture")
                            camera_output = gr.Image(label="Camera Preview")
                            with gr.Row():
                                start_preview_btn = gr.Button("🎥 Start Preview", variant="secondary")
                                stop_preview_btn = gr.Button("⏹ Stop Preview", variant="stop")

                    start_capture_btn.click(
                        validate_and_capture,
                        [usn, name, course, student_password],
                        [camera_output, register_status, usn, name, course, student_password]
                    )

                    start_preview_btn.click(
                        start_preview_ui,
                        [],
                        [camera_output, register_status]
                    )

                    stop_preview_btn.click(
                        stop_preview_ui,
                        [],
                        [camera_output, register_status]
                    )

                    # Manage Students
                    with gr.Accordion("🗑️ Manage Students", open=False):
                        with gr.Column(elem_classes=["custom-card"]):
                            gr.Markdown("### 🗑️ Delete Student")
                            with gr.Row(equal_height=True):
                                delete_usn = gr.Textbox(
                                    label="USN to Delete",
                                    placeholder="Enter USN",
                                    scale=3,
                                    elem_classes=["uppercase-input"]
                                )
                                delete_btn = gr.Button(
                                    "Delete", variant="stop", scale=0, min_width=100
                                )
                            delete_status = gr.Textbox(
                                label="Delete Status", elem_classes=["status-box"]
                            )

                            def delete_student_ui(usn):
                                result = attend.delete_student(usn)
                                attend.encode_faces()
                                return result + "\n✅ Model retrained", ""

                            delete_btn.click(
                                delete_student_ui, [delete_usn], [delete_status, delete_usn]
                            )

                # LOGIN / LOGOUT bindings
                admin_login_btn.click(
                    admin_login,
                    [admin_username, admin_password],
                    [register_auth_status, register_login_panel, register_dashboard]
                )
                logout_btn.click(
                    admin_logout, [],
                    [register_auth_status, register_login_panel, register_dashboard,
                     admin_username, admin_password]
                )

            # ══════════════════════════════════════════
            # EXECUTIVE INSIGHTS PANEL
            # ══════════════════════════════════════════
            with gr.Group(visible=False) as insights_panel:
                with gr.Group(elem_classes=["dashboard-header"]):
                    gr.HTML("""
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:15px;">
                        <div>
                            <h1 style="margin:0; font-size:32px; font-weight:800;">📈 Executive Insights & Analytics</h1>
                            <p style="margin-top:6px; font-size:15px; opacity:0.8;">Interactive visualizations of classroom attendance and student metrics.</p>
                        </div>
                    </div>
                    """)

                with gr.Column(elem_classes=["custom-card"]):
                    with gr.Row(equal_height=True):
                        gr.Markdown("### 📊 Live Analytics Control")
                        refresh_analytics_btn = gr.Button("🔄 Refresh Analytics", variant="primary", scale=0, min_width=180)
                    
                    # Embed our Chart.js dashboard HTML component
                    insights_html = gr.HTML(label="Visual Dashboard")

                refresh_analytics_btn.click(
                    analytics.generate_insights_html,
                    [],
                    insights_html
                )

            # ══════════════════════════════════════════
            # SIDEBAR BINDINGS
            # ══════════════════════════════════════════
            _nav_outputs = [
                student_panel, teacher_panel, register_panel, insights_panel,
                home_banner,
                student_dashboard, session_panel, register_dashboard,
                student_usn_login, student_password_login,
                username, password,
                admin_username, admin_password
            ]

            student_btn.click(reset_all_panels,  [gr.State("student")],  _nav_outputs)
            teacher_btn.click(reset_all_panels,  [gr.State("teacher")],  _nav_outputs)
            register_btn.click(reset_all_panels, [gr.State("register")], _nav_outputs)
            insights_btn.click(reset_all_panels, [gr.State("insights")], _nav_outputs)
            
            # Auto-update insights when clicking the insights tab
            insights_btn.click(
                analytics.generate_insights_html,
                [],
                insights_html
            )


# ── RUN ──
if __name__ == "__main__":
    app.launch(share=True, debug=True,  favicon_path="emoji.png")