import pathlib

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from models import init_db, get_db, User, Problem
import crud
import cf_api

app = FastAPI(title="CodeCoach API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── DB init on startup ─────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    init_db()
    db = next(get_db())
    try:
        if db.query(Problem).count() == 0:
            print("First run: fetching problem set from Codeforces...")
            try:
                problems = cf_api.fetch_all_problems()
                crud.upsert_problems(db, problems)
                print(f"Loaded {len(problems)} problems.")
            except Exception as e:
                print(f"Warning: could not pre-load problems ({e}). "
                      f"Ingesting a handle will retry this automatically.")
    finally:
        db.close()


# ── Serve frontend ─────────────────────────────────────────────────────────────
FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"


@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


# ── Pydantic schemas ────────────────────────────────────────────────────────────
class IngestRequest(BaseModel):
    handle: str


class RecommendRequest(BaseModel):
    handle: str
    tag_filter: Optional[str] = None
    difficulty: Optional[str] = None  # easy / medium / hard / None=auto


# ── Helpers ──────────────────────────────────────────────────────────────────
def _get_user_or_404(db: Session, handle: str) -> User:
    user = db.query(User).filter(User.handle == handle).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"'{handle}' not found. Ingest it first via /api/ingest.")
    return user


def _ensure_problems_loaded(db: Session):
    if db.query(Problem).count() == 0:
        problems = cf_api.fetch_all_problems()
        crud.upsert_problems(db, problems)


def _get_or_ingest_user(db: Session, handle: str) -> User:
    user = db.query(User).filter(User.handle == handle).first()
    if user:
        return user
    _ensure_problems_loaded(db)
    return crud.ingest_user(db, handle, [])


# ── Core routes ─────────────────────────────────────────────────────────────────
@app.post("/api/ingest")
def ingest_user(req: IngestRequest, db: Session = Depends(get_db)):
    try:
        _ensure_problems_loaded(db)
        user = crud.ingest_user(db, req.handle, [])
        return {
            "status": "ok",
            "handle": user.handle,
            "rating": user.rating,
            "max_rating": user.max_rating,
            "rank": user.rank,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/user/{handle}")
def get_user(handle: str, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, handle)
    return {
        "handle": user.handle,
        "rating": user.rating,
        "max_rating": user.max_rating,
        "rank": user.rank,
        "max_rank": user.max_rank,
    }


@app.get("/api/skills/{handle}")
def get_skills(handle: str, db: Session = Depends(get_db)):
    """Skill Intelligence Engine: per-topic attempted/solved/acceptance/mastery/weakness/trend."""
    user = _get_user_or_404(db, handle)
    skills = crud.get_skill_intelligence(db, user.id, user.rating or 1200)
    weak_strong = crud.get_weak_strong_topics(db, user.id, user.rating or 1200)
    return {
        "skills": skills,
        "weak_topics": weak_strong["weak"],
        "strong_topics": weak_strong["strong"],
        "untested_topics": weak_strong["untested"],
    }


@app.get("/api/difficulty-bands/{handle}")
def get_difficulty_bands(handle: str, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, handle)
    return {"bands": crud.get_difficulty_bands(db, user.id)}


@app.get("/api/timeline/{handle}")
def get_timeline(handle: str, tag: Optional[str] = None, months: int = 12,
                  db: Session = Depends(get_db)):
    user = _get_user_or_404(db, handle)
    return {"timeline": crud.get_activity_timeline(db, user.id, tag=tag, months=months)}


@app.get("/api/consistency/{handle}")
def get_consistency(handle: str, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, handle)
    return crud.get_consistency(db, user.id)


@app.get("/api/rating-history/{handle}")
def get_rating_history(handle: str, db: Session = Depends(get_db)):
    from models import RatingChange
    user = _get_user_or_404(db, handle)
    rows = (db.query(RatingChange)
            .filter(RatingChange.user_id == user.id)
            .order_by(RatingChange.timestamp).all())
    return {
        "history": [
            {"contest": r.contest_name, "rank": r.rank, "old_rating": r.old_rating,
             "new_rating": r.new_rating, "timestamp": r.timestamp}
            for r in rows
        ]
    }


@app.post("/api/recommend")
def recommend(req: RecommendRequest, db: Session = Depends(get_db)):
    try:
        user = _get_user_or_404(db, req.handle)
        problems = crud.get_recommendations(
            db, user.id, user.rating or 1200,
            tag_filter=req.tag_filter, difficulty=req.difficulty,
        )
        return {"recommendations": problems}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/roadmap/{handle}")
def get_roadmap(handle: str, db: Session = Depends(get_db)):
    user = _get_user_or_404(db, handle)
    roadmap = crud.get_roadmap(db, user.id, user.rating or 1200)
    roadmap["current_rating"] = user.rating
    return roadmap


@app.get("/api/compare/{handle_a}/{handle_b}")
def compare(handle_a: str, handle_b: str, db: Session = Depends(get_db)):
    """Comparison mode. Auto-ingests either handle if it hasn't been loaded yet."""
    try:
        user_a = _get_or_ingest_user(db, handle_a)
        user_b = _get_or_ingest_user(db, handle_b)
        return crud.compare_handles(db, user_a, user_b)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
