import whisper
import os

model = whisper.load_model("medium")  # lightweight + fast

def transcribe_audio(file_path):
    if not os.path.exists(file_path):
        return "❌ Audio file not found"

    model = whisper.load_model("medium")

    result = model.transcribe(
        file_path,
        language="en",
        beam_size=5,
        initial_prompt="""
        Engineering classroom lecture.
        Technical educational discussion.
        """
    )
    return result["text"]