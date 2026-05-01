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

# 🔥 NEW DB NAME (forces reset on Render)
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

# 🔥 UPLOAD (OVERWRITE SAME TOPIC)
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

            # 🔥 DELETE OLD TOPIC (FIX)
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

# 🔥 GENERATE
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
- Be clear and exam-oriented

NOTES:
{notes}

TASK:
Generate {n} {output} questions
Difficulty: {difficulty}

Return JSON format:
[
  {{
    "question": "...",
    "options": ["A","B","C","D"],
    "answer": "A"
  }}
]
"""

    try:
        res = client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}]
        )

        raw = res.choices[0].message.content

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        parsed = json.loads(raw)
    except:
        parsed = {"raw": raw}

    return jsonify(parsed)

# ─── RUN ──────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))