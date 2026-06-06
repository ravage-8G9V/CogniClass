import cv2
import os
import pickle
import csv
from datetime import datetime
import face_recognition
import json

# ─────────────────────────────────────────
# PATH SETUP (LOCAL MACHINE)
# ─────────────────────────────────────────
print("ATTENDANCE FILE LOADED FROM:", __file__)
BASE_DIR = os.getcwd()

DATASET_PATH = os.path.join(BASE_DIR, "dataset")
MODEL_DIR = os.path.join(BASE_DIR, "models")
MODEL_PATH = os.path.join(MODEL_DIR, "encodings.pkl")
REGISTRY_PATH = os.path.join(DATASET_PATH, "registry.json")

def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return {}

    try:
        with open(REGISTRY_PATH, "r") as f:
            data = f.read().strip()

            if not data:   # 🔴 empty file case
                return {}

            return json.loads(data)

    except Exception:
        print("⚠️ Registry corrupted. Resetting...")
        return {}

def save_registry(data):
    with open(REGISTRY_PATH, "w") as f:
        json.dump(data, f, indent=2)

os.makedirs(DATASET_PATH, exist_ok=True)
os.makedirs(MODEL_DIR, exist_ok=True)

print("BASE_DIR:", BASE_DIR)
attendance_running = True


# ─────────────────────────────────────────
# 1) REGISTER STUDENT
# ─────────────────────────────────────────

def register_student(usn, name, course, num_images=20):
    folder_name = f"{name}_{usn}"
    student_path = os.path.join(DATASET_PATH, folder_name)
    os.makedirs(student_path, exist_ok=True)

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("❌ Camera not working")
        return

    print(f"[INFO] Capturing images for {name}")

    count = 0

    while count < num_images:
        ret, frame = cap.read()
        if not ret:
            print("❌ Frame not captured")
            break

        cv2.imshow("Register Face", frame)

        img_path = os.path.join(student_path, f"{count}.jpg")
        cv2.imwrite(img_path, frame)
        count += 1

        if cv2.waitKey(200) & 0xFF == 27:
            break

    cap.release()
    cv2.destroyAllWindows()

    print(f"[INFO] {count} images saved at {student_path}")

preview_running = False

def start_face_preview():
    global preview_running
    import time
    preview_running = True

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ Camera not working for preview")
        yield None
        return

    try:
        while preview_running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            # Convert BGR to RGB for Gradio
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            yield rgb_frame
            time.sleep(0.03)  # around 30 FPS
    finally:
        cap.release()

def stop_face_preview():
    global preview_running
    preview_running = False

def capture_face_stream(usn, name, course, password, num_images=20, cap=None):
    global preview_running
    import time

    # Stop preview first and wait for camera to be freed
    preview_running = False
    time.sleep(0.4)

    # 🔴 HARD VALIDATION FIRST (before anything)
    if not usn or not name or not course:
        yield None, "❌ All fields are required"
        return

    registry = load_registry()

    if usn in registry:
        yield None, f"❌ Duplicate USN detected: {usn}. Registration blocked."
        return

    # ✅ Only after validation → proceed
    folder_name = f"{name}_{usn}"
    student_path = os.path.join(DATASET_PATH, folder_name)

    # extra safety (edge case)
    if os.path.exists(student_path):
        yield None, "❌ Folder already exists. Possible duplicate."
        return

    os.makedirs(student_path, exist_ok=False)

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        yield None, "❌ Camera not working"
        return

    count = 0
    last_frame = None

    try:
        while count < num_images:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            last_frame = frame
            img_path = os.path.join(student_path, f"{count}.jpg")
            cv2.imwrite(img_path, frame)
            count += 1

            # Convert BGR to RGB for live stream in Gradio UI
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            yield rgb_frame, f"📸 Capturing face sample {count}/{num_images}..."
            time.sleep(0.08)  # slight delay for facial variation
    finally:
        cap.release()

    # ✅ Save only after successful capture
    registry[usn] = {
        "name": name,
        "course": course,
        "password": password,
        "folder": folder_name
    }
    save_registry(registry)

    # Convert last frame to RGB for final output
    final_rgb = cv2.cvtColor(last_frame, cv2.COLOR_BGR2RGB) if last_frame is not None else None
    yield final_rgb, f"✅ Registered {name} ({usn})"

# ─────────────────────────────────────────
# 2) ENCODE FACES
# ─────────────────────────────────────────

def encode_faces():
    known_encodings = []
    known_names = []

    print("[INFO] Encoding started...")

    for person in os.listdir(DATASET_PATH):

        person_path = os.path.join(DATASET_PATH, person)

        # 🔴 SKIP non-directories (VERY IMPORTANT)
        if not os.path.isdir(person_path):
            continue

        for img_name in os.listdir(person_path):
            img_path = os.path.join(person_path, img_name)

            image = face_recognition.load_image_file(img_path)
            encodings = face_recognition.face_encodings(image)

            if len(encodings) > 0:
                known_encodings.append(encodings[0])
                known_names.append(person)
            else:
                print(f"[WARNING] No face in {img_name}")

    if len(known_encodings) == 0:
        print("❌ No valid faces found. Encoding aborted.")
        return

    data = {"encodings": known_encodings, "names": known_names}

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(data, f)

    print(f"[INFO] Encoding saved at {MODEL_PATH}")

# ─────────────────────────────────────────
# 3) RUN ATTENDANCE
# ─────────────────────────────────────────

def run_attendance(session_folder):
    global attendance_running

    attendance_running = True

    import time
    import numpy as np

    registry = load_registry()

    total_students = len(registry)

    if total_students == 0:
        yield None, "❌ No registered students found"
        return

    if not os.path.exists(MODEL_PATH):
        yield None, "❌ Train model first"
        return

    with open(MODEL_PATH, "rb") as f:
        data = pickle.load(f)

    os.makedirs(session_folder, exist_ok=True)

    attendance_file = os.path.join(
        session_folder,
        "attendance.csv"
    )

    present_students = {}

    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        yield None, "❌ Camera not working"
        return

    try:
        while True:

            if not attendance_running:
                break

            ret, frame = cap.read()

            if not ret:
                continue

            small = cv2.resize(
                frame,
                (0, 0),
                fx=0.25,
                fy=0.25
            )

            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

            locations = face_recognition.face_locations(rgb)

            encodings = face_recognition.face_encodings(
                rgb,
                locations
            )

            for encoding, location in zip(encodings, locations):

                distances = face_recognition.face_distance(
                    data["encodings"],
                    encoding
                )

                if len(distances) > 0:

                    idx = np.argmin(distances)

                    if distances[idx] < 0.5:

                        folder_name = data["names"][idx]

                        # SPLIT NAME_USN
                        parts = folder_name.rsplit("_", 1)

                        if len(parts) != 2:
                            continue

                        student_name = parts[0]
                        usn = parts[1]

                        # MARK PRESENT
                        if usn not in present_students:

                            present_students[usn] = {
                                "name": student_name,
                                "time": datetime.now().strftime(
                                    "%H:%M:%S"
                                ),
                                "status": "Present"
                            }

                        top, right, bottom, left = location

                        top *= 4
                        right *= 4
                        bottom *= 4
                        left *= 4

                        # FACE BOX
                        cv2.rectangle(
                            frame,
                            (left, top),
                            (right, bottom),
                            (0, 255, 0),
                            2
                        )

                        # LABEL
                        cv2.putText(
                            frame,
                            f"{student_name} ({usn})",
                            (left, top - 10),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (0, 255, 0),
                            2
                        )

            # LIVE COUNT
            present_count = len(present_students)

            counter_text = (
                f"{present_count}/{total_students} Present"
            )

            cv2.putText(
                frame,
                counter_text,
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 0, 255),
                3
            )
            yield frame, counter_text

    finally:
        cap.release()

        # SAVE FINAL CSV
        print(f"[ATTENDANCE] Saving final CSV to: {attendance_file}")
        with open(attendance_file, "w", newline="") as f:

            writer = csv.writer(f)

            writer.writerow([
                "USN",
                "Name",
                "Time",
                "Status"
            ])

            for usn, details in registry.items():

                if usn in present_students:

                    writer.writerow([
                        usn,
                        present_students[usn]["name"],
                        present_students[usn]["time"],
                        "Present"
                    ])

                else:

                    writer.writerow([
                        usn,
                        details["name"],
                        "-",
                        "Absent"
                    ])
        print("[ATTENDANCE] Final CSV saved successfully.")

    yield None, (
        f"✅ Attendance completed\n"
        f"Present: {present_count}/{total_students}"
    )

def stop_attendance():

    global attendance_running

    attendance_running = False

def delete_student(usn):
    import shutil

    registry = load_registry()

    if not usn:
        return "❌ Enter USN"

    usn = usn.upper()

    if usn not in registry:
        return f"❌ USN not found: {usn}"

    student_info = registry[usn]
    folder_name = student_info["folder"]
    student_path = os.path.join(DATASET_PATH, folder_name)

    # Delete folder
    if os.path.exists(student_path):
        shutil.rmtree(student_path)

    # Remove from registry
    del registry[usn]
    save_registry(registry)

    return f"✅ {usn} deleted"
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("attend.py loaded for manual testing only")