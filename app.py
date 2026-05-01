import os
import json
import sqlite3
import fitz
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from groq import Groq

app = Flask(__name__, static_folder=".")
CORS(app)

app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

DB_PATH = "notes_v2.db"

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ─── DATABASE ─────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT,
                content TEXT
            )
        """)
        conn.commit()

# ─── TEXT EXTRACTION ──────────────────────────────

def extract_text(file):
    name = file.filename.lower()

    if name.endswith(".pdf"):
        data = file.read()
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)

    elif name.endswith(".txt"):
        return file.read().decode("utf-8", errors="ignore")

    else:
        raise ValueError("Only PDF and TXT supported")

# ─── CHUNKING ─────────────────────────────────────

def split_text(text, size=1500):
    return [text[i:i+size] for i in range(0, len(text), size)]

# ─── SMART SEARCH ─────────────────────────────────

def search_relevant_chunks(topic, query, limit=5):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT content FROM notes WHERE topic=?",
            (topic,)
        ).fetchall()

    scored = []

    for r in rows:
        content = r["content"]
        score = (
            content.lower().count(query.lower()) +
            content.lower().count(topic.lower())
        )
        scored.append((score, content))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [c for _, c in scored[:limit]]

# ─── ROUTES ───────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# UPLOAD (OVERWRITE SAME TOPIC)
@app.route("/upload", methods=["POST"])
def upload_notes():
    topic = request.form.get("topic", "").strip()
    files = request.files.getlist("file")

    if not topic:
        return "Topic required", 400
    if not files:
        return "No files", 400

    try:
        with get_db() as conn:
            conn.execute("DELETE FROM notes WHERE topic=?", (topic,))

            for file in files:
                text = extract_text(file)

                if len(text) < 20:
                    continue

                chunks = split_text(text)

                for chunk in chunks:
                    conn.execute(
                        "INSERT INTO notes (topic, content) VALUES (?, ?)",
                        (topic, chunk)
                    )

            conn.commit()

    except Exception as e:
        return f"Upload error: {e}", 500

    return f"Uploaded successfully for topic '{topic}'"

# GET TOPICS
@app.route("/topics")
def topics():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT topic FROM notes ORDER BY topic"
        ).fetchall()

    return jsonify([r["topic"] for r in rows])

# GENERATE
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

    if output == "quiz":
        format_block = """
[
  {
    "question": "string",
    "options": ["option text A", "option text B", "option text C", "option text D"],
    "answer": "A"
  }
]
"""

    elif output == "flashcards":
        format_block = """
[
  {
    "question": "string",
    "answer": "string"
  }
]
"""

    elif output == "question paper":
        format_block = f"""
[
  {{
    "question": "string",
    "options": ["option text A", "option text B", "option text C", "option text D"],
    "answer": "A"
  }}
]
"""

    else:
        return jsonify({"error": "Invalid output type"}), 400

    prompt = f"""IMPORTANT: Output ONLY a valid JSON array. No explanation. No markdown. No extra text before or after. Start directly with [ and end with ].

You are an expert teacher creating study material from the notes below.

STRICT RULES:
- Use ONLY the provided notes
- Do NOT use outside knowledge
- Output ONLY a raw JSON array — no markdown, no code fences, no explanation
- The answer field must be ONLY the letter: A, B, C, or D
- Each question must have exactly 4 options as plain text (no letter prefix in options)
- Generate exactly {n} items
- Difficulty: {difficulty}
- Type: {output}

NOTES:
{notes}

REQUIRED JSON FORMAT:
{format_block}

Remember: Start your response with [ and end with ]. Nothing else."""

    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        raw = res.choices[0].message.content.strip()

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # ─── ROBUST JSON EXTRACTION ───────────────────
    try:
        # Remove markdown code fences if present
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("[") or part.startswith("{"):
                    raw = part
                    break

        raw = raw.strip()

        # Find outermost JSON array or object
        if "[" in raw:
            start = raw.index("[")
            end   = raw.rindex("]") + 1
        elif "{" in raw:
            start = raw.index("{")
            end   = raw.rindex("}") + 1
        else:
            raise ValueError("No JSON found in response")

        clean_json = raw[start:end]
        parsed = json.loads(clean_json)

        if not isinstance(parsed, list):
            parsed = [parsed]

    except Exception as e:
        return jsonify({
            "error": "Failed to parse AI output",
            "detail": str(e),
            "raw": raw
        }), 500

    return jsonify(parsed)

# ─── RUN ──────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))