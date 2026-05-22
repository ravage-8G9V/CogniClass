from openai import OpenAI

client = OpenAI(
    api_key="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",
    base_url="https://api.groq.com/openai/v1"
)

def generate_ai_notes(text, mode, custom_prompt):

    prompts = {
        "Summary":
            "Summarize these lecture notes clearly.",

        "Detailed Notes":
            "Generate well-structured lecture notes from this transcription.",

        "Important Questions":
            "Generate important exam questions from this lecture.",

        "Key Points":
            "Extract key points from this lecture.",

        "Explain Simply":
            "Explain this lecture in simple student-friendly language."
    }

    system_prompt = prompts.get(mode, "")

    if custom_prompt:
        system_prompt += "\nAdditional Instruction: " + custom_prompt

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",

        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": text
            }
        ],

        temperature=0.3
    )

    return response.choices[0].message.content