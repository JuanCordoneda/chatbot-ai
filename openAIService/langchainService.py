from flask import Flask, request
import os
import anthropic

app = Flask(__name__)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

@app.route("/getresponsegpt", methods=["GET"])
def get_response_gpt():
    user_prompt = request.args.get("user_prompt", "")

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system="Eres un asistente virtual útil que responde en el mismo idioma que el usuario.",
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = next(
        (block.text for block in response.content if block.type == "text"), ""
    )
    return text

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
