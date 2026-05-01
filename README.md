# StudyAI — Upload Once, Learn Forever

A local AI study tool. Upload your notes once, generate quizzes, flashcards,
question papers, and recall prompts forever — all powered by Ollama (offline).

---

## Files

```
study_app/
├── app.py       ← Flask backend
├── index.html   ← Frontend (served by Flask)
├── notes.db     ← SQLite database (auto-created on first run)
└── README.md
```

---

## Prerequisites

### 1. Python packages
```bash
pip install flask flask-cors PyMuPDF
```

### 2. Ollama (local AI)
Install from https://ollama.com then:
```bash
ollama pull llama3
ollama serve          # keep this running in a terminal
```

---

## Run the App

```bash
cd study_app
python app.py
```

Open http://localhost:5000 in your browser.

---

## Usage

1. **Upload tab** — Enter a topic name + upload a PDF or TXT file → saved to SQLite forever
2. **Generate tab** — Select topic from dropdown, choose output type, click Generate
3. Results appear as formatted cards + raw JSON

## API Endpoints

| Method | Path              | Description               |
|--------|-------------------|---------------------------|
| POST   | /upload_notes     | Upload topic + file       |
| GET    | /topics           | List all saved topics     |
| GET    | /get_notes?topic= | Fetch stored notes        |
| POST   | /generate         | Generate AI study content |
