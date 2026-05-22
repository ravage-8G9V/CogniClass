import sounddevice as sd
from scipy.io.wavfile import write
import threading
import os

# GLOBAL CONTROL FLAG
is_recording = False

def get_input_devices():
    devices = sd.query_devices()
    input_devices = []

    for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0:
            input_devices.append(f"{i} - {dev['name']}")

    return input_devices if input_devices else ["No Input Devices Found"]

def list_input_devices():
    devices = sd.query_devices()
    input_devices = []
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0:
            input_devices.append(f"{i} - {d['name']}")
    return input_devices


def record_audio(file_path, device_index, duration):
    global is_recording
    is_recording = True

    fs = 16000
    frames = []

    def callback(indata, frames_count, time, status):
        if not is_recording:
            raise sd.CallbackStop()
        frames.append(indata.copy())

    with sd.InputStream(samplerate=fs, channels=1,
                        callback=callback, device=device_index):
        sd.sleep(int(duration * 1000))

    import numpy as np
    audio = np.concatenate(frames, axis=0)

    write(file_path, fs, audio)


def start_recording(file_path, device_index, duration):
    thread = threading.Thread(
        target=record_audio,
        args=(file_path, device_index, duration)
    )
    thread.start()
    return "🎙️ Recording started"


def stop_recording():
    global is_recording
    is_recording = False
    return "🛑 Recording stopped"