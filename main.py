import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from database import db, create_document, get_documents
from schemas import Leaderboard

app = FastAPI(title="Fantasy 5-Minute Challenge API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Game Content ----------
# Lightweight fantasy-themed question bank (multiple choice)
# Each question: prompt, options (A-D), answer (index), points
QUESTIONS = [
    {
        "prompt": "You find a mysterious rune-stone pulsing with light. What school of magic does it resonate with?",
        "options": ["Evocation", "Illusion", "Abjuration", "Transmutation"],
        "answer": 3,
        "points": 100,
    },
    {
        "prompt": "A dragon's hoard often contains which precious metal most abundantly?",
        "options": ["Mithril", "Gold", "Adamantine", "Electrum"],
        "answer": 1,
        "points": 80,
    },
    {
        "prompt": "Which creature is weakest to silvered weapons?",
        "options": ["Troll", "Werewolf", "Golem", "Wraith"],
        "answer": 1,
        "points": 90,
    },
    {
        "prompt": "What herb is famed for curing poison in many realms?",
        "options": ["Kingsfoil", "Nightshade", "Bloodroot", "Ghostcap"],
        "answer": 0,
        "points": 85,
    },
    {
        "prompt": "Which element do salamanders embody?",
        "options": ["Air", "Water", "Fire", "Earth"],
        "answer": 2,
        "points": 70,
    },
    {
        "prompt": "A ranger's favored terrain grants what benefit?",
        "options": ["Extra damage", "Faster travel", "Spell slots", "Heavy armor use"],
        "answer": 1,
        "points": 75,
    },
    {
        "prompt": "Elven blades are renowned for…",
        "options": ["Weight", "Balance", "Rust resistance", "Holy glow"],
        "answer": 1,
        "points": 60,
    },
    {
        "prompt": "What banishes a specter most reliably?",
        "options": ["Cold iron", "Sunlight", "Consecrated symbols", "Sea salt"],
        "answer": 2,
        "points": 95,
    },
    {
        "prompt": "In ancient prophecies, the comet of the Wolf heralds…",
        "options": ["Famine", "A new age", "Unending winter", "A demon king"],
        "answer": 2,
        "points": 110,
    },
    {
        "prompt": "Which potion color most often indicates healing?",
        "options": ["Crimson", "Emerald", "Cobalt", "Amber"],
        "answer": 0,
        "points": 65,
    },
]

# ---------- Models ----------
class StartResponse(BaseModel):
    session_id: str
    ends_at: str
    questions: List[dict]

class AnswerRequest(BaseModel):
    session_id: str
    question_index: int
    selected_index: int

class SubmitScoreRequest(BaseModel):
    player_name: str
    score: int
    duration_seconds: int
    streak: int

# ---------- In-memory session tracking (short-lived only) ----------
# Acceptable for ephemeral play sessions; scores persist in DB
_sessions: dict = {}
SESSION_DURATION = 300  # seconds (5 minutes)

from uuid import uuid4
import time

@app.get("/")
def read_root():
    return {"message": "Fantasy 5-Minute Challenge API"}

@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response

# ---------- Game Endpoints ----------
@app.post("/api/start", response_model=StartResponse)
def start_session():
    session_id = str(uuid4())
    now = int(time.time())
    _sessions[session_id] = {
        "started": now,
        "ends": now + SESSION_DURATION,
        "score": 0,
        "streak": 0,
        "answered": set(),
    }
    # Provide a shuffled subset of questions
    import random
    indices = list(range(len(QUESTIONS)))
    random.shuffle(indices)
    selected = indices[:10]
    questions = [
        {
            "prompt": QUESTIONS[i]["prompt"],
            "options": QUESTIONS[i]["options"],
            # Do not send answer or points to client
            "index": i,
        }
        for i in selected
    ]
    return StartResponse(
        session_id=session_id,
        ends_at=datetime.utcfromtimestamp(now + SESSION_DURATION).isoformat() + "Z",
        questions=questions,
    )

@app.post("/api/answer")
def answer(req: AnswerRequest):
    s = _sessions.get(req.session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if int(time.time()) > s["ends"]:
        raise HTTPException(status_code=400, detail="Session expired")
    if req.question_index in s["answered"]:
        raise HTTPException(status_code=400, detail="Already answered")

    q = QUESTIONS[req.question_index]
    correct = q["answer"] == req.selected_index
    if correct:
        s["score"] += q["points"]
        s["streak"] += 1
    else:
        s["streak"] = 0
    s["answered"].add(req.question_index)

    return {
        "correct": bool(correct),
        "score": s["score"],
        "streak": s["streak"],
        "ends_at": s["ends"],
    }

@app.post("/api/submit")
def submit_score(req: SubmitScoreRequest):
    if req.duration_seconds > SESSION_DURATION:
        raise HTTPException(status_code=400, detail="Duration exceeds 5 minutes")

    # Persist to DB leaderboard
    try:
        entry = Leaderboard(
            player_name=req.player_name,
            score=req.score,
            duration_seconds=req.duration_seconds,
            streak=req.streak,
        )
        inserted_id = create_document("leaderboard", entry)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {str(e)}")

    return {"status": "ok", "id": inserted_id}

@app.get("/api/leaderboard")
def get_leaderboard(limit: int = 10):
    try:
        docs = get_documents("leaderboard", {}, limit)
        # Convert ObjectId to string and sort by score desc
        for d in docs:
            d["_id"] = str(d.get("_id"))
        docs.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {"items": docs[:limit]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
