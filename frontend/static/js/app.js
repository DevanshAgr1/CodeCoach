/* ═══════════════════════════════════════════════════
   CodeCoach — Frontend App (v2)
═══════════════════════════════════════════════════ */

const API = "";

let currentHandle = "";
let currentRating = 1200;
let lastSkills = [];

let bandsChart, timelineChart, ratingChart, compareRatingChart, compareDifficultyChart;

// Mouse-wheel/pinch zoom + drag-to-pan on the X axis, with a "Reset Zoom" button
// per chart. Only applied to the line charts (timeline/rating/compare) -- the
// difficulty-band bar chart uses categorical labels, which this plugin doesn't
// meaningfully zoom.
const ZOOM_PLUGIN_OPTS = {
  zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" },
  pan: { enabled: true, mode: "x" },
};

// ── Helpers ────────────────────────────────────────────────────────────────

function setStatus(msg, type = "loading", elId = "status-msg") {
  const el = document.getElementById(elId);
  el.textContent = msg;
  el.className = `status-msg ${type}`;
  el.classList.remove("hidden");
}

function showPanel(id) { document.getElementById(id).classList.remove("hidden"); }

function escHtml(str = "") {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function destroyChart(ref) { if (ref) ref.destroy(); }

// ── Load Profile ─────────────────────────────────────────────────────────────

async function loadProfile() {
  const handle = document.getElementById("cf-handle").value.trim();
  if (!handle) { setStatus("Please enter a Codeforces handle.", "error"); return; }

  currentHandle = handle;
  const btn = document.getElementById("btn-load");
  btn.disabled = true;
  btn.querySelector(".btn-label").textContent = "LOADING…";
  setStatus("⏳ Fetching profile, submissions, and rating history from Codeforces…", "loading");

  try {
    const res = await fetch(`${API}/api/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handle })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Unknown error");

    currentRating = data.rating || 1200;

    document.getElementById("uc-handle").textContent = "@" + data.handle;
    const compareAInput = document.getElementById("compare-a");
    if (compareAInput && !compareAInput.value) compareAInput.value = data.handle;
    document.getElementById("uc-rating").textContent = data.rating || "Unrated";
    document.getElementById("uc-max-rating").textContent = data.max_rating || "—";
    document.getElementById("uc-rank").textContent = data.rank || "—";
    document.getElementById("user-card").classList.remove("hidden");

    setStatus("✓ Profile loaded! Building analytics…", "success");

    await loadSkills();
    showPanel("panel-skills");
    showPanel("panel-timeline");
    showPanel("panel-rating");
    showPanel("panel-roadmap");

    await Promise.all([loadTimeline(), loadRatingChart(), loadRoadmap()]);

    setStatus(`✓ Done! ${data.handle} is ready.`, "success");
  } catch (e) {
    setStatus("✗ Error: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.querySelector(".btn-label").textContent = "ANALYZE";
  }
}

// ── Skill Intelligence ────────────────────────────────────────────────────────

async function loadSkills() {
  const res = await fetch(`${API}/api/skills/${currentHandle}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail);

  lastSkills = data.skills || [];

  renderTopicList("weak-list", data.weak_topics, "None identified yet");
  renderTopicList("untested-list", data.untested_topics, "You've touched every tracked topic at least once.");
  renderTopicList("strong-list", data.strong_topics, "Solve more problems to surface strengths!");
  renderSkillTable(lastSkills);
  populateTimelineTagOptions(lastSkills);
  await loadDifficultyBands();
}

function renderTopicList(elId, topics, emptyMsg) {
  const list = document.getElementById(elId);
  list.innerHTML = "";
  if (!topics || topics.length === 0) {
    list.innerHTML = `<li style="color:var(--text-dim)">${emptyMsg}</li>`;
    return;
  }
  topics.forEach(t => {
    const li = document.createElement("li");
    const detail = t.attempted > 0 ? `${t.solved}/${t.attempted} solved` : "—";
    li.innerHTML = `<span class="tag-name">${escHtml(t.tag)}</span><span class="tag-count">${detail}</span>`;
    list.appendChild(li);
  });
}

function renderSkillTable(skills) {
  const body = document.getElementById("skill-table-body");
  body.innerHTML = "";
  const sorted = [...skills].sort((a, b) => b.attempted - a.attempted);
  sorted.forEach(s => {
    const tr = document.createElement("tr");
    const acc = s.acceptance_pct != null ? s.acceptance_pct + "%" : "—";
    tr.innerHTML = `
      <td>${escHtml(s.tag)}</td>
      <td>${s.attempted}</td>
      <td>${s.solved}</td>
      <td>${acc}</td>
      <td>${s.mastery_score}/100</td>
      <td><span class="trend-badge trend-${s.trend}">${s.trend}</span></td>
    `;
    body.appendChild(tr);
  });
}

function populateTimelineTagOptions(skills) {
  const sel = document.getElementById("timeline-tag");
  const current = sel.value;
  sel.innerHTML = '<option value="">All Topics</option>';
  [...skills]
    .filter(s => s.attempted > 0)
    .sort((a, b) => b.attempted - a.attempted)
    .forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.tag;
      opt.textContent = `${s.tag} (${s.attempted})`;
      sel.appendChild(opt);
    });
  sel.value = current || "";
}

async function loadDifficultyBands() {
  const res = await fetch(`${API}/api/difficulty-bands/${currentHandle}`);
  const data = await res.json();
  const bands = (data.bands || []).filter(b => b.attempted > 0);

  destroyChart(bandsChart);
  const ctx = document.getElementById("chart-bands").getContext("2d");
  bandsChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: bands.map(b => b.rating),
      datasets: [
        { label: "Attempted", data: bands.map(b => b.attempted), backgroundColor: "#cbd5e1" },
        { label: "Solved", data: bands.map(b => b.solved), backgroundColor: "#3b82f6" },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 } },
        x: { title: { display: true, text: "Problem Rating" } }
      }
    }
  });
}

// ── Topic Activity Timeline ───────────────────────────────────────────────────

async function loadTimeline() {
  const tag = document.getElementById("timeline-tag").value;
  const url = tag
    ? `${API}/api/timeline/${currentHandle}?tag=${encodeURIComponent(tag)}`
    : `${API}/api/timeline/${currentHandle}`;
  const res = await fetch(url);
  const data = await res.json();
  const timeline = data.timeline || [];

  const emptyEl = document.getElementById("timeline-empty");
  const canvas = document.getElementById("chart-timeline");

  if (timeline.length === 0) {
    emptyEl.classList.remove("hidden");
    canvas.classList.add("hidden");
    return;
  }
  emptyEl.classList.add("hidden");
  canvas.classList.remove("hidden");

  destroyChart(timelineChart);
  const ctx = canvas.getContext("2d");
  timelineChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: timeline.map(t => t.month),
      datasets: [
        { label: "Attempts", data: timeline.map(t => t.attempts), borderColor: "#94a3b8", backgroundColor: "rgba(148,163,184,0.15)", tension: 0.3, fill: true },
        { label: "Solves", data: timeline.map(t => t.solves), borderColor: "#3b82f6", backgroundColor: "rgba(59,130,246,0.15)", tension: 0.3, fill: true },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      plugins: { zoom: ZOOM_PLUGIN_OPTS }
    }
  });
}

// ── Rating Progression ────────────────────────────────────────────────────────

async function loadRatingChart() {
  const res = await fetch(`${API}/api/rating-history/${currentHandle}`);
  const data = await res.json();
  const history = data.history || [];

  const emptyEl = document.getElementById("rating-empty");
  const canvas = document.getElementById("chart-rating");

  if (history.length === 0) {
    emptyEl.classList.remove("hidden");
    canvas.classList.add("hidden");
    return;
  }
  emptyEl.classList.add("hidden");
  canvas.classList.remove("hidden");

  destroyChart(ratingChart);
  const ctx = canvas.getContext("2d");
  ratingChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: history.map(h => new Date(h.timestamp * 1000).toLocaleDateString()),
      datasets: [{
        label: "Rating", data: history.map(h => h.new_rating),
        borderColor: "#3b82f6", backgroundColor: "rgba(59,130,246,0.1)",
        tension: 0.25, fill: true, pointRadius: 3,
      }]
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { zoom: ZOOM_PLUGIN_OPTS } }
  });
}

// ── Shared problem-card renderer (used by Roadmap) ────────────────────────────

function buildProblemCard(p) {
  return `
    <a class="rec-card" href="${p.url || "#"}" target="_blank" rel="noopener">
      <div class="rec-rating">${p.rating ?? "?"}</div>
      <div class="rec-info">
        <div class="rec-name">${escHtml(p.name || "Unknown")}</div>
        <div class="rec-tags">${escHtml(p.tags || "")}</div>
        <div class="rec-reason">${escHtml(p.reason || "")}</div>
      </div>
      <div class="rec-link">SOLVE →</div>
    </a>
  `;
}

// ── Roadmap ───────────────────────────────────────────────────────────────────

async function loadRoadmap() {
  const res = await fetch(`${API}/api/roadmap/${currentHandle}`);
  const data = await res.json();

  document.getElementById("roadmap-title").textContent = data.roadmap_title;
  document.getElementById("roadmap-gap").textContent = data.next_milestone_rating
    ? `${data.current_rating} → ${data.next_milestone_rating} (gap ${data.rating_gap})`
    : `Current peak tier: ${data.current_rank}`;

  const pct = data.progress_pct ?? 0;
  document.getElementById("roadmap-bar").style.width = pct + "%";

  document.getElementById("roadmap-plan").textContent = data.weekly_plan;

  const focusList = document.getElementById("roadmap-focus");
  focusList.innerHTML = "";
  (data.focus_topics || []).forEach(t => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escHtml(t.tag)}</span><span>weakness ${t.weakness_score}/100</span>`;
    focusList.appendChild(li);
  });

  const recList = document.getElementById("roadmap-rec-list");
  const problems = data.next_problems || [];
  recList.innerHTML = problems.length
    ? problems.map(buildProblemCard).join("")
    : '<div class="empty-state">No candidates found yet — solve a few more problems first.</div>';
}

document.getElementById("timeline-tag")?.addEventListener("change", loadTimeline);

// ── Comparison Mode ────────────────────────────────────────────────────────────

async function runComparison() {
  const a = document.getElementById("compare-a").value.trim();
  const b = document.getElementById("compare-b").value.trim();
  if (!a || !b) { setStatus("Enter both handles to compare.", "error", "compare-status"); return; }

  setStatus(`⏳ Comparing ${a} vs ${b}… (auto-loading any handle not already analyzed)`, "loading", "compare-status");
  document.getElementById("compare-results").classList.add("hidden");

  try {
    const res = await fetch(`${API}/api/compare/${encodeURIComponent(a)}/${encodeURIComponent(b)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Comparison failed");

    setStatus(`✓ Comparison ready.`, "success", "compare-status");
    document.getElementById("compare-results").classList.remove("hidden");

    // Summary
    const summaryEl = document.getElementById("compare-summary");
    summaryEl.innerHTML = "";
    (data.summary || []).forEach(line => {
      const li = document.createElement("li");
      li.textContent = line;
      summaryEl.appendChild(li);
    });

    // Rating progression on a SHARED timeline: x-axis is the union of every
    // contest either user competed in (chronological), y-axis is rating, so
    // both lines are directly comparable at the same x position. Points are
    // only drawn solid where that user actually competed in that contest --
    // the connecting line between is forward-filled from their last known
    // rating so the chart stays continuous even when only one of them
    // entered a given contest.
    const timeline = data.rating_timeline || [];
    const emptyEl = document.getElementById("compare-rating-empty");
    const canvas = document.getElementById("chart-compare-rating");

    destroyChart(compareRatingChart);

    if (timeline.length === 0) {
      emptyEl.classList.remove("hidden");
      canvas.classList.add("hidden");
    } else {
      emptyEl.classList.add("hidden");
      canvas.classList.remove("hidden");

      const ctx = canvas.getContext("2d");
      compareRatingChart = new Chart(ctx, {
        type: "line",
        data: {
          labels: timeline.map(t => t.contest),
          datasets: [
            {
              label: data.user_a.handle,
              data: timeline.map(t => t.rating_a),
              borderColor: "#3b82f6", backgroundColor: "rgba(59,130,246,0.1)", tension: 0.2,
              spanGaps: true,
              pointRadius: timeline.map(t => t.competed_a ? 4 : 0),
              pointBackgroundColor: "#3b82f6",
            },
            {
              label: data.user_b.handle,
              data: timeline.map(t => t.rating_b),
              borderColor: "#f97316", backgroundColor: "rgba(249,115,22,0.1)", tension: 0.2,
              spanGaps: true,
              pointRadius: timeline.map(t => t.competed_b ? 4 : 0),
              pointBackgroundColor: "#f97316",
            },
          ]
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { zoom: ZOOM_PLUGIN_OPTS },
          scales: { x: { ticks: { maxRotation: 45, minRotation: 0 } } }
        }
      });
    }

    // Solve distribution by difficulty -- this data (difficulty_bands_a/b) was
    // already being computed by the backend on every comparison call but never
    // rendered. Union of every rating either user has attempted, solved-count
    // per user at each rating, so you can see who's stronger at which difficulty.
    destroyChart(compareDifficultyChart);
    const bandsA = data.difficulty_bands_a || [];
    const bandsB = data.difficulty_bands_b || [];
    const ratingUnion = [...new Set([...bandsA.map(b => b.rating), ...bandsB.map(b => b.rating)])]
      .sort((x, y) => x - y);
    const solvedByRatingA = Object.fromEntries(bandsA.map(b => [b.rating, b.solved]));
    const solvedByRatingB = Object.fromEntries(bandsB.map(b => [b.rating, b.solved]));

    const diffCtx = document.getElementById("chart-compare-difficulty").getContext("2d");
    compareDifficultyChart = new Chart(diffCtx, {
      type: "bar",
      data: {
        labels: ratingUnion,
        datasets: [
          { label: data.user_a.handle, data: ratingUnion.map(r => solvedByRatingA[r] || 0), backgroundColor: "#3b82f6" },
          { label: data.user_b.handle, data: ratingUnion.map(r => solvedByRatingB[r] || 0), backgroundColor: "#f97316" },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          y: { beginAtZero: true, ticks: { precision: 0 } },
          x: { title: { display: true, text: "Problem Rating" } }
        }
      }
    });

    // Topic mastery table
    document.getElementById("compare-th-a").textContent = data.user_a.handle;
    document.getElementById("compare-th-b").textContent = data.user_b.handle;
    const body = document.getElementById("compare-table-body");
    body.innerHTML = "";
    (data.topic_comparison || [])
      .filter(t => t.mastery_a > 0 || t.mastery_b > 0)
      .sort((x, y) => Math.abs(y.mastery_a - y.mastery_b) - Math.abs(x.mastery_a - x.mastery_b))
      .forEach(t => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${escHtml(t.tag)}</td>
          <td>${t.mastery_a}</td>
          <td>${t.mastery_b}</td>
          <td>${escHtml(t.stronger)}</td>
        `;
        body.appendChild(tr);
      });
  } catch (e) {
    setStatus("✗ " + e.message, "error", "compare-status");
  }
}
