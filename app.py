import os
import json
import sqlite3
import fitz  # PyMuPDF
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from groq import Groq   # 🔥 NEW

app = Flask(__name__, static_folder=".")
CORS(app)

app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

DB_PATH = "notes.db"

# 🔥 GROQ CLIENT
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ─── Database ────────────────────────────────────────────────────────────────

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

# ─── Text Extraction ─────────────────────────────────────────────────────────

def extract_text(file_storage):
    filename = file_storage.filename.lower()

    if filename.endswith(".pdf"):
        data = file_storage.read()
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n".join(page.get_text() for page in doc).strip()

    elif filename.endswith(".txt"):
        return file_storage.read().decode("utf-8", errors="ignore").strip()

    else:
        raise ValueError("Only PDF and TXT files are supported.")

# ─── Chunking ────────────────────────────────────────────────────────────────

def split_text(text, chunk_size=1500):
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

# ─── Smart Retrieval (RAG) ───────────────────────────────────────────────────

def search_relevant_chunks(topic, query, limit=5):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT content FROM notes WHERE topic=?",
            (topic,)
        ).fetchall()

    scored = []

    for r in rows:
        content = r["content"]
        score = content.lower().count(query.lower())
        scored.append((score, content))

    scored.sort(reverse=True, key=lambda x: x[0])

    return [c for _, c in scored[:limit]]

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# 🔥 MULTI-FILE UPLOAD + CHUNKING
@app.route("/upload", methods=["POST"])
def upload_notes():
    topic = request.form.get("topic", "").strip()
    files = request.files.getlist("file")

    if not topic:
        return "Topic name is required.", 400
    if not files or files[0].filename == "":
        return "At least one file is required.", 400

    try:
        with get_db() as conn:
            for file in files:
                content = extract_text(file)

                if len(content) < 20:
                    continue

                chunks = split_text(content)

                for chunk in chunks:
                    conn.execute(
                        "INSERT INTO notes (topic, content) VALUES (?, ?)",
                        (topic, chunk)
                    )

            conn.commit()

    except Exception as e:
        return f"Upload error: {e}", 500

    return f"Uploaded {len(files)} file(s) successfully for topic '{topic}'"

# GET TOPICS
@app.route("/topics", methods=["GET"])
def get_topics():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT topic FROM notes ORDER BY topic ASC"
        ).fetchall()

    return jsonify([{"topic": r["topic"]} for r in rows])

# GET NOTES
@app.route("/get_notes", methods=["GET"])
def get_notes():
    topic = request.args.get("topic", "").strip()

    if not topic:
        return jsonify({"error": "topic required"}), 400

    with get_db() as conn:
        rows = conn.execute(
            "SELECT content FROM notes WHERE topic=?",
            (topic,)
        ).fetchall()

    if not rows:
        return jsonify({"error": "No notes found"}), 404

    combined = "\n\n".join([r["content"] for r in rows])

    return jsonify({
        "topic": topic,
        "content": combined,
        "chars": len(combined)
    })

# 🔥 GROQ GENERATE
@app.route("/generate", methods=["POST"])
def generate():
    body = request.get_json(force=True)

    topic          = (body.get("topic") or "").strip()
    output_type    = (body.get("output_type") or "quiz").strip()
    difficulty     = (body.get("difficulty") or "medium").strip()
    num_questions  = int(body.get("number_of_questions") or 5)

    if not topic:
        return jsonify({"error": "Topic required"}), 400

    query = output_type + " " + topic
    relevant_chunks = search_relevant_chunks(topic, query)

    if not relevant_chunks:
        return jsonify({"error": "No relevant notes found"}), 404

    notes = "\n\n".join(relevant_chunks)

    prompt = f"""
Use ONLY the notes below.

NOTES:
{notes}

Generate {output_type} with {num_questions} questions.
Difficulty: {difficulty}

Return ONLY JSON.
"""

    try:
        response = client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        raw = response.choices[0].message.content

    except Exception as e:
        return jsonify({"error": f"GROQ error: {e}"}), 500

    try:
        parsed = json.loads(raw)
    except:
        parsed = {"raw_text": raw}

    return jsonify({
        "success": True,
        "topic": topic,
        "result": parsed,
        "raw": raw
    })

# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )