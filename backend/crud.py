"""
CodeCoach analytics & recommendation core.

Design principle: every number this module produces must be traceable back to a
plain-English explanation. There is no black-box ML here on purpose -- a transparent
weighted formula that a user can question and a developer can defend in an interview
is worth more than a marginally-fancier model nobody can explain.
"""

import time
from collections import defaultdict
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

import cf_api
from models import User, Problem, Tag, ProblemTag, Submission, RatingChange

# ── Constants ─────────────────────────────────────────────────────────────────

# Codeforces rank tiers, in ascending rating-threshold order.
RANK_TIERS = [
    (0,    "Newbie"),
    (1200, "Pupil"),
    (1400, "Specialist"),
    (1600, "Expert"),
    (1900, "Candidate Master"),
    (2100, "Master"),
    (2300, "International Master"),
    (2400, "Grandmaster"),
    (2600, "International Grandmaster"),
    (2900, "Legendary Grandmaster"),
]

DAY = 86400

# Tags too rare/niche on Codeforces to be useful analytics topics -- they show up
# with 0-1 attempts for almost every user and add noise to the skill table and
# weak-topic list without being practical training targets. Filtered out at
# ingestion time so they never enter the tags/problem_tags tables at all.
EXCLUDED_TAGS = frozenset({
    "expression parsing",
    "chinese remainder theorem",
    "flows",
    "*special",
    "matrices",
    "2-sat",
    "communication",
    "divide and conquer",
    "fft",
    "graph matchings",
    "interactive",
    "meet-in-the-middle",
    "probabilities",
    "string suffix structures",
    "ternary search",
})


# ── Upsert / ingestion ────────────────────────────────────────────────────────

def upsert_user(db: Session, handle: str) -> User:
    info = cf_api.fetch_user_info(handle)
    user = db.query(User).filter(User.handle == handle).first()
    if not user:
        user = User(handle=handle)
        db.add(user)
    user.rating      = info.get("rating", 0)
    user.max_rating  = info.get("maxRating", 0)
    user.rank        = info.get("rank", "unrated")
    user.max_rank    = info.get("maxRank", info.get("rank", "unrated"))
    user.last_synced = int(time.time())
    db.commit()
    db.refresh(user)
    return user


def upsert_problems(db: Session, problems_raw: list):
    """Bulk-insert problems + tags, skip duplicates. Runs once on first startup."""
    existing_tags = {t.tag_name: t for t in db.query(Tag).all()}
    existing_problems = {
        (p.contest_id, p.index): p for p in db.query(Problem).all()
    }

    for p in problems_raw:
        cid    = p.get("contestId")
        idx    = p.get("index")
        name   = p.get("name", "")
        rating = p.get("rating", 0)
        tags   = p.get("tags", [])

        if not cid or not idx:
            continue

        prob = existing_problems.get((cid, idx))
        if not prob:
            prob = Problem(contest_id=cid, index=idx, name=name, rating=rating)
            db.add(prob)
            db.flush()
            existing_problems[(cid, idx)] = prob

        for tag_name in tags:
            if tag_name in EXCLUDED_TAGS:
                continue
            tag = existing_tags.get(tag_name)
            if not tag:
                tag = Tag(tag_name=tag_name)
                db.add(tag)
                db.flush()
                existing_tags[tag_name] = tag

            link_exists = db.query(ProblemTag).filter(
                ProblemTag.problem_id == prob.id,
                ProblemTag.tag_id == tag.id
            ).first()
            if not link_exists:
                db.add(ProblemTag(problem_id=prob.id, tag_id=tag.id))

    db.commit()


def upsert_submissions(db: Session, user: User, subs_raw: list):
    """
    Stores EVERY verdict, not just 'OK'. Wrong answers, TLEs, etc. are the data
    behind acceptance %, attempted-vs-solved, and avg-failed-difficulty -- discarding
    them (as the old version did) makes those metrics impossible to compute.
    """
    known_ids = {
        row[0] for row in db.query(Submission.cf_submission_id)
        .filter(Submission.user_id == user.id).all()
    }
    problem_lookup = {
        (p.contest_id, p.index): p.id for p in db.query(Problem).all()
    }

    new_rows = []
    for s in subs_raw:
        cf_id = s.get("id")
        if cf_id in known_ids:
            continue
        prob_raw = s.get("problem", {})
        cid = prob_raw.get("contestId")
        idx = prob_raw.get("index")
        if not cid or not idx:
            continue
        problem_id = problem_lookup.get((cid, idx))
        if not problem_id:
            continue
        new_rows.append(Submission(
            cf_submission_id=cf_id,
            user_id=user.id,
            problem_id=problem_id,
            verdict=s.get("verdict", "UNKNOWN"),
            timestamp=s.get("creationTimeSeconds", 0),
        ))
        known_ids.add(cf_id)

    if new_rows:
        db.bulk_save_objects(new_rows)
    db.commit()


def upsert_rating_history(db: Session, user: User, changes_raw: list):
    known = {
        row[0] for row in db.query(RatingChange.contest_id)
        .filter(RatingChange.user_id == user.id).all()
    }
    new_rows = []
    for c in changes_raw:
        cid = c.get("contestId")
        if cid in known:
            continue
        new_rows.append(RatingChange(
            user_id=user.id,
            contest_id=cid,
            contest_name=c.get("contestName", ""),
            rank=c.get("rank", 0),
            old_rating=c.get("oldRating", 0),
            new_rating=c.get("newRating", 0),
            timestamp=c.get("ratingUpdateTimeSeconds", 0),
        ))
        known.add(cid)
    if new_rows:
        db.bulk_save_objects(new_rows)
    db.commit()


def ingest_user(db: Session, handle: str, problems_raw: list) -> User:
    user = upsert_user(db, handle)
    subs_raw = cf_api.fetch_user_submissions(handle)
    upsert_submissions(db, user, subs_raw)
    rating_raw = cf_api.fetch_user_rating_history(handle)
    upsert_rating_history(db, user, rating_raw)
    return user


# ── Skill Intelligence Engine ─────────────────────────────────────────────────

def _compute_mastery(solved_count: int, acceptance_pct: Optional[float],
                      avg_solved_rating: Optional[float], user_rating: int,
                      recent_attempted: int = 0, recent_acceptance: Optional[float] = None,
                      avg_solved_rating_recent: Optional[float] = None) -> float:
    """
    mastery_score (0-100) = breadth (up to 40) + accuracy (up to 35) + difficulty edge (up to 25)

    breadth   rewards solving a meaningful volume of problems in the topic, capped at 15
              solves so grinding one topic forever stops paying off.
    accuracy  rewards a high ratio of solved/attempted, BLENDED toward your last-90-day
              acceptance once there are at least 3 recent attempts to trust -- otherwise
              a strong streak from a year ago can mask a topic you're failing right now
              (or an old slump can unfairly bury a topic you've since gotten good at).
    diff_edge rewards solving problems above your own rating, using your recent average
              solved difficulty when available (same reasoning -- what you can solve
              NOW matters more than what you solved long ago), falling back to the
              all-time average if you haven't solved anything in this topic recently.
    """
    breadth = min(solved_count, 15) / 15 * 40

    if recent_attempted >= 3 and recent_acceptance is not None:
        accuracy_pct = 0.6 * recent_acceptance + 0.4 * (acceptance_pct or 0)
    else:
        accuracy_pct = acceptance_pct or 0
    accuracy = accuracy_pct * 35

    effective_avg_rating = avg_solved_rating_recent or avg_solved_rating
    if solved_count > 0 and effective_avg_rating and user_rating:
        edge = max(-300, min(300, effective_avg_rating - user_rating))
        diff_bonus = (edge + 300) / 600 * 25
    else:
        diff_bonus = 0

    return round(min(100.0, breadth + accuracy + diff_bonus), 1)


def _trend_label(attempts_recent: int, attempts_prior: int, attempted_total: int) -> str:
    if attempted_total == 0:
        return "untested"
    if attempts_recent == 0 and attempts_prior == 0:
        return "inactive"
    if attempts_recent > attempts_prior:
        return "rising"
    if attempts_recent < attempts_prior:
        return "falling"
    return "steady"


def get_skill_intelligence(db: Session, user_id: int, user_rating: int) -> list[dict]:
    """
    Per-topic breakdown: attempted, solved, acceptance %, avg solved/failed difficulty,
    mastery score, weakness score, and a recent activity trend.

    weakness_score = 100 - mastery_score, EXCEPT topics with zero attempts are fixed at 75:
    an untested topic is a roadmap risk (unknown gap), but it hasn't actually been failed,
    so it shouldn't score as badly as a topic the user has genuinely struggled with.
    """
    now = int(time.time())
    recent_cutoff = now - 30 * DAY
    prior_cutoff = now - 60 * DAY
    recency_window = now - 90 * DAY  # window for recency-weighted mastery (see _compute_mastery)

    base_sql = text("""
        SELECT
            t.tag_name,
            COUNT(DISTINCT CASE WHEN s.id IS NOT NULL THEN s.problem_id END) AS attempted,
            COUNT(DISTINCT CASE WHEN s.verdict = 'OK' THEN s.problem_id END) AS solved,
            AVG(CASE WHEN s.verdict = 'OK' THEN p.rating END)               AS avg_solved_rating,
            AVG(CASE WHEN s.verdict != 'OK' AND s.id IS NOT NULL
                     AND p.id NOT IN (
                         SELECT problem_id FROM submissions
                         WHERE user_id = :uid AND verdict = 'OK'
                     )
                     THEN p.rating END)                                     AS avg_failed_rating,
            COUNT(DISTINCT CASE WHEN s.timestamp >= :recent THEN s.id END)   AS attempts_recent,
            COUNT(DISTINCT CASE WHEN s.timestamp >= :prior
                                  AND s.timestamp < :recent THEN s.id END)   AS attempts_prior,
            COUNT(DISTINCT CASE WHEN s.timestamp >= :recency_window
                                  THEN s.problem_id END)                     AS attempted_90d,
            COUNT(DISTINCT CASE WHEN s.verdict = 'OK'
                                  AND s.timestamp >= :recency_window
                                  THEN s.problem_id END)                     AS solved_90d,
            AVG(CASE WHEN s.verdict = 'OK' AND s.timestamp >= :recency_window
                     THEN p.rating END)                                     AS avg_solved_rating_90d,
            MAX(s.timestamp)                                                 AS last_active
        FROM tags t
        JOIN problem_tags pt ON pt.tag_id = t.id
        JOIN problems p      ON p.id = pt.problem_id
        LEFT JOIN submissions s
               ON s.problem_id = p.id
              AND s.user_id    = :uid
        GROUP BY t.tag_name
    """)
    rows = db.execute(base_sql, {"uid": user_id, "recent": recent_cutoff, "prior": prior_cutoff,
                                  "recency_window": recency_window}).fetchall()

    results = []
    for r in rows:
        (tag, attempted, solved, avg_solved, avg_failed, recent, prior,
         attempted_90d, solved_90d, avg_solved_rating_90d, last_active) = r
        attempted = attempted or 0
        solved = solved or 0
        attempted_90d = attempted_90d or 0
        solved_90d = solved_90d or 0
        acceptance_pct = (solved / attempted) if attempted > 0 else None
        recent_acceptance = (solved_90d / attempted_90d) if attempted_90d > 0 else None

        mastery = _compute_mastery(solved, acceptance_pct, avg_solved, user_rating,
                                    recent_attempted=attempted_90d, recent_acceptance=recent_acceptance,
                                    avg_solved_rating_recent=avg_solved_rating_90d)
        weakness = 75.0 if attempted == 0 else round(100 - mastery, 1)
        trend = _trend_label(recent or 0, prior or 0, attempted)

        results.append({
            "tag": tag,
            "attempted": attempted,
            "solved": solved,
            "acceptance_pct": round(acceptance_pct * 100, 1) if acceptance_pct is not None else None,
            "avg_solved_rating": round(avg_solved) if avg_solved else None,
            "avg_failed_rating": round(avg_failed) if avg_failed else None,
            "mastery_score": mastery,
            "weakness_score": weakness,
            "trend": trend,
            "last_active": last_active,
        })

    return sorted(results, key=lambda x: x["weakness_score"], reverse=True)


def get_weak_strong_topics(db: Session, user_id: int, user_rating: int) -> dict:
    """
    Splits "weak" into two genuinely different categories instead of one blended
    list ranked by weakness_score alone (where untested topics, forced to a flat
    75, were drowning out topics you've actually tried and struggled with):

      - struggling: attempted at least once, ranked by weakness_score -- these
        are the actionable "you keep failing this" signals.
      - untested:   never attempted at all -- a coverage gap, not a failure.

    Conflating them made the old single list less useful: "never tried X" and
    "keep failing Y" call for different reactions, and a user couldn't tell
    which they were looking at without reading every row.
    """
    skills = get_skill_intelligence(db, user_id, user_rating)
    attempted_skills = [s for s in skills if s["attempted"] > 0]
    untested_skills = [s for s in skills if s["attempted"] == 0]

    # Floor of 50 (below-average mastery), not a plain top-6 slice: with few
    # attempted topics, an unfloored slice would pad itself with genuinely
    # STRONG topics just to fill 6 slots, and even a below-50-mastery floor
    # has to allow for topics that simply haven't accumulated enough solves
    # yet (volume is 40% of the formula) without being mislabeled "struggling."
    # If fewer than 6 genuinely qualify, showing fewer is the honest count.
    struggling = sorted(
        [s for s in attempted_skills if s["weakness_score"] >= 50],
        key=lambda x: x["weakness_score"], reverse=True
    )[:6]
    struggling_tags = {t["tag"] for t in struggling}
    strong = [t for t in sorted(attempted_skills, key=lambda x: x["mastery_score"], reverse=True)
              if t["tag"] not in struggling_tags][:6]
    untested = sorted(untested_skills, key=lambda x: x["tag"])[:6]

    return {"weak": struggling, "strong": strong, "untested": untested}


# ── Difficulty-band analysis ──────────────────────────────────────────────────

def get_difficulty_bands(db: Session, user_id: int) -> list[dict]:
    """
    Attempted/solved/acceptance breakdown per EXACT Codeforces rating value
    (800, 900, 1000, ...), not grouped into named bands -- gives a much more
    precise picture of exactly where solve rate starts dropping off, rather
    than smearing it across a wide "Medium" bucket. Only includes ratings the
    user has actually attempted, sorted ascending starting from 800.
    """
    sql = text("""
        SELECT p.rating,
               COUNT(DISTINCT CASE WHEN s.id IS NOT NULL THEN s.problem_id END) AS attempted,
               COUNT(DISTINCT CASE WHEN s.verdict = 'OK' THEN s.problem_id END) AS solved
        FROM problems p
        LEFT JOIN submissions s ON s.problem_id = p.id AND s.user_id = :uid
        WHERE p.rating > 0
        GROUP BY p.rating
        HAVING attempted > 0
        ORDER BY p.rating ASC
    """)
    rows = db.execute(sql, {"uid": user_id}).fetchall()

    out = []
    for rating, attempted, solved in rows:
        attempted = attempted or 0
        solved = solved or 0
        acc = round(solved / attempted * 100, 1) if attempted else None
        out.append({
            "rating": rating, "attempted": attempted, "solved": solved, "acceptance_pct": acc,
        })
    return out


# ── Topic activity timeline ───────────────────────────────────────────────────

def get_activity_timeline(db: Session, user_id: int, tag: Optional[str] = None,
                           months: int = 12) -> list[dict]:
    """Monthly attempts-vs-solves trend, optionally scoped to one topic."""
    if tag:
        sql = text("""
            SELECT strftime('%Y-%m', datetime(s.timestamp, 'unixepoch')) AS month,
                   COUNT(*) AS attempts,
                   SUM(CASE WHEN s.verdict = 'OK' THEN 1 ELSE 0 END) AS solves
            FROM submissions s
            JOIN problems p ON p.id = s.problem_id
            JOIN problem_tags pt ON pt.problem_id = p.id
            JOIN tags t ON t.id = pt.tag_id
            WHERE s.user_id = :uid AND t.tag_name = :tag
            GROUP BY month ORDER BY month
        """)
        rows = db.execute(sql, {"uid": user_id, "tag": tag}).fetchall()
    else:
        sql = text("""
            SELECT strftime('%Y-%m', datetime(timestamp, 'unixepoch')) AS month,
                   COUNT(*) AS attempts,
                   SUM(CASE WHEN verdict = 'OK' THEN 1 ELSE 0 END) AS solves
            FROM submissions
            WHERE user_id = :uid
            GROUP BY month ORDER BY month
        """)
        rows = db.execute(sql, {"uid": user_id}).fetchall()

    data = [{"month": m, "attempts": a, "solves": s} for m, a, s in rows if m]
    return data[-months:]


# ── Consistency / inactivity detection ────────────────────────────────────────

def get_consistency(db: Session, user_id: int, weeks: int = 16) -> dict:
    now = int(time.time())
    earliest = now - weeks * 7 * DAY

    rows = db.execute(text("""
        SELECT timestamp FROM submissions WHERE user_id = :uid AND timestamp >= :earliest
    """), {"uid": user_id, "earliest": earliest}).fetchall()

    active_weeks = set()
    for (ts,) in rows:
        week_index = (now - ts) // (7 * DAY)
        active_weeks.add(weeks - 1 - week_index)

    timeline = [i in active_weeks for i in range(weeks)]

    current_streak = 0
    for active in reversed(timeline):
        if active:
            current_streak += 1
        else:
            break

    longest_streak, run = 0, 0
    for active in timeline:
        run = run + 1 if active else 0
        longest_streak = max(longest_streak, run)

    last_ts_row = db.execute(text(
        "SELECT MAX(timestamp) FROM submissions WHERE user_id = :uid"
    ), {"uid": user_id}).fetchone()
    last_ts = last_ts_row[0] if last_ts_row else None

    if last_ts is None:
        days_since_last, status = None, "no activity yet"
    else:
        days_since_last = round((now - last_ts) / DAY, 1)
        if days_since_last <= 3:
            status = "active"
        elif days_since_last <= 10:
            status = "cooling off"
        else:
            status = "inactive"

    return {
        "weekly_activity": timeline,
        "current_streak_weeks": current_streak,
        "longest_streak_weeks": longest_streak,
        "days_since_last_submission": days_since_last,
        "status": status,
    }


# ── Recommendation Engine v2 ───────────────────────────────────────────────────
#
# No randomness. Every candidate problem is scored from four explainable signals:
#   1. weak_component  -- sum of weakness_score for any of the problem's tags that
#                          are genuinely weak for this user (threshold >= 50/100)
#   2. fit_component   -- how close the problem's rating sits to the center of the
#                          target difficulty band
#   3. trend_bonus     -- extra urgency if a matched weak topic's activity is
#                          falling or inactive (use-it-or-lose-it skills)
#   4. revisit_bonus   -- small bonus for problems already attempted but not solved
#                          (finishing unfinished business beats starting fresh)
# The same four signals are turned directly into the human-readable "reason" string,
# so the score and the explanation can never drift apart.

_BAND_DEFS = {
    "easy":   (-400, -100, "Easy"),
    "medium": (-100,  200, "Medium"),
    "hard":   ( 200,  500, "Hard"),
}


def _band_for(user_rating: int, difficulty: Optional[str]):
    # Default ("Growth") band is intentionally tight: -100 to +300 relative to the
    # user's current rating. A higher-rated user gets zero value from being shown
    # problems they could solve in their sleep -- the default surface should always
    # sit at or above their current level, not blend in a wide swath of easy ones.
    lo_off, hi_off, label = _BAND_DEFS.get(difficulty, (-100, 300, "Growth"))
    lo = max(800, user_rating + lo_off)
    hi = max(lo + 100, user_rating + hi_off)
    return lo, hi, label


def _fetch_candidates(db: Session, user_id: int, lo: int, hi: int,
                       tag_filter: Optional[str], pool_size: int = 400) -> list:
    """
    Returns unsolved problems in [lo, hi], capped per discrete CF rating value
    (CF problem ratings are always multiples of 100) rather than a flat overall
    LIMIT. A flat LIMIT + 'ORDER BY rating ASC' can be entirely consumed by the
    single lowest rating in the range when that bucket alone has hundreds of
    problems -- e.g. a [1286,1686] band silently returning ONLY 1300-rated
    problems and never reaching 1400/1500/1600. Capping per-bucket guarantees
    every rating value in the band gets representation.
    """
    num_buckets = max(1, (hi - lo) // 100 + 1)
    per_bucket_cap = max(15, pool_size // num_buckets)

    params = {"uid": user_id, "lo": lo, "hi": hi, "cap": per_bucket_cap}
    tag_join, tag_clause = "", ""
    if tag_filter:
        tag_join = "JOIN problem_tags ptf ON ptf.problem_id = p.id JOIN tags tf ON tf.id = ptf.tag_id"
        tag_clause = "AND tf.tag_name = :tag"
        params["tag"] = tag_filter

    sql = text(f"""
        WITH grouped AS (
            SELECT p.id, p.contest_id, p.\"index\" AS idx, p.name, p.rating,
                   GROUP_CONCAT(DISTINCT t2.tag_name) AS tags,
                   (SELECT COUNT(*) FROM submissions WHERE problem_id = p.id AND user_id = :uid) AS attempts_on_problem
            FROM problems p
            JOIN problem_tags pt2 ON pt2.problem_id = p.id
            JOIN tags t2 ON t2.id = pt2.tag_id
            {tag_join}
            WHERE p.rating BETWEEN :lo AND :hi
              {tag_clause}
              AND p.id NOT IN (
                  SELECT problem_id FROM submissions WHERE user_id = :uid AND verdict = 'OK'
              )
            GROUP BY p.id
        ),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY rating ORDER BY id) AS rn
            FROM grouped
        )
        SELECT id, contest_id, idx, name, rating, tags, attempts_on_problem
        FROM ranked
        WHERE rn <= :cap
    """)
    return db.execute(sql, params).fetchall()


def _build_reason(matched_weak: list, rating: int, band_label: str, band: tuple,
                   is_revisit: bool, tag_filter: Optional[str], user_rating: int) -> str:
    parts = []
    if matched_weak:
        best_tag, best_stat = max(matched_weak, key=lambda x: x[1]["weakness_score"])
        acc = best_stat["acceptance_pct"]
        acc_str = f"{acc}% acceptance" if acc is not None else "no attempts yet"
        parts.append(
            f"Targets '{best_tag}', your weakest matched topic "
            f"(mastery {best_stat['mastery_score']}/100, {acc_str})."
        )
        if best_stat["trend"] in ("falling", "inactive"):
            parts.append(f"Practice on '{best_tag}' has gone {best_stat['trend']} recently.")
    elif tag_filter:
        parts.append(f"Matches your selected topic '{tag_filter}'.")
    else:
        parts.append("General growth-band pick — no single weak topic dominates here.")

    if band[0] == band[1]:
        parts.append(f"Rated {rating}, a {band_label} step {rating - user_rating:+d} from your {user_rating} rating.")
    else:
        parts.append(f"Rated {rating}, inside your {band_label} band ({band[0]}–{band[1]}) for a {user_rating} rating.")
    if is_revisit:
        parts.append("You've attempted this one before — a good candidate to finish.")
    return " ".join(parts)


def _fetch_and_score(db: Session, user_id: int, user_rating: int, skill_by_tag: dict,
                      lo: int, hi: int, label: str, tag_filter: Optional[str],
                      pool_size: int = 400) -> list[dict]:
    """Fetches candidates in [lo, hi] and scores all of them (unsliced, sorted desc)."""
    rows = _fetch_candidates(db, user_id, lo, hi, tag_filter, pool_size)

    scored = []
    for pid, cid, idx, name, rating, tags_str, attempts_on_problem in rows:
        tags_list = (tags_str or "").split(",")
        matched_weak = [(t, skill_by_tag[t]) for t in tags_list
                         if t in skill_by_tag and skill_by_tag[t]["weakness_score"] >= 50]

        # max(), not sum() -- the reason text below cites only the single weakest
        # matched tag, so the score must be driven by that same tag or a multi-tag
        # problem could win purely from tag-count, for a reason it never actually states.
        weak_component = max((s["weakness_score"] for _, s in matched_weak), default=0)
        fit_component = max(0, 25 - abs(rating - (lo + hi) / 2) / 8)
        trend_bonus = 15 if any(s["trend"] in ("falling", "inactive") for _, s in matched_weak) else 0
        is_revisit = (attempts_on_problem or 0) > 0
        revisit_bonus = 10 if is_revisit else 0

        score = weak_component + fit_component + trend_bonus + revisit_bonus
        reason = _build_reason(matched_weak, rating, label, (lo, hi), is_revisit, tag_filter, user_rating)
        primary_tag = max(matched_weak, key=lambda x: x[1]["weakness_score"])[0] if matched_weak else None

        scored.append({
            "id": pid, "contest_id": cid, "index": idx, "name": name, "rating": rating,
            "tags": tags_str, "url": f"https://codeforces.com/problemset/problem/{cid}/{idx}",
            "score": round(score, 1), "reason": reason, "band": label,
            "primary_tag": primary_tag,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def _score_band(db: Session, user_id: int, user_rating: int, skill_by_tag: dict,
                 difficulty: Optional[str], tag_filter: Optional[str], take: int) -> list[dict]:
    lo, hi, label = _band_for(user_rating, difficulty)
    scored = _fetch_and_score(db, user_id, user_rating, skill_by_tag, lo, hi, label, tag_filter)
    return scored[:take]


def _diversify_by_topic(scored: list[dict], count: int) -> list[dict]:
    """Within a single exact-rating bucket, spreads picks across different primary
    weak topics where possible, falling back to best-score once topics run out."""
    selected, used_tags = [], set()
    remaining = list(scored)

    i = 0
    while len(selected) < count and i < len(remaining):
        c = remaining[i]
        if c["primary_tag"] not in used_tags:
            selected.append(remaining.pop(i))
            used_tags.add(c["primary_tag"])
        else:
            i += 1

    while len(selected) < count and remaining:
        selected.append(remaining.pop(0))

    return selected[:count]


def _round_to_nearest_100(rating: int):
    """Rounds to the nearest 100 (half rounds up), returning (rounded, rounded_up)."""
    base = (rating // 100) * 100
    remainder = rating - base
    rounds_up = remainder > 50
    return (base + 100 if rounds_up else base), rounds_up


def get_roadmap_picks(db: Session, user_id: int, user_rating: int,
                       skill_by_tag: dict, limit: int = 7) -> list[dict]:
    """
    Roadmap-specific picks, using exact rating buckets rather than a continuous
    range, with an allocation that depends on where the user sits in their
    current 100-point bracket:

      - Upper half of the bracket (e.g. 1380, >50 into the 1300s): round UP to
        1400 and treat all three bands (1400/1500/1600) as pure stretch,
        split evenly (3/2/2) -- the user is already performing near the next
        bracket, so there's no value in a "current level" problem.
      - Lower-or-mid half (e.g. 1320): round DOWN to 1300 and give just one
        problem at that level (consolidation), putting most of the effort
        (3/3) into the next two brackets up (1400/1500) for actual growth.
    """
    rounded, rounds_up = _round_to_nearest_100(user_rating)
    band_values = [rounded, rounded + 100, rounded + 200]
    counts = [3, 2, 2] if rounds_up else [1, 3, 3]

    picks, leftovers = [], []
    for rating_value, count in zip(band_values, counts):
        scored = _fetch_and_score(db, user_id, user_rating, skill_by_tag,
                                   rating_value, rating_value, "Stretch", None, pool_size=100)
        chosen = _diversify_by_topic(scored, count)
        picks.extend(chosen)
        chosen_ids = {c["id"] for c in chosen}
        leftovers.extend(c for c in scored if c["id"] not in chosen_ids)

    # If a bucket came up short (e.g. no unsolved problems left at that exact
    # rating), top up from the best-scoring leftovers across all three bands
    # rather than silently returning fewer than `limit` problems.
    if len(picks) < limit:
        leftovers.sort(key=lambda x: x["score"], reverse=True)
        existing_ids = {p["id"] for p in picks}
        for c in leftovers:
            if len(picks) >= limit:
                break
            if c["id"] not in existing_ids:
                picks.append(c)
                existing_ids.add(c["id"])

    return picks[:limit]


def get_recommendations(db: Session, user_id: int, user_rating: int,
                         tag_filter: Optional[str] = None,
                         difficulty: Optional[str] = None,
                         limit: int = 20) -> list[dict]:
    skills = get_skill_intelligence(db, user_id, user_rating)
    skill_by_tag = {s["tag"]: s for s in skills}

    if difficulty in ("easy", "medium", "hard"):
        return _score_band(db, user_id, user_rating, skill_by_tag, difficulty, tag_filter, limit)

    # Auto mode: a single Growth band (-100 to +300 relative to current rating),
    # scored purely by weak-topic relevance. Previously this blended in the much
    # lower "easy" band by default, which meant a high-rated user got recommended
    # problems far below their level even with no filter selected -- fixed.
    return _score_band(db, user_id, user_rating, skill_by_tag, None, tag_filter, limit)


# ── Roadmap Engine ─────────────────────────────────────────────────────────────

def get_roadmap(db: Session, user_id: int, user_rating: int) -> dict:
    current_label = RANK_TIERS[0][1]
    current_threshold = RANK_TIERS[0][0]
    next_threshold, next_label = None, None
    for thresh, label in RANK_TIERS:
        if user_rating >= thresh:
            current_label = label
            current_threshold = thresh
        else:
            next_threshold, next_label = thresh, label
            break

    if next_label is None:
        roadmap_title = f"Beyond {current_label} — Legendary Grandmaster Track"
        gap = 0
        progress_pct = 100
    else:
        roadmap_title = f"Road to {next_label}"
        gap = max(0, next_threshold - user_rating)
        # Progress THROUGH THE CURRENT BRACKET, not relative to the absolute
        # target rating. The old formula (100 - gap/next_threshold*100) made the
        # same 200-point gap show a higher percentage in a high tier than a low
        # one purely because of the larger denominator -- mathematically wrong.
        bracket_size = max(1, next_threshold - current_threshold)
        progress_pct = round(min(100, max(0, (user_rating - current_threshold) / bracket_size * 100)), 1)

    if gap <= 100:
        plan = "Maintenance — 5 problems/week, prioritize consistency over volume."
    elif gap <= 300:
        plan = "Growth — 8–10 problems/week across your weakest topics, plus 1 rated contest."
    else:
        plan = "Intensive — 12–15 problems/week, 2 rated contests this month, heavy focus on your weakest 3 topics."

    skills = get_skill_intelligence(db, user_id, user_rating)
    skill_by_tag = {s["tag"]: s for s in skills}
    # Prefer topics you've actually attempted and struggled with -- concrete,
    # drillable gaps -- over ones you've simply never tried, since "next
    # contest" framing is near-term tactical prep, not long-term coverage.
    # Floor of 50 (same as get_weak_strong_topics) so this never pads itself
    # with a genuinely strong topic just to reach 3 slots. Falls back to
    # untested topics only if there aren't enough genuinely struggling ones.
    struggling = [s for s in skills if s["attempted"] > 0 and s["weakness_score"] >= 50]
    untested = [s for s in skills if s["attempted"] == 0]
    focus_topics = sorted(struggling, key=lambda x: x["weakness_score"], reverse=True)[:3]
    if len(focus_topics) < 3:
        focus_topics += sorted(untested, key=lambda x: x["tag"])[:3 - len(focus_topics)]
    next_problems = get_roadmap_picks(db, user_id, user_rating, skill_by_tag, limit=7)

    return {
        "current_rank": current_label,
        "roadmap_title": roadmap_title,
        "next_milestone_rating": next_threshold,
        "rating_gap": gap,
        "progress_pct": progress_pct,
        "weekly_plan": plan,
        "focus_topics": focus_topics,
        "next_problems": next_problems,
    }


# ── Comparison Mode ────────────────────────────────────────────────────────────

def compare_handles(db: Session, user_a: User, user_b: User) -> dict:
    skills_a = get_skill_intelligence(db, user_a.id, user_a.rating)
    skills_b = get_skill_intelligence(db, user_b.id, user_b.rating)
    by_a = {s["tag"]: s for s in skills_a}
    by_b = {s["tag"]: s for s in skills_b}

    a_wins = b_wins = 0
    topic_comparison = []
    for tag in sorted(set(by_a) | set(by_b)):
        sa, sb = by_a.get(tag), by_b.get(tag)
        ma = sa["mastery_score"] if sa else 0.0
        mb = sb["mastery_score"] if sb else 0.0
        if abs(ma - mb) < 3:
            stronger = "tie"
        elif ma > mb:
            stronger = user_a.handle
            a_wins += 1
        else:
            stronger = user_b.handle
            b_wins += 1
        topic_comparison.append({"tag": tag, "mastery_a": ma, "mastery_b": mb, "stronger": stronger})

    consistency_a = get_consistency(db, user_a.id)
    consistency_b = get_consistency(db, user_b.id)

    rating_timeline = _aligned_rating_timeline(db, user_a.id, user_b.id)

    summary = [
        f"{user_a.handle} ({user_a.rating}) vs {user_b.handle} ({user_b.rating}) — "
        f"rating gap {abs(user_a.rating - user_b.rating)}.",
        f"Topic strength: {user_a.handle} stronger in {a_wins} topics, "
        f"{user_b.handle} stronger in {b_wins} topics ({len(topic_comparison) - a_wins - b_wins} ties).",
        f"Consistency: {user_a.handle} is '{consistency_a['status']}' "
        f"(streak {consistency_a['current_streak_weeks']}w); {user_b.handle} is '{consistency_b['status']}' "
        f"(streak {consistency_b['current_streak_weeks']}w).",
    ]

    return {
        "user_a": {"handle": user_a.handle, "rating": user_a.rating, "rank": user_a.rank},
        "user_b": {"handle": user_b.handle, "rating": user_b.rating, "rank": user_b.rank},
        "topic_comparison": topic_comparison,
        "difficulty_bands_a": get_difficulty_bands(db, user_a.id),
        "difficulty_bands_b": get_difficulty_bands(db, user_b.id),
        "consistency_a": consistency_a,
        "consistency_b": consistency_b,
        "rating_timeline": rating_timeline,
        "summary": summary,
    }


def _aligned_rating_timeline(db: Session, user_a_id: int, user_b_id: int) -> list[dict]:
    """
    Builds ONE timeline keyed by the union of both users' contests (sorted
    chronologically), rather than two separate per-user series plotted against
    their own contest index -- that previous approach made it impossible to
    tell who did better in any specific contest since the x-axes didn't align.

    Each entry forward-fills each user's most recent known rating into contests
    they didn't personally compete in, so both lines stay continuous and
    comparable at every x position. `competed_a`/`competed_b` flag which points
    are real data vs. carried forward, so the frontend can draw a solid marker
    only where that user actually competed.
    """
    rows_a = (db.query(RatingChange).filter(RatingChange.user_id == user_a_id)
              .order_by(RatingChange.timestamp).all())
    rows_b = (db.query(RatingChange).filter(RatingChange.user_id == user_b_id)
              .order_by(RatingChange.timestamp).all())

    by_contest: dict = {}
    for r in rows_a:
        e = by_contest.setdefault(r.contest_id, {"contest": r.contest_name, "timestamp": r.timestamp,
                                                   "rating_a": None, "rating_b": None})
        e["rating_a"] = r.new_rating
        e["timestamp"] = r.timestamp
        e["contest"] = r.contest_name
    for r in rows_b:
        e = by_contest.setdefault(r.contest_id, {"contest": r.contest_name, "timestamp": r.timestamp,
                                                   "rating_a": None, "rating_b": None})
        e["rating_b"] = r.new_rating
        if e.get("rating_a") is None:
            e["timestamp"] = e.get("timestamp") or r.timestamp
            e.setdefault("contest", r.contest_name)

    timeline = sorted(by_contest.values(), key=lambda x: x["timestamp"] or 0)

    last_a = last_b = None
    result = []
    for entry in timeline:
        competed_a = entry["rating_a"] is not None
        competed_b = entry["rating_b"] is not None
        if competed_a:
            last_a = entry["rating_a"]
        if competed_b:
            last_b = entry["rating_b"]
        result.append({
            "contest": entry["contest"],
            "timestamp": entry["timestamp"],
            "rating_a": last_a,
            "rating_b": last_b,
            "competed_a": competed_a,
            "competed_b": competed_b,
        })
    return result
