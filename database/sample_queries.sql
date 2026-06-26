-- ═══════════════════════════════════════════════════════════
--  CodeCoach — Sample SQL Queries Reference (v2 schema)
--  These mirror the live queries in backend/crud.py, with a literal
--  user_id substituted in for readability. Replace `1` with a real user id.
-- ═══════════════════════════════════════════════════════════

-- 1. Skill Intelligence: attempted, solved, acceptance %, avg solved/failed
--    difficulty, and 30-day activity trend per topic. This is the core query
--    behind the whole product -- everything else (weak/strong topics, the
--    recommendation engine, the roadmap) is built on top of this.
SELECT
    t.tag_name,
    COUNT(DISTINCT CASE WHEN s.id IS NOT NULL THEN s.problem_id END)         AS attempted,
    COUNT(DISTINCT CASE WHEN s.verdict = 'OK' THEN s.problem_id END)        AS solved,
    AVG(CASE WHEN s.verdict = 'OK' THEN p.rating END)                       AS avg_solved_rating,
    AVG(CASE WHEN s.verdict != 'OK' AND s.id IS NOT NULL
             AND p.id NOT IN (
                 SELECT problem_id FROM submissions WHERE user_id = 1 AND verdict = 'OK'
             )
             THEN p.rating END)                                             AS avg_failed_rating
FROM tags t
JOIN problem_tags pt ON pt.tag_id = t.id
JOIN problems p      ON p.id = pt.problem_id
LEFT JOIN submissions s
       ON s.problem_id = p.id AND s.user_id = 1
GROUP BY t.tag_name;


-- 2. Difficulty-band breakdown: how reliably does this user solve problems
--    at each rating tier? (Distinct from per-topic analysis -- this is purely
--    about difficulty, regardless of subject.)
SELECT
    (p.rating / 100) * 100 AS rating_bucket,
    COUNT(DISTINCT CASE WHEN s.id IS NOT NULL THEN s.problem_id END) AS attempted,
    COUNT(DISTINCT CASE WHEN s.verdict = 'OK' THEN s.problem_id END) AS solved
FROM problems p
LEFT JOIN submissions s ON s.problem_id = p.id AND s.user_id = 1
WHERE p.rating > 0
GROUP BY rating_bucket
ORDER BY rating_bucket;


-- 3. Monthly activity timeline (attempts vs. solves), used for the topic
--    activity chart and inactivity detection.
SELECT
    strftime('%Y-%m', datetime(timestamp, 'unixepoch')) AS month,
    COUNT(*)                                              AS attempts,
    SUM(CASE WHEN verdict = 'OK' THEN 1 ELSE 0 END)       AS solves
FROM submissions
WHERE user_id = 1
GROUP BY month
ORDER BY month;


-- 4. Recommendation candidate pool: unsolved problems in a target rating
--    range, with their full tag list and whether the user has attempted
--    (but not solved) them before. Scoring against weak topics happens in
--    Python (see crud._score_band) once these rows come back.
SELECT
    p.id, p.contest_id, p."index", p.name, p.rating,
    GROUP_CONCAT(DISTINCT t2.tag_name) AS tags,
    (SELECT COUNT(*) FROM submissions WHERE problem_id = p.id AND user_id = 1) AS attempts_on_problem
FROM problems p
JOIN problem_tags pt2 ON pt2.problem_id = p.id
JOIN tags t2          ON t2.id = pt2.tag_id
WHERE p.rating BETWEEN 1200 AND 1500
  AND p.id NOT IN (
      SELECT problem_id FROM submissions WHERE user_id = 1 AND verdict = 'OK'
  )
GROUP BY p.id
ORDER BY p.rating ASC
LIMIT 20;


-- 5. Rating progression (contest-by-contest), the source data for the
--    rating chart and the roadmap's "gap to next milestone" calculation.
SELECT contest_name, old_rating, new_rating, timestamp
FROM rating_changes
WHERE user_id = 1
ORDER BY timestamp;


-- 6. Total solved count for a user.
SELECT COUNT(DISTINCT problem_id) AS total_solved
FROM submissions
WHERE user_id = 1 AND verdict = 'OK';


-- 7. Most recently solved problems.
SELECT p.name, p.rating, s.timestamp
FROM submissions s
JOIN problems p ON p.id = s.problem_id
WHERE s.user_id = 1 AND s.verdict = 'OK'
ORDER BY s.timestamp DESC
LIMIT 10;
