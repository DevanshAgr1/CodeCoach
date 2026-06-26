from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    ForeignKey, UniqueConstraint, Text, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    handle      = Column(String(100), unique=True, nullable=False)
    rating      = Column(Integer, default=0)
    max_rating  = Column(Integer, default=0)
    rank        = Column(String(50), default="unrated")
    max_rank    = Column(String(50), default="unrated")
    last_synced = Column(Integer, default=0)  # unix timestamp of last successful ingest

    submissions    = relationship("Submission", back_populates="user", cascade="all, delete-orphan")
    rating_changes = relationship("RatingChange", back_populates="user", cascade="all, delete-orphan")


class Problem(Base):
    __tablename__ = "problems"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    contest_id = Column(Integer, nullable=False)
    index      = Column(String(10), nullable=False)
    name       = Column(String(300), nullable=False)
    rating     = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("contest_id", "index", name="uq_problem"),)

    tags        = relationship("Tag", secondary="problem_tags", back_populates="problems")
    submissions = relationship("Submission", back_populates="problem")


class Tag(Base):
    __tablename__ = "tags"
    id       = Column(Integer, primary_key=True, autoincrement=True)
    tag_name = Column(String(100), unique=True, nullable=False)
    problems = relationship("Problem", secondary="problem_tags", back_populates="tags")


class ProblemTag(Base):
    __tablename__ = "problem_tags"
    problem_id = Column(Integer, ForeignKey("problems.id"), primary_key=True)
    tag_id     = Column(Integer, ForeignKey("tags.id"), primary_key=True)


class Submission(Base):
    """
    Stores EVERY submission verdict (OK, WRONG_ANSWER, TIME_LIMIT_EXCEEDED, etc.),
    not just accepted ones. This is required to compute acceptance %, attempted-vs-solved,
    and average failed difficulty -- none of that is possible if failed attempts are discarded.
    """
    __tablename__    = "submissions"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    cf_submission_id = Column(Integer, unique=True, nullable=False)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False)
    problem_id       = Column(Integer, ForeignKey("problems.id"), nullable=False)
    verdict          = Column(String(50))
    timestamp        = Column(Integer)

    user    = relationship("User", back_populates="submissions")
    problem = relationship("Problem", back_populates="submissions")


Index("ix_submissions_user_problem", Submission.user_id, Submission.problem_id)
Index("ix_submissions_user_verdict", Submission.user_id, Submission.verdict)


class RatingChange(Base):
    """
    One row per contest a user has participated in. Sourced from CF's user.rating endpoint.
    This is the data backbone for rating progression charts, contest-consistency analysis,
    and the roadmap engine's "rating gap to next milestone" calculation.
    """
    __tablename__ = "rating_changes"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    contest_id    = Column(Integer, nullable=False)
    contest_name  = Column(String(300), default="")
    rank          = Column(Integer, default=0)
    old_rating    = Column(Integer, default=0)
    new_rating    = Column(Integer, default=0)
    timestamp     = Column(Integer)
    __table_args__ = (UniqueConstraint("user_id", "contest_id", name="uq_rating_change"),)

    user = relationship("User", back_populates="rating_changes")


# ── Engine / Session helpers ─────────────────────────────────────────────────
DATABASE_URL = "sqlite:///./codecoach.db"
engine       = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
