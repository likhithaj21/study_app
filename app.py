import os
import json
import sqlite3
import requests
import fitz  # PyMuPDF
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder=".")
CORS(app)

DB_PATH = "notes.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3"

# ─── Database ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                topic   TEXT    NOT NULL UNIQUE,
                content TEXT    NOT NULL
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

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/upload_notes", methods=["POST"])
def upload_notes():
    topic = request.form.get("topic", "").strip()
    file  = request.files.get("file")

    if not topic:
        return jsonify({"error": "Topic name is required."}), 400
    if not file or file.filename == "":
        return jsonify({"error": "A file (PDF or TXT) is required."}), 400

    try:
        content = extract_text(file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to extract text: {e}"}), 500

    if len(content) < 20:
        return jsonify({"error": "Extracted text is too short. Check the file content."}), 400

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO notes (topic, content) VALUES (?, ?) "
                "ON CONFLICT(topic) DO UPDATE SET content=excluded.content",
                (topic, content)
            )
            conn.commit()
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500

    return jsonify({"success": True, "message": f"Notes for '{topic}' saved successfully!", "chars": len(content)})

@app.route("/topics", methods=["GET"])
def get_topics():
    with get_db() as conn:
        rows = conn.execute("SELECT id, topic FROM notes ORDER BY topic ASC").fetchall()
    return jsonify([{"id": r["id"], "topic": r["topic"]} for r in rows])

@app.route("/get_notes", methods=["GET"])
def get_notes():
    topic = request.args.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "topic parameter is required."}), 400
    with get_db() as conn:
        row = conn.execute("SELECT content FROM notes WHERE topic=?", (topic,)).fetchone()
    if not row:
        return jsonify({"error": f"No notes found for topic: {topic}"}), 404
    return jsonify({"topic": topic, "content": row["content"], "chars": len(row["content"])})

@app.route("/generate", methods=["POST"])
def generate():
    body = request.get_json(force=True)
    topic             = (body.get("topic") or "").strip()
    output_type       = (body.get("output_type") or "quiz").strip()
    difficulty        = (body.get("difficulty") or "medium").strip()
    num_questions     = int(body.get("number_of_questions") or 5)
    question_types    = body.get("question_types") or []

    if not topic:
        return jsonify({"error": "Topic is required."}), 400

    with get_db() as conn:
        row = conn.execute("SELECT content FROM notes WHERE topic=?", (topic,)).fetchone()
    if not row:
        return jsonify({"error": f"No notes found for topic: '{topic}'. Please upload first."}), 404

    notes = row["content"]

    prompt = build_prompt(notes, output_type, difficulty, num_questions, question_types)

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model":  OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False
        }, timeout=120)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to Ollama. Make sure 'ollama serve' is running."}), 503
    except Exception as e:
        return jsonify({"error": f"Ollama error: {e}"}), 500

    parsed = safe_parse_json(raw)
    return jsonify({"success": True, "topic": topic, "output_type": output_type, "result": parsed, "raw": raw})

# ─── Prompt Builder ───────────────────────────────────────────────────────────

def build_prompt(notes, output_type, difficulty, num_questions, question_types):
    type_list = ", ".join(question_types) if question_types else "any"
    
    output_instructions = {
        "quiz": f"""Generate a quiz with {num_questions} questions.
Each question must have: question, 4 options (A/B/C/D), correct_answer, explanation.
Return a JSON object: {{ "quiz": [ {{ "id":1, "question":"...", "options":{{"A":"...","B":"...","C":"...","D":"..."}}, "correct_answer":"A", "explanation":"..." }} ] }}""",

        "flashcards": f"""Generate {num_questions} flashcards.
Each flashcard: front (question/concept), back (answer/explanation).
Return JSON: {{ "flashcards": [ {{ "id":1, "front":"...", "back":"..." }} ] }}""",

        "question_paper": f"""Generate a question paper with {num_questions} questions.
Question types to include: {type_list}. Difficulty: {difficulty}.
Each question: id, type, question, options (4 choices), correct_answer, explanation.
Return JSON: {{ "question_paper": [ {{ "id":1, "type":"MCQ", "question":"...", "options":{{"A":"...","B":"...","C":"...","D":"..."}}, "correct_answer":"A", "explanation":"..." }} ], "instructions": "...", "duration": "..." }}""",

        "recall": f"""Generate {num_questions} recall/revision prompts.
Each item: a short recall cue on the front, a full explanation on the back.
Return JSON: {{ "recall": [ {{ "id":1, "cue":"...", "recall_answer":"..." }} ] }}"""
    }.get(output_type, "Generate a quiz with 5 questions and return JSON.")

    return f"""You are a strict exam content generator.

CRITICAL RULE: Use ONLY the notes provided below. Do NOT use any external knowledge.
Every question, answer, and explanation must be directly traceable to the notes.

INPUT NOTES:
\"\"\"
{notes}
\"\"\"

TASK:
Difficulty level: {difficulty}
{output_instructions}

IMPORTANT:
- Return ONLY valid JSON. No markdown. No backticks. No preamble.
- Every fact must come from the notes above."""

def safe_parse_json(raw: str):
    text = raw.strip()
    # strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except Exception:
        return {"raw_text": raw}

# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("✅ Database initialized: notes.db")
    print("🚀 Starting server at http://localhost:5000")
    app.run(debug=True, port=5000)

import os

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
