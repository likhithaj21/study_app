@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()

    topic = data.get("topic")
    output = data.get("output_type", "quiz")
    difficulty = data.get("difficulty", "medium")
    n = data.get("number_of_questions", 5)

    if not topic:
        return jsonify({"error": "topic required"}), 400

    chunks = search_relevant_chunks(topic, output)

    if not chunks:
        return jsonify({"error": "no notes"}), 404

    notes = "\n\n".join(chunks)

    prompt = f"""
You are an expert teacher.

STRICT RULES:
- Use ONLY the provided notes
- Do NOT add outside knowledge
- Output ONLY valid JSON (no explanation, no text before/after)
- Always follow the exact format

NOTES:
{notes}

TASK:
Generate {n} {output} questions
Difficulty: {difficulty}

FORMAT:
[
  {{
    "question": "string",
    "options": ["A","B","C","D"],
    "answer": "A"
  }}
]
"""

    try:
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3   # 🔥 lower = more structured output
        )

        raw = res.choices[0].message.content.strip()

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # 🔥 CLEAN JSON EXTRACTION
    try:
        # Remove markdown/code blocks if model adds them
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        # Extract only JSON part
        start = raw.find("[")
        end = raw.rfind("]") + 1
        raw_json = raw[start:end]

        parsed = json.loads(raw_json)

    except Exception:
        return jsonify({
            "error": "Failed to parse model output",
            "raw": raw
        }), 500

    return jsonify(parsed)