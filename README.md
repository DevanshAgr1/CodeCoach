# CodeCoach

**A Codeforces analytics platform that turns raw submission history into an explainable training plan.**

CodeCoach answers five questions every competitive programmer actually asks:

1. **What am I weak at?** — per-topic mastery/weakness scoring, not guesswork
2. **What should I solve next?** — a scored, explainable recommendation engine (no randomness)
3. **Am I improving or stalling?** — activity timelines + inactivity detection
4. **How do I compare against others?** — head-to-head comparison mode between two handles
5. **What's my path to the next rating milestone?** — a "Road to Specialist / Expert" roadmap engine

---

## Why this exists

Most Codeforces dashboards show you a rating graph and call it analytics. CodeCoach is built around one rule: **every recommendation has to say why it was picked** — which weak topic it targets, what difficulty band it's in, and whether your practice on that topic has been falling off. If a suggestion can't be explained in one sentence, it shouldn't be a suggestion.

## Features

### Skill Intelligence Engine
Per-topic breakdown of attempted / solved / acceptance % / average solved & failed difficulty / a 0–100 mastery score / a 0–100 weakness score / a recent activity trend (`rising`, `falling`, `steady`, `inactive`, `untested`).

### Topic Activity Timeline
Monthly attempts-vs-solves trend, overall or scoped to a single topic — surfaces topics you've quietly stopped practicing.

### Recommendation Engine v2
Every candidate problem is scored from four transparent signals (weak-topic match, difficulty fit, activity-trend urgency, unfinished-attempt bonus) — **zero randomness**. Each result ships with a generated explanation, e.g.:

> *Targets 'graphs', your weakest matched topic (mastery 12/100, 17% acceptance). Practice on 'graphs' has gone falling recently. Rated 1400, inside your Growth band (1200–1600) for a 1350 rating.*

### Roadmap Engine
Maps your current rating to the next Codeforces rank tier, calculates the rating gap, and generates a weekly practice plan (maintenance / growth / intensive) plus 10 scored problems targeting your weakest topics.

### Comparison Mode
Head-to-head analysis between two handles: topic-by-topic mastery comparison, rating progression overlay, and consistency comparison.

### Dashboard
Clean single-page UI: skill table, difficulty-band chart, activity timeline, rating chart, recommendation cards, roadmap panel, and comparison panel.

---

## How it works (data pipeline)

```
Codeforces API ──▶ ingest ──▶ SQLite ──▶ analytics engine ──▶ dashboard
  user.info          │                       │
  user.status        │            skill intelligence
  user.rating         │            recommendation scoring
  problemset.problems │            roadmap / comparison
```

Every submission verdict (not just accepted ones) is stored, because acceptance %, attempted-vs-solved, and average-failed-difficulty are mathematically impossible to compute if failed attempts are discarded.

---

## Tech stack

- **Backend:** FastAPI + SQLAlchemy + SQLite
- **Frontend:** Vanilla HTML/CSS/JS + Chart.js (no build step, no framework)
- **Data source:** [Codeforces public API](https://codeforces.com/apiHelp)

---

## How to run

```bash
git clone <your-repo-url>
cd codecoach
pip install -r requirements.txt
python run.py
```

Then open **http://localhost:8000** and enter any Codeforces handle (try `tourist`, `Petr`, or your own).

First load takes a few seconds — CodeCoach pulls the full Codeforces problem set (~10k problems) once and caches it in `backend/codecoach.db`, then fetches the handle's submissions and rated-contest history.

> Requires Python 3.10+ and an internet connection (to reach the Codeforces API).

## How to use

- **Load Profile** — enter a handle and hit Analyze. This builds your full skill profile.
- **Skill Intelligence** — see exactly which topics are dragging your acceptance rate down.
- **Activity Timeline** — pick a topic from the dropdown to see if you're still practicing it.
- **Recommendations** — filter by topic/difficulty, or leave it on Auto to get a blended set scored against your actual weak spots.
- **Roadmap** — see your gap to the next rank and a generated weekly plan.
- **Compare Handles** — pit your profile against a friend's or a rival's.

---

## Notes & limitations

- **Removed the original ML-based difficulty predictor.** It was dead code (never wired to a route) and added no real signal over a transparent formula. An explainable weighted score is a better fit for a product whose core promise is "every recommendation says why."
- **Mastery scoring rewards solving above your own rating**, which means a topic solved entirely on easy problems will show moderate (not maximal) mastery even at 100% acceptance — this is intentional, but worth knowing if a number looks lower than expected.
- **Comparison mode auto-ingests** any handle that hasn't been analyzed yet, so the first comparison involving a new handle takes a few extra seconds.
- Designed for one local user at a time (SQLite, no auth) — fine for personal use or a portfolio demo, not multi-tenant production use as-is.

---

## Resume bullet points

- Built a full-stack competitive-programming analytics platform (FastAPI/SQLAlchemy/SQLite + vanilla JS/Chart.js) that ingests and normalizes Codeforces submission and contest-rating data for arbitrary user handles.
- Designed an explainable, weighted scoring engine for problem recommendations — replacing a random-selection baseline — using per-topic mastery/weakness metrics derived from acceptance rate, solve volume, and difficulty-relative-to-rating.
- Implemented a topic-level skill intelligence system computing acceptance %, average solved/failed difficulty, and 30-day activity trend across ~35 Codeforces problem tags via aggregated SQL.
- Built a rank-progression roadmap engine that maps live rating data to Codeforces tier thresholds and generates a personalized weekly practice plan.
- Shipped a head-to-head comparison feature contrasting two users' topic mastery, rating progression, and contest consistency.

## Demo script (60 seconds)

1. "This is CodeCoach — it turns your Codeforces history into an actual training plan, not just a rating graph." *(load your own handle)*
2. "Here's my skill intelligence — it's telling me graphs is my weakest topic at 17% acceptance, and that I've been slacking on it for three weeks." *(point at skill table + trend badge)*
3. "So when I ask for recommendations, every single one tells me exactly why it was picked." *(open a rec card, read the reason aloud)*
4. "And this roadmap shows my actual gap to Specialist, with a weekly plan generated from that gap." *(scroll to roadmap)*
5. "Last thing — I can compare myself against a friend, topic by topic." *(run a quick comparison)*

## Interview Q&A

**1. Why did you remove the scikit-learn model that was in the original codebase?**
It was dead code — the route that called it was never registered — and even working, a single-feature decision tree predicting "will this rating be solved" added no signal a weighted formula couldn't give more transparently. Given the product's core requirement was explainability, a formula I can fully justify in one sentence beats an ML model I'd have to hand-wave around.

**2. Walk me through how a recommendation gets its score.**
Four components: weakness_score summed across any of the problem's tags that are genuinely weak for that user (≥50/100), a difficulty-fit bonus based on distance from the band's center rating, a trend bonus if a matched weak topic's practice has gone stale, and a small bonus for problems already attempted but unsolved. The same four signals are turned directly into the reason string, so the score and the explanation can't drift apart.

**3. Why store every submission verdict instead of just accepted ones?**
Acceptance %, attempted-vs-solved, and average-failed-difficulty are all derived from comparing solved problems against the full attempt pool. Discarding non-OK verdicts at ingestion — which the original code did — makes those metrics mathematically impossible to compute later; the fix has to happen at the data layer, not the analytics layer.

**4. How does the mastery score handle a user who's only ever solved very easy problems in a topic?**
It deliberately caps the difficulty-edge bonus so solving far below your own rating yields moderate mastery even at 100% acceptance — high accuracy at low difficulty isn't strong evidence of mastery at your *current* level. It's a tradeoff I'd tune further with real user feedback, but it's documented in code rather than hidden.

**5. How do you avoid recommending problems that are impossible for the candidate pool to be empty?**
Difficulty bands are computed relative to the user's current rating with a fixed floor of 800 (CF's minimum rated value), and the auto mode blends three overlapping bands so a single narrow filter can't starve the result set. If a manual tag+difficulty combination genuinely returns nothing, the UI shows an explicit empty state rather than silently failing.

**6. Why SQLite instead of Postgres for a project like this?**
Zero setup for a single-user local tool — the entire point was a `git clone && pip install && run` experience. SQLAlchemy's ORM layer means swapping the connection string for Postgres is a one-line change if this needed to support concurrent users later.

**7. How does the roadmap engine decide the weekly practice intensity?**
It's a simple rule based on the rating gap to the next tier threshold: ≤100 gap is "maintenance," ≤300 is "growth," and beyond that is "intensive," each mapped to a specific problems-per-week and contest-frequency recommendation. It's intentionally simple and tunable rather than a black box.

**8. What was the most important bug you found in the original code, and why?**
The submission ingestion only stored `verdict == "OK"` rows, silently discarding every failed attempt. It looked like a minor filter but it's actually the root cause that made half the requested features — acceptance %, attempted-vs-solved, failed-difficulty — structurally impossible, regardless of how good the analytics code on top of it was.

**9. How would you scale this to support multiple concurrent users comfortably?**
Move from SQLite to Postgres, add a job queue (e.g. Celery/RQ) for the Codeforces ingestion calls so they don't block request threads, and cache the global problem set (which changes rarely) separately from per-user data so re-ingestion doesn't repeatedly hit `problemset.problems`.

**10. What would you build next if you had another week?**
Persisted per-user goal tracking (so the roadmap reflects actual progress over time instead of just current-state), and contest-day-of recommendations that weight recently-added problems and topic-frequency in upcoming Div rounds, since "what should I drill right before a contest" is a different question from general practice.
