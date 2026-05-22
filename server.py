import os
import shutil
import tempfile
import whisper
import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI(
    title="Smart Classroom Transcription & AI Server",
    description="Offloaded Whisper transcription and Groq AI service for SBC client"
)

# Enable CORS so the client SBC can communicate with it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load the model globally so it stays in RAM and does not reload on every request
print("[INFO] Loading Whisper 'medium' model... This may take a minute.")
try:
    model = whisper.load_model("medium")
    print("[SUCCESS] Whisper model loaded and ready!")
    model_loaded = True
except Exception as e:
    print(f"[ERROR] Failed to load Whisper model: {e}")
    model_loaded = False

# Initialize Groq client
try:
    groq_client = OpenAI(
        api_key="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",
        base_url="https://api.groq.com/openai/v1"
    )
    print("[SUCCESS] Groq AI client initialized!")
except Exception as e:
    print(f"[ERROR] Failed to initialize Groq client: {e}")


class NotesRequest(BaseModel):
    text: str
    mode: str
    custom_prompt: str = ""


@app.get("/health")
def health_check():
    return {
        "status": "healthy" if model_loaded else "unhealthy",
        "model": "medium" if model_loaded else None
    }


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Whisper model is not loaded or healthy on the server.")

    # Check file extension
    filename = file.filename
    _, ext = os.path.splitext(filename)
    if ext.lower() not in [".wav", ".mp3", ".m4a", ".ogg", ".flac"]:
        raise HTTPException(status_code=400, detail=f"Unsupported file format: {ext}")

    # Create a temporary file to save the uploaded audio data
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_audio:
        temp_path = temp_audio.name
        try:
            # Stream upload file contents into the temporary file
            shutil.copyfileobj(file.file, temp_audio)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {e}")

    try:
        print(f"[INFO] Transcribing file: {filename} ({os.path.getsize(temp_path)} bytes)...")
        # Run Whisper transcription with same prompts and settings as transcribe_module.py
        result = model.transcribe(
            temp_path,
            language="en",
            beam_size=5,
            initial_prompt="""
            Engineering classroom lecture.
            Technical educational discussion.
            """
        )
        transcription_text = result.get("text", "").strip()
        print(f"[SUCCESS] Transcription complete for: {filename}")
        return {"transcription": transcription_text}

    except Exception as e:
        print(f"[ERROR] Error during transcription: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    finally:
        # Always clean up the temporary file
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.post("/generate_notes")
async def generate_notes(request: NotesRequest):
    prompts = {
        "Summary": "Summarize these lecture notes clearly.",
        "Detailed Notes": "Generate well-structured lecture notes from this transcription.",
        "Important Questions": "Generate important exam questions from this lecture.",
        "Key Points": "Extract key points from this lecture.",
        "Explain Simply": "Explain this lecture in simple student-friendly language."
    }

    system_prompt = prompts.get(request.mode, "")
    if request.custom_prompt:
        system_prompt += "\nAdditional Instruction: " + request.custom_prompt

    try:
        print(f"[INFO] Generating AI notes using Groq (mode: {request.mode})...")
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": request.text
                }
            ],
            temperature=0.3
        )
        ai_content = response.choices[0].message.content
        print("[SUCCESS] AI notes generated successfully!")
        return {"notes": ai_content}
    except Exception as e:
        print(f"[ERROR] Failed to generate AI notes: {e}")
        raise HTTPException(status_code=500, detail=f"AI generation failed: {str(e)}")


if __name__ == "__main__":
    # Run on all network interfaces (0.0.0.0) at port 8000 so the SBC can reach it
    uvicorn.run(app, host="0.0.0.0", port=8000)
