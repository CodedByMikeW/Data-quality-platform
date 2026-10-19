// Belt-and-suspenders alongside the inline scrollRestoration fix in
// index.html's <head> - guarantees a fresh load always starts at the top.
window.scrollTo(0, 0);

const CHECK_META = {
  missing_values: {
    label: "Missing Values",
    code: "MV",
    description: "Flags any row with a blank or null cell in any column.",
  },
  duplicates: {
    label: "Duplicates",
    code: "DU",
    description: "Flags rows that are exact repeats of another row.",
  },
  invalid_formats: {
    label: "Invalid Formats",
    code: "IF",
    description: "Flags values that break the format the rest of their column follows — e.g. a malformed email or an ID that doesn't match the usual pattern.",
  },
  schema_changes: {
    label: "Schema Changes",
    code: "SC",
    description: "Flags columns that were added, removed, or changed type since the last run.",
  },
  outliers: {
    label: "Outliers",
    code: "OL",
    description: "Flags numeric values far outside a column's typical range, based on that column's own data.",
  },
  drift: {
    label: "Data Drift",
    code: "DR",
    description: "Flags when a column's values have shifted meaningfully from an earlier baseline.",
  },
  broken_relationships: {
    label: "Broken Relationships",
    code: "BR",
    description: "Flags references to IDs that don't exist in the related dataset.",
  },
  unexpected_values: {
    label: "Unexpected Values",
    code: "UV",
    description: "Flags values in a column that fall outside the set of values normally seen there.",
  },
};

const TOTAL_PLANNED_WEIGHT = 100; // weights across all 8 planned checks sum to 100
const CERTIFIED_THRESHOLD = 90; // arbitrary demo threshold - tune as you like

let trendChart = null;

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

// For a value inside a quoted HTML attribute. escapeHtml is for text and leaves
// quotes alone, so a filename like  a" onmouseover="...  would end the attribute early.
function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function formatTimestamp(iso, short = false) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return short
    ? d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const HERO_QUOTES = [
  "A documented quality score, and the exact rows behind every issue — down to the row.",
  "Eight checks, one weighted score — missing values, duplicates, drift, and more, in a single number.",
  "Every score traces back to a formula you can read. No black box, no guesswork.",
  "Catch broken relationships between datasets before they break your reports.",
  "From a single CSV to a full quality history — track your score, run after run.",
  "Schema changes, data drift, outliers — caught automatically, explained clearly.",
];

// Rotates the hero subtitle through a few different pitches every 5s, with
// a soft fade/slide so the swap reads as motion, not a jarring text jump.
// Skipped under reduced motion by swapping instantly instead of fading.
function initHeroQuoteRotator() {
  const el = document.getElementById("hero-copy");
  if (!el || HERO_QUOTES.length < 2) return;

  let index = 0;

  setInterval(() => {
    index = (index + 1) % HERO_QUOTES.length;

    if (prefersReducedMotion()) {
      el.textContent = HERO_QUOTES[index];
      return;
    }

    el.classList.add("is-swapping");
    setTimeout(() => {
      el.textContent = HERO_QUOTES[index];
      el.classList.remove("is-swapping");
    }, 350);
  }, 5000);
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Counts the gauge number up from 0 instead of just appearing at its final
// value - a small touch, but it's the difference between a dashboard that
// feels static and one that feels alive. Skipped entirely under reduced
// motion, where the number just appears at its target value immediately.
function animateGaugeNumber(el, target) {
  if (prefersReducedMotion()) {
    el.textContent = target.toFixed(1);
    return;
  }

  const duration = 900;
  const start = performance.now();

  function tick(now) {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = (target * eased).toFixed(1);
    if (progress < 1) requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

function renderScorecardSkeleton() {
  document.getElementById("scorecard").innerHTML = `
    <div class="skeleton-scorecard">
      <div class="skeleton skeleton-circle"></div>
      <div class="skeleton-lines">
        <div class="skeleton skeleton-line" style="width:40%"></div>
        <div class="skeleton skeleton-line" style="width:70%"></div>
        <div class="skeleton skeleton-line" style="width:55%"></div>
        <div class="skeleton skeleton-line" style="width:30%"></div>
      </div>
    </div>`;
}

function renderChecksGridSkeleton() {
  const card = `
    <div class="skeleton-check-card">
      <div class="skeleton skeleton-badge"></div>
      <div class="skeleton skeleton-line" style="width:65%"></div>
      <div class="skeleton skeleton-line" style="width:90%"></div>
      <div class="skeleton skeleton-line" style="width:40%;height:24px"></div>
    </div>`;
  document.getElementById("checks-grid").innerHTML =
    `<div class="skeleton-checks-grid">${card.repeat(4)}</div>`;
}

// Fades/slides sections up as they scroll into view - purely a "product,
// not a static page" polish touch, so it's skipped entirely (elements just
// appear at full opacity) under reduced motion.
function initScrollReveal() {
  const targets = document.querySelectorAll(".reveal");
  if (!targets.length) return;

  if (prefersReducedMotion()) {
    targets.forEach((t) => t.classList.add("in-view"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("in-view");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15 }
  );

  targets.forEach((t) => observer.observe(t));
}

// The "Live demo" badge: the pulse line draws itself, then the badge beats like
// a heart and the arc around it spins. That starts once the header is actually
// on screen (not when its section first peeks in at the bottom edge) and runs
// for as long as it stays there - it is held (is-offscreen) while scrolled out
// of view so it isn't animating for nobody. Under reduced motion it never arms,
// so the line just sits there drawn and nothing moves.
function initDemoIntro() {
  const intro = document.querySelector(".demo-intro");
  if (!intro || prefersReducedMotion() || !("IntersectionObserver" in window)) return;

  intro.classList.add("is-armed");
  const observer = new IntersectionObserver(
    (entries) => {
      const entry = entries[entries.length - 1];
      intro.classList.toggle("is-offscreen", !entry.isIntersecting);
      if (entry.intersectionRatio >= 0.6) intro.classList.add("is-live");
    },
    { threshold: [0, 0.6] }
  );
  observer.observe(intro);
}

// The "How it works" icon and each of its four step cards are all shortcuts
// to the same place: the actual upload bar, since that's what every one of
// those steps is describing how to use.
function initScrollToUpload() {
  const target = document.getElementById("upload-bar");
  if (!target) return;

  document.querySelectorAll(".scroll-to-upload").forEach((el) => {
    el.addEventListener("click", () => {
      target.scrollIntoView({
        behavior: prefersReducedMotion() ? "auto" : "smooth",
        block: "center",
      });
    });
  });
}

// ---------- two pages, one document ----------
// The dashboard and the Production Incident Intelligence page are both in
// index.html. <html data-view="incidents"> shows the incident page and hides
// the dashboard (CSS does the swap), and the URL mirrors it as
// #incident-intelligence - so Back/Forward and refresh behave like real page
// navigation, all in the same tab with no reload and no new window. (The
// inline script in <head> sets data-view early on a refresh so the wrong
// page never flashes; this keeps it in sync from then on.)
const INCIDENT_PAGE_HASH = "#incident-intelligence";
const INCIDENT_PAGE_TITLE = "Production Incident Intelligence — Data Quality Platform";

function initPageViews() {
  const root = document.documentElement;
  const dashboardTitle = document.title;
  let dashboardScrollY = 0; // so "Back" lands exactly where the card was clicked

  const activeView = () => (root.dataset.view === "incidents" ? "incidents" : "main");
  const viewFromUrl = () => (location.hash === INCIDENT_PAGE_HASH ? "incidents" : "main");

  // html { scroll-behavior: smooth } would turn a plain scrollTo into a long
  // animated scroll across the page that was just swapped out; a page change
  // should land instantly, so that is spelled out on the call itself.
  function jumpTo(y) {
    window.scrollTo({ top: y, left: 0, behavior: "instant" });
  }

  function applyView(view, { initial = false } = {}) {
    // hashchange also fires for the dashboard's own "#live-demo"-style links,
    // which change the hash but not the view - nothing to do for those.
    if (!initial && view === activeView()) return;

    if (view === "incidents") {
      dashboardScrollY = window.scrollY;
      root.dataset.view = "incidents";
      document.title = INCIDENT_PAGE_TITLE;
      if (!initial) {
        jumpTo(0);
        document.getElementById("incident-page-title")?.focus({ preventScroll: true });
      }
    } else {
      root.dataset.view = "main";
      document.title = dashboardTitle;
      if (!initial) {
        jumpTo(dashboardScrollY);
        document.getElementById("open-incident-page-btn")?.focus({ preventScroll: true });
      }
    }
  }

  function openIncidentPage() {
    if (activeView() === "incidents") return;
    history.pushState({ fromDashboard: true }, "", INCIDENT_PAGE_HASH);
    applyView("incidents");
  }

  function closeIncidentPage() {
    if (activeView() === "main") return;
    if (history.state && history.state.fromDashboard) {
      history.back(); // the popstate handler below swaps the dashboard back in
    } else {
      // Landed here directly (a refresh or a shared link): there is no
      // dashboard entry behind this one to step back to, so swap in place.
      history.replaceState(null, "", location.pathname + location.search);
      applyView("main");
    }
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-view-link]");
    if (!link) return;
    // Cmd/Ctrl/Shift-click and middle-click keep their normal "open in a new tab" meaning.
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (link.dataset.viewLink === "incidents") openIncidentPage();
    else closeIncidentPage();
  });

  const syncToUrl = () => applyView(viewFromUrl());
  window.addEventListener("popstate", syncToUrl);
  window.addEventListener("hashchange", syncToUrl);

  applyView(viewFromUrl(), { initial: true });
}

function renderNoLabelPrompt() {
  document.getElementById("scorecard").innerHTML = `
    <div class="empty-state">
      <p class="eyebrow">No dataset selected</p>
      <h1>Give this dataset a label above to get started</h1>
      <p class="empty-copy">Type a name (try <code>customers</code> to see the seeded demo, or anything
      else to start a new one), choose a CSV, and run a check. Whatever you type becomes the
      saved label future runs and history are stored under.</p>
    </div>`;
  document.getElementById("checks-grid").innerHTML = "";
  renderTrendChart([]);
  updateCoverageNote(null);
}

function renderScorecard(run, datasetName) {
  const el = document.getElementById("scorecard");

  if (!run) {
    el.innerHTML = `
      <div class="empty-state">
        <p class="eyebrow">No inspection on file</p>
        <h1>No runs yet for <code>${escapeHtml(datasetName)}</code></h1>
        <p class="empty-copy">Choose a CSV above and run a check to generate the first report.</p>
      </div>`;
    return;
  }

  const score = run.score;
  const status = score >= CERTIFIED_THRESHOLD ? "pass" : "fail";
  const statusLabel = status === "pass" ? "CERTIFIED" : "NEEDS REVIEW";
  const circumference = 2 * Math.PI * 88;
  const offset = circumference * (1 - Math.min(Math.max(score, 0), 100) / 100);

  el.innerHTML = `
    <div class="scorecard-inner status-${status}">
      <div class="gauge-wrap">
        <svg class="gauge" viewBox="0 0 200 200" role="img" aria-label="Score ${score.toFixed(1)} out of 100">
          <circle class="gauge-track" cx="100" cy="100" r="88"></circle>
          <circle class="gauge-fill" cx="100" cy="100" r="88"
            style="stroke-dasharray:${circumference};stroke-dashoffset:${circumference}"></circle>
        </svg>
        <div class="gauge-readout">
          <span class="gauge-number">0.0</span>
          <span class="gauge-max">/ 100</span>
        </div>
      </div>
      <div class="scorecard-meta">
        <p class="eyebrow">Data Quality Score</p>
        <p class="dataset-line"><code>${escapeHtml(datasetName)}</code> &middot; run #${run.id} &middot; ${run.row_count.toLocaleString()} rows</p>
        <p class="timestamp">${formatTimestamp(run.created_at)}</p>
        <span class="stamp stamp-${status}">${statusLabel}</span>
      </div>
    </div>`;

  requestAnimationFrame(() => {
    const fillEl = el.querySelector(".gauge-fill");
    requestAnimationFrame(() => {
      fillEl.style.strokeDashoffset = String(offset);
    });
  });

  animateGaugeNumber(el.querySelector(".gauge-number"), score);
}

function renderChecksGrid(run, datasetName) {
  const gridEl = document.getElementById("checks-grid");

  if (!run) {
    gridEl.innerHTML = "";
    return;
  }

  const entries = Object.entries(run.breakdown);
  gridEl.innerHTML = entries
    .map(([checkName, data]) => {
      const meta = CHECK_META[checkName] || { label: checkName, code: "??" };
      const passRate = data.pass_rate * 100;
      const status = passRate >= 95 ? "pass" : passRate >= 85 ? "warn" : "fail";
      return `
        <button type="button" class="check-card status-${status}" data-check="${escapeHtml(checkName)}" aria-expanded="false">
          <div class="check-card-head">
            <span class="check-code">${meta.code}</span>
            <span class="check-name">${escapeHtml(meta.label)}</span>
          </div>
          <p class="check-description">${escapeHtml(meta.description || "")}</p>
          <div class="check-rate-row">
            <span class="check-rate">${passRate.toFixed(1)}%</span>
            <span class="check-rate-label">rows passed</span>
          </div>
          <div class="check-bar-track"><div class="check-bar-fill" style="width:${passRate}%"></div></div>
          <div class="check-affected">${data.affected_rows.toLocaleString()} of ${data.total_rows.toLocaleString()} rows affected (${(100 - passRate).toFixed(1)}%)</div>
          <div class="check-weight">weight ${data.weight}</div>
        </button>
        <div class="check-detail" id="detail-${escapeHtml(checkName)}" hidden></div>`;
    })
    .join("");

  gridEl.querySelectorAll(".check-card").forEach((card) => {
    card.addEventListener("click", () => toggleDetail(card, run, datasetName));
  });
}

const AFFECTED_ROWS_SHOWN = 50; // what a check's detail lists, and all that is asked of the server (a check can flag a million)

async function toggleDetail(card, run, datasetName) {
  const checkName = card.dataset.check;
  const detailEl = document.getElementById(`detail-${checkName}`);
  const expanded = card.getAttribute("aria-expanded") === "true";

  if (expanded) {
    detailEl.hidden = true;
    card.setAttribute("aria-expanded", "false");
    return;
  }

  card.setAttribute("aria-expanded", "true");
  detailEl.hidden = false;

  if (detailEl.dataset.loaded === "true") return;

  detailEl.innerHTML = `
    <div class="skeleton skeleton-line" style="width:45%;margin-bottom:12px"></div>
    <div class="skeleton skeleton-line" style="width:90%"></div>`;
  try {
    const res = await fetch(
      `/datasets/${encodeURIComponent(datasetName)}/runs/${run.id}/issues/${encodeURIComponent(checkName)}?limit=${AFFECTED_ROWS_SHOWN}`
    );
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const data = await res.json();
    const shown = data.row_keys.slice(0, AFFECTED_ROWS_SHOWN);

    // schema_changes and drift list sentences ("column 'x' added"), not short row keys: give each one its own line
    const sentences = checkName === "schema_changes" || checkName === "drift";
    detailEl.innerHTML = `
      <p class="detail-count">Showing ${shown.length.toLocaleString()} of ${data.count.toLocaleString()} affected rows</p>
      <ul class="detail-list${sentences ? " detail-list-text" : ""}">${shown.map((key) => `<li><code>${escapeHtml(key)}</code></li>`).join("")}</ul>`;
    detailEl.dataset.loaded = "true";
  } catch (err) {
    detailEl.innerHTML = `<p class="error">Couldn't load affected rows (${escapeHtml(err.message)}).</p>`;
  }
}

function renderTrendChart(runs) {
  const wrapEl = document.getElementById("trend-section");
  const canvas = document.getElementById("trend-chart");

  if (!runs.length) {
    wrapEl.style.display = "none";
    return;
  }
  wrapEl.style.display = "";

  const labels = runs.map((r) => formatTimestamp(r.created_at, true));
  const scores = runs.map((r) => r.score);

  if (trendChart) {
    trendChart.destroy();
  }

  trendChart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Score",
          data: scores,
          borderColor: "#5b9fd6",
          backgroundColor: "rgba(91, 159, 214, 0.12)",
          fill: true,
          tension: 0.25,
          pointRadius: 3,
          pointBackgroundColor: "#5b9fd6",
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          min: 0,
          max: 100,
          ticks: { color: "#878c96" },
          grid: { color: "rgba(255,255,255,0.06)" },
        },
        x: {
          ticks: { color: "#878c96" },
          grid: { display: false },
        },
      },
    },
  });
}

function updateCoverageNote(run) {
  const el = document.getElementById("coverage-note");
  if (!run) {
    el.textContent = "";
    return;
  }
  const implementedWeight = Object.values(run.breakdown).reduce((sum, c) => sum + c.weight, 0);
  const implementedCount = Object.keys(run.breakdown).length;
  el.textContent = `${implementedCount} of 8 checks live — covers ${implementedWeight} of ${TOTAL_PLANNED_WEIGHT} score weight.`;
}

async function loadDataset(name) {
  const scorecardEl = document.getElementById("scorecard");
  renderScorecardSkeleton();
  renderChecksGridSkeleton();

  try {
    const res = await fetch(`/datasets/${encodeURIComponent(name)}/runs`);

    if (res.status === 404) {
      renderScorecard(null, name);
      renderChecksGrid(null, name);
      renderTrendChart([]);
      updateCoverageNote(null);
      return;
    }
    if (!res.ok) throw new Error(`Server returned ${res.status}`);

    const runs = await res.json();
    const latest = runs.length ? runs[runs.length - 1] : null;

    renderScorecard(latest, name);
    renderChecksGrid(latest, name);
    try {
      renderTrendChart(runs);
    } catch (chartErr) {
      // The chart is a nice-to-have, rendered by a separate CDN library -
      // if that failed to load, the scorecard and checks above (already
      // rendered) shouldn't disappear along with it.
      document.getElementById("trend-section").style.display = "none";
      console.error("Trend chart failed to render:", chartErr);
    }
    updateCoverageNote(latest);
  } catch (err) {
    scorecardEl.innerHTML = `
      <div class="empty-state">
        <p class="eyebrow">Something went wrong</p>
        <h1>Couldn't reach the API</h1>
        <p class="empty-copy">${escapeHtml(err.message)} — is uvicorn still running?</p>
      </div>`;
  }
}

function initDatasetField() {
  const field = document.getElementById("dataset-field");
  const chipLabel = document.getElementById("dataset-chip-label");
  const input = document.getElementById("dataset-name");

  field.addEventListener("toggle", () => {
    if (field.open) {
      input.focus();
      input.select();
    }
  });

  function closeEditor() {
    const name = input.value.trim();
    if (name) {
      chipLabel.textContent = name;
      chipLabel.removeAttribute("data-empty");
    } else {
      chipLabel.textContent = "no label set";
      chipLabel.setAttribute("data-empty", "true");
    }
    field.open = false;
  }

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    } else if (event.key === "Escape") {
      input.value = chipLabel.dataset.empty ? "" : chipLabel.textContent;
      input.blur();
    }
  });

  input.addEventListener("blur", closeEditor);

  // The field starts open and empty (no default dataset like "customers"
  // anymore) - put the cursor in it immediately instead of making the
  // first-time visitor click to discover it's editable. preventScroll here
  // matters: without it, focusing an element below the fold on page load
  // makes the browser auto-scroll down to it, so the page never actually
  // starts at the top like a fresh load should.
  if (field.open && !input.value) {
    input.focus({ preventScroll: true });
  }
}

let relationshipRowId = 0;

function createRelationshipRow() {
  const row = document.createElement("div");
  row.className = "relationship-row";
  row.dataset.rowId = String(relationshipRowId++);
  row.innerHTML = `
    <input type="text" class="rel-column" placeholder="column here (e.g. customer_id)" spellcheck="false" />
    <span class="relationship-connector">must exist in</span>
    <label class="file-btn">
      <input type="file" class="rel-file" accept=".csv" />
      <span class="rel-file-label">Choose CSV</span>
    </label>
    <input type="text" class="rel-ref-column" placeholder="column there (e.g. id)" spellcheck="false" />
    <button type="button" class="remove-relationship-btn" aria-label="Remove relationship">✕</button>
  `;

  const fileInput = row.querySelector(".rel-file");
  const fileLabel = row.querySelector(".rel-file-label");
  fileInput.addEventListener("change", () => {
    turnAwayIfTooBig(fileInput, document.getElementById("form-status"), "Try a smaller related file.");
    fileLabel.textContent = fileInput.files.length ? fileInput.files[0].name : "Choose CSV";
  });

  row.querySelector(".remove-relationship-btn").addEventListener("click", () => row.remove());

  return row;
}

function initRelationshipsField() {
  const list = document.getElementById("relationships-list");
  document.getElementById("add-relationship-btn").addEventListener("click", () => {
    list.appendChild(createRelationshipRow());
  });
}

// Reads every relationship row that's actually filled in (column + reference
// file + reference column) and appends them to the upload's form data - a
// row missing any of the three is silently skipped rather than blocking
// the whole upload, since it's just an unfinished rule, not an error.
function appendRelationships(formData) {
  const rows = document.querySelectorAll("#relationships-list .relationship-row");
  const rules = [];
  let referenceIndex = 0;

  rows.forEach((row) => {
    const column = row.querySelector(".rel-column").value.trim();
    const referenceColumn = row.querySelector(".rel-ref-column").value.trim();
    const file = row.querySelector(".rel-file").files[0];

    if (!column || !referenceColumn || !file) return;

    formData.append("reference_files", file);
    rules.push({ column, reference_index: referenceIndex, reference_column: referenceColumn });
    referenceIndex += 1;
  });

  formData.append("relationships", JSON.stringify(rules));
}

function summarizeRun(run) {
  const feedback = run.relationship_feedback;
  if (!feedback || !feedback.length) {
    return "✓ Run complete.";
  }

  const applied = feedback.filter((f) => f.status === "applied");
  const skipped = feedback.filter((f) => f.status === "skipped");
  const brokenTotal = applied.reduce((sum, f) => sum + f.affected_rows, 0);

  const parts = [`✓ Run complete — ${applied.length} relationship rule${applied.length === 1 ? "" : "s"} checked`];
  if (applied.length) {
    parts.push(brokenTotal > 0 ? `${brokenTotal} broken reference${brokenTotal === 1 ? "" : "s"} found` : "no broken references");
  }
  if (skipped.length) {
    parts.push(`${skipped.length} rule${skipped.length === 1 ? "" : "s"} skipped`);
  }
  return parts.join(", ") + ".";
}

// Confirms to the user exactly what each relationship rule they configured
// actually did - matched cleanly, found broken references, or got skipped
// (with why) - instead of leaving them to infer it from the score alone.
function renderRelationshipResult(feedback) {
  const el = document.getElementById("relationships-result");
  const field = document.getElementById("relationships-field");

  if (!feedback || !feedback.length) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }

  el.hidden = false;
  el.innerHTML = feedback
    .map((f) => {
      if (f.status === "applied") {
        const ok = f.affected_rows === 0;
        const icon = ok ? "✓" : "⚠";
        const detail = ok
          ? "every value matched"
          : `${f.affected_rows.toLocaleString()} of ${f.total_rows.toLocaleString()} rows had no match`;
        return `<p class="relationship-result-line result-${ok ? "pass" : "warn"}">${icon}
          <code>${escapeHtml(f.column)}</code> → <code>${escapeHtml(f.reference_label)}</code>: ${detail}</p>`;
      }
      return `<p class="relationship-result-line result-skip">✕
        <code>${escapeHtml(f.column)}</code>: skipped — ${escapeHtml(f.reason)}</p>`;
    })
    .join("");

  field.open = true; // surface the confirmation even if the section had been collapsed
}

function accessLevelBadge(accessLevel) {
  return `<span class="access-badge access-${escapeHtml(accessLevel)}">${escapeHtml(accessLevel)}</span>`;
}

function getViewerRole() {
  return document.getElementById("viewer-role-select")?.value || "employee";
}

function renderDocumentsList(docs) {
  const el = document.getElementById("documents-list");

  if (!docs.length) {
    el.innerHTML = `<p class="empty-copy">No documents yet — upload one above.</p>`;
    return;
  }

  el.innerHTML = docs
    .map((d) => {
      const versionTag = d.version > 1 ? `<span class="version-tag">v${d.version}</span>` : "";
      if (d.locked) {
        // Listed so it can be found, but nothing from inside it: no size, no text, no history.
        return `
        <div class="document-card is-locked" ${documentCardAttrs(d)}>
          <div class="check-card-head">
            <span class="check-code">${escapeHtml(d.content_type.toUpperCase())}</span>
            ${documentNameButton(d.filename, true)}
            ${versionTag}
            ${accessLevelBadge(d.access_level)}
          </div>
          <p class="check-description">Added ${formatTimestamp(d.uploaded_at, true)}</p>
          ${lockedBodyHtml(d, false)}
        </div>`;
      }
      const historyBtn =
        d.version > 1
          ? `<button type="button" class="view-history-btn" data-document-id="${d.id}">View history</button>`
          : "";
      return `
        <div class="document-card" ${documentCardAttrs(d)}>
          <div class="check-card-head">
            <span class="check-code">${escapeHtml(d.content_type.toUpperCase())}</span>
            ${documentNameButton(d.filename)}
            ${versionTag}
            ${accessLevelBadge(d.access_level)}
          </div>
          <p class="check-description">${d.char_count.toLocaleString()} characters extracted &middot; ${formatTimestamp(d.uploaded_at, true)}</p>
          <div class="document-actions">
            <button type="button" class="open-document-btn doc-open-toggle" aria-expanded="false">Open</button>
            ${historyBtn}
          </div>
          <div class="document-viewer" hidden></div>
          <div class="version-history" hidden></div>
        </div>`;
    })
    .join("");
}

async function loadDocuments() {
  const el = document.getElementById("documents-list");
  el.innerHTML = `<p class="loading">Loading…</p>`;
  try {
    const res = await docFetch(`/documents?role=${encodeURIComponent(getViewerRole())}`);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    renderDocumentsList(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="error">Couldn't load documents (${escapeHtml(err.message)}).</p>`;
  }
}

function initDocumentsList() {
  const el = document.getElementById("documents-list");

  el.addEventListener("click", async (event) => {
    const btn = event.target.closest(".view-history-btn");
    if (!btn) return;

    const panel = btn.closest(".document-card").querySelector(".version-history");
    if (!panel.hidden) {
      panel.hidden = true;
      return;
    }

    panel.hidden = false;
    panel.innerHTML = `<p class="loading">Loading history…</p>`;

    try {
      const res = await docFetch(`/documents/${btn.dataset.documentId}/history?role=${encodeURIComponent(getViewerRole())}`);
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const versions = await res.json();

      panel.innerHTML = versions
        .map(
          (v) => `
            <div class="version-row">
              <span class="version-tag">v${v.version}${v.is_latest ? " · latest" : ""}</span>
              ${accessLevelBadge(v.access_level)}
              <span class="version-meta">${v.char_count.toLocaleString()} chars &middot; ${formatTimestamp(v.uploaded_at, true)}</span>
            </div>`
        )
        .join("");
    } catch (err) {
      panel.innerHTML = `<p class="error">Couldn't load history (${escapeHtml(err.message)}).</p>`;
    }
  });
}

// ---- Access codes: what this page has unlocked ----
//
// Documents are protected on the server: every document request carries the
// access code in an X-Access-Code header (and, for Confidential, the ID number
// in an X-Access-Id header), and the server refuses whatever that code doesn't
// open. This section only looks after the code and ID the person typed -
// held in memory and nowhere else, so a refresh (or closing the tab) forgets
// them and they have to be typed in again - and keeps the "Viewing as"
// dropdown in step with them.

// An earlier version kept the code in sessionStorage; that copy is wiped on load.
const LEGACY_ACCESS_CODE_STORAGE_KEY = "dqp.accessCode";
const ACCESS_ROLES = ["employee", "manager", "admin"]; // least -> most access, same order as the server
const ACCESS_LEVEL_NAMES = { employee: "Public", manager: "Team", admin: "Confidential" };
const LEVEL_REQUIRED_ROLE = { public: "employee", team: "manager", confidential: "admin" };
// What each greyed-out "Viewing as" choice is waiting for. Confidential takes the code and an ID number.
const ACCESS_NEEDS = { manager: "the team code", admin: "the confidential code and ID number" };

let accessCode = ""; // a code the server has accepted; sent with every document request
let accessId = ""; // the ID number that went with the Confidential code (empty for every other level)
let accessRole = "employee"; // the most access that code (and ID) opens
let accessHint = null; // what the hint box shows (the demo codes and ID), when the server has it switched on
let rerunDocumentSearch = () => Promise.resolve(); // set once the search form exists: runs the search on screen again

// fetch() for the document endpoints: the same call, plus the access code (and the ID number, once there is one).
function docFetch(url, init = {}) {
  if (!accessCode) return fetch(url, init);
  const headers = { ...(init.headers || {}), "X-Access-Code": accessCode };
  if (accessId) headers["X-Access-Id"] = accessId;
  return fetch(url, { ...init, headers });
}

// Can someone viewing as `role` open a document saved at `level`?
function roleCanOpen(role, level) {
  return ACCESS_ROLES.indexOf(role) >= ACCESS_ROLES.indexOf(LEVEL_REQUIRED_ROLE[level]);
}

function setAccessMessage(text, tone) {
  const el = document.getElementById("access-message");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("is-error", tone === "error");
}

// Greys out the "Viewing as" choices the current code doesn't open, and shows the lock state.
function syncAccessUi() {
  const select = document.getElementById("viewer-role-select");
  const status = document.getElementById("access-status");
  const lockBtn = document.getElementById("access-lock-btn");
  const unlocked = accessRole !== "employee";

  if (select) {
    for (const option of select.options) {
      option.disabled = ACCESS_ROLES.indexOf(option.value) > ACCESS_ROLES.indexOf(accessRole);
      const name = ACCESS_LEVEL_NAMES[option.value];
      option.textContent = option.disabled ? `${name} — needs ${ACCESS_NEEDS[option.value]}` : name;
    }
  }
  if (status) {
    status.textContent = unlocked
      ? `Unlocked — ${ACCESS_LEVEL_NAMES[accessRole]} access.`
      : "Locked — only public documents open.";
    status.classList.toggle("is-unlocked", unlocked);
  }
  if (lockBtn) lockBtn.hidden = !unlocked;
}

// Moves to what a code (and ID) opens, or back to public, and refreshes what is on screen.
function setAccess(role, code, { reload = true, id = "" } = {}) {
  accessRole = role;
  accessCode = code;
  accessId = id;

  const select = document.getElementById("viewer-role-select");
  if (select) select.value = role; // the person just asked for this much access, so show it
  syncAccessUi();
  // The dropdown's change handler reloads the list, the analytics and any search on screen.
  if (reload && select) select.dispatchEvent(new Event("change"));
}

async function requestUnlock(code, id) {
  const res = await fetch("/documents/access/unlock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(id ? { code, id } : { code }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(typeof body.detail === "string" ? body.detail : `Server returned ${res.status}`);
    error.status = res.status; // 403 = a wrong code or ID; the tries below count only those
    throw error;
  }
  // { role, level, needs_id } - needs_id: the Confidential code was right but no ID came with it yet
  return body;
}

function describeUnlock(role) {
  const opened = role === "admin" ? "Confidential, Team and Public" : "Team and Public";
  return `✓ Unlocked — you can now open ${opened} documents.`;
}

// ---- Three tries at an access code ----
//
// A wrong code or ID (the server's 403) uses up one of three tries. The third
// wrong one switches code entry off - the code boxes, the Unlock buttons, the
// hint chips, the ID pad's keys - until the page is refreshed, which starts
// over. The count is this one variable and nothing else, like the code itself. It slows a person down at the
// page; it is not a limit the server enforces, so a script calling the API
// directly isn't held to it.

const MAX_WRONG_CODES = 3;
const CODE_LOCKOUT_MESSAGE = "Too many wrong codes. Refresh the page to start over.";
let wrongCodes = 0; // wrong codes typed since this page loaded

function codeEntryLocked() {
  return wrongCodes >= MAX_WRONG_CODES;
}

// The server's "wrong code" message plus what is left: "That access code isn't right. 2 tries left."
function wrongCodeMessage(serverMessage) {
  if (codeEntryLocked()) return `${serverMessage} No tries left — refresh the page to start over.`;
  const left = MAX_WRONG_CODES - wrongCodes;
  return `${serverMessage} ${left} ${left === 1 ? "try" : "tries"} left.`;
}

// Switches off every place a code can be typed and says why wherever it is open.
function lockOutCodeEntry() {
  document
    .querySelectorAll(".access-code-input, .access-unlock-btn, .access-hint-code, .id-key")
    .forEach((el) => {
      el.disabled = true;
    });
  document.querySelectorAll(".unlock-prompt:not([hidden]) .unlock-error").forEach((el) => {
    el.textContent = CODE_LOCKOUT_MESSAGE;
  });
  setAccessMessage(CODE_LOCKOUT_MESSAGE, "error");
  lockIdPad();
}

// What to show when an unlock fails. Only a code the server turned down (403)
// uses up a try; a blank box or a server that can't be reached doesn't.
function codeFailureMessage(err) {
  if (err.status !== 403) return err.message;
  wrongCodes += 1;
  if (codeEntryLocked()) lockOutCodeEntry();
  return wrongCodeMessage(err.message);
}

async function submitAccessCode() {
  const input = document.getElementById("access-code-input");
  const btn = document.getElementById("access-unlock-btn");
  const code = input.value.trim();
  setAccessMessage("", "ok");

  if (codeEntryLocked()) {
    setAccessMessage(CODE_LOCKOUT_MESSAGE, "error");
    return;
  }
  if (!code) {
    setAccessMessage("Enter an access code first.", "error");
    input.focus();
    return;
  }

  btn.disabled = true;
  try {
    const { role, needs_id: needsId } = await requestUnlock(code);
    let granted = role;
    let id = "";
    if (needsId) {
      // The Confidential code is right; the other half of the key is the ID number, typed on the pad.
      const result = await askForId(code);
      if (!result) {
        setAccessMessage(
          codeEntryLocked() ? CODE_LOCKOUT_MESSAGE : "Confidential needs the ID number too — nothing was unlocked.",
          codeEntryLocked() ? "error" : "ok"
        );
        return;
      }
      ({ id, role: granted } = result);
    }
    input.value = "";
    setAccess(granted, code, { id });
    setAccessMessage(describeUnlock(granted), "ok");
  } catch (err) {
    setAccessMessage(codeFailureMessage(err), "error");
    if (!codeEntryLocked()) {
      input.focus();
      input.select();
    }
  } finally {
    btn.disabled = codeEntryLocked();
  }
}

function lockAccess() {
  setAccess("employee", "");
  setAccessMessage("Locked — only public documents open.", "ok");
}

// Every page load starts locked. Nothing about a code is kept anywhere, so this
// only has to make sure nothing is left over: a code stored by an earlier
// version of the page, and a "Viewing as" choice the browser restored on refresh.
function startLocked() {
  try {
    sessionStorage.removeItem(LEGACY_ACCESS_CODE_STORAGE_KEY);
  } catch {
    // Storage can be blocked (private windows); then there is nothing to clean up.
  }
  accessRole = "employee";
  accessCode = "";
  accessId = "";
  const select = document.getElementById("viewer-role-select");
  if (select) select.value = "employee";
}

// The hint box: the demo codes themselves, so they don't get forgotten.
async function loadAccessHint() {
  const box = document.getElementById("access-hint");
  if (!box) return;
  try {
    const res = await fetch("/documents/access/hint");
    if (!res.ok) return;
    const hint = await res.json();
    if (!hint.enabled) return;
    accessHint = hint; // the ID pad shows the ID number too, so it isn't forgotten while the pad covers this box

    const codes = [
      ["access-hint-team", hint.team_code],
      ["access-hint-confidential", hint.confidential_code],
    ];
    for (const [id, code] of codes) {
      const el = document.getElementById(id);
      el.textContent = code;
      el.dataset.code = code;
    }
    // The ID number belongs to the Confidential code only, so it is shown beside that one.
    if (hint.confidential_id) {
      document.getElementById("access-hint-id").textContent = hint.confidential_id;
      document.getElementById("access-hint-id-group").hidden = false;
    }
    box.hidden = false;
  } catch {
    // The hint box is a convenience; the page works without it.
  }
}

function initAccessPanel() {
  startLocked();
  initIdPad();

  const form = document.getElementById("access-form");
  if (!form) return;

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAccessCode();
  });
  document.getElementById("access-lock-btn").addEventListener("click", lockAccess);
  document.getElementById("access-hint").addEventListener("click", (event) => {
    const chip = event.target.closest(".access-hint-code");
    if (!chip || chip.disabled || !chip.dataset.code) return;
    const input = document.getElementById("access-code-input");
    input.value = chip.dataset.code;
    input.focus();
  });

  syncAccessUi();
  loadAccessHint();
}

// ---- The ID pad: the 5-dot passcode screen that follows the Confidential code ----
//
// Confidential is the one level that takes two things: its code and an ID number. When
// the server says the code is right but no ID came with it, this screen asks for the ID
// the way a phone asks for a passcode: five dots fill in as digits go in and the fifth
// sends it. The server decides - a wrong ID shakes the dots, empties them and uses up one
// of the three tries the code boxes use. The digits live in one variable while the pad is
// open and nowhere else: not in the page, a URL or storage.

const ID_LENGTH = 5; // keep in step with the server: CONFIDENTIAL_ID_NUMBER is exactly this many digits
const ID_PAD_PAUSE_MS = 140; // lets the fifth dot show before the answer comes back
const ID_PAD_SHAKE_MS = 520;
const ID_PAD_STILL_MS = 400; // with reduced motion there is no shake: the red dots just stay a moment
const ID_PAD_ACCEPTED_MS = 260;

let idPad = null; // the pad that is open right now: { code, digits, busy, opener, resolve }; null otherwise

const idPadPause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function idPadEls() {
  return {
    dialog: document.getElementById("id-pad"),
    body: document.querySelector("#id-pad .id-pad-body"),
    dots: document.getElementById("id-pad-dots"),
    status: document.getElementById("id-pad-status"),
    count: document.getElementById("id-pad-count"),
    subtitle: document.getElementById("id-pad-subtitle"),
    hint: document.getElementById("id-pad-hint"),
    hintCode: document.getElementById("id-pad-hint-code"),
    del: document.getElementById("id-pad-delete"),
    cancel: document.getElementById("id-pad-cancel"),
  };
}

function setIdPadStatus(text, tone = "") {
  const { status } = idPadEls();
  status.textContent = text;
  status.classList.toggle("is-ok", tone === "ok");
}

// Fills as many dots as digits typed. Only the count is ever shown, never the digits.
function paintIdDots() {
  const { dots, del, count, body } = idPadEls();
  const entered = idPad ? idPad.digits.length : 0;
  dots.querySelectorAll(".id-pad-dot").forEach((dot, i) => dot.classList.toggle("is-filled", i < entered));
  count.textContent = entered ? `${entered} of ${ID_LENGTH} digits entered` : "";
  const hadFocus = document.activeElement === del;
  del.hidden = entered === 0; // nothing to delete yet
  if (del.hidden && hadFocus) body.focus({ preventScroll: true });
}

function pressIdDigit(digit) {
  const pad = idPad;
  if (!pad || pad.busy || codeEntryLocked() || pad.digits.length >= ID_LENGTH) return;
  if (pad.digits === "") setIdPadStatus(""); // a new try begins: the last message has done its job
  pad.digits += digit;
  paintIdDots();
  if (pad.digits.length === ID_LENGTH) submitIdPad();
}

function deleteIdDigit() {
  const pad = idPad;
  if (!pad || pad.busy || !pad.digits) return;
  pad.digits = pad.digits.slice(0, -1);
  paintIdDots();
}

async function submitIdPad() {
  const pad = idPad;
  const { dots } = idPadEls();
  const id = pad.digits;
  pad.busy = true;
  await idPadPause(ID_PAD_PAUSE_MS);
  try {
    const result = await requestUnlock(pad.code, id);
    if (idPad !== pad) return; // closed while the server was thinking
    if (result.role !== "admin") throw new Error("That ID number wasn't accepted.");
    dots.classList.add("is-ok");
    setIdPadStatus("ID accepted", "ok");
    await idPadPause(ID_PAD_ACCEPTED_MS);
    closeIdPad({ id, role: result.role });
  } catch (err) {
    if (idPad !== pad) return;
    const message = codeFailureMessage(err); // a 403 uses up a try, and the third switches everything off
    setIdPadStatus(message);
    dots.classList.add("is-wrong");
    await idPadPause(prefersReducedMotion() ? ID_PAD_STILL_MS : ID_PAD_SHAKE_MS);
    if (idPad !== pad) return;
    dots.classList.remove("is-wrong");
    pad.digits = "";
    pad.busy = false;
    paintIdDots();
  }
}

// The third wrong try: the keys go grey, the message says why, and Cancel becomes Close.
function lockIdPad() {
  if (!idPad) return;
  const { cancel } = idPadEls();
  setIdPadStatus(CODE_LOCKOUT_MESSAGE);
  cancel.textContent = "Close";
  cancel.focus({ preventScroll: true });
}

function onIdPadKeydown(event) {
  if (!idPad || event.ctrlKey || event.metaKey || event.altKey) return;
  if (/^[0-9]$/.test(event.key)) {
    event.preventDefault();
    if (!event.repeat) pressIdDigit(event.key); // holding a key down types one digit, not five
  } else if (event.key === "Backspace" || event.key === "Delete") {
    event.preventDefault();
    deleteIdDigit();
  }
}

// Shows the pad for the Confidential `code` and resolves with { id, role } once the server
// accepts the ID - or null if the person cancels or the tries run out.
function askForId(code, { filename = "" } = {}) {
  const { dialog, body, dots, subtitle, hint, hintCode, cancel } = idPadEls();
  if (!dialog || idPad || codeEntryLocked()) return Promise.resolve(null);

  return new Promise((resolve) => {
    idPad = { code, digits: "", busy: false, opener: document.activeElement, resolve };
    dots.classList.remove("is-ok", "is-wrong");
    subtitle.textContent = filename ? `to open ${filename}` : "for Confidential documents";
    subtitle.title = filename;
    const shownId = accessHint && accessHint.confidential_id;
    hint.hidden = !shownId;
    hintCode.textContent = shownId || "";
    cancel.textContent = "Cancel";
    setIdPadStatus("");
    paintIdDots();
    document.addEventListener("keydown", onIdPadKeydown, true);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    body.focus({ preventScroll: true });
  });
}

function closeIdPad(result) {
  const pad = idPad;
  if (!pad) return;
  idPad = null;
  document.removeEventListener("keydown", onIdPadKeydown, true);
  const { dialog } = idPadEls();
  if (dialog.open) dialog.close();
  if (pad.opener && pad.opener.isConnected && typeof pad.opener.focus === "function") {
    pad.opener.focus({ preventScroll: true });
  }
  pad.resolve(result);
}

function initIdPad() {
  const { dialog, del, cancel } = idPadEls();
  if (!dialog) return;
  document.getElementById("id-pad-keys").addEventListener("click", (event) => {
    const key = event.target.closest(".id-key");
    if (key && !key.disabled) pressIdDigit(key.dataset.digit);
  });
  del.addEventListener("click", deleteIdDigit);
  cancel.addEventListener("click", () => closeIdPad(null));
  dialog.addEventListener("close", () => closeIdPad(null)); // Escape closes it too
}

// ---- Opening a document: its text, or a CSV as a table ----

const TABLE_PAGE_ROWS = 200;

function documentNameButton(filename, locked = false) {
  const title = locked ? "Enter the access code to open this document" : "Open this document";
  return `<button type="button" class="check-name open-document-btn open-document-link" aria-expanded="false" title="${title}">${escapeHtml(filename)}</button>`;
}

function documentCardAttrs(doc) {
  return `data-document-id="${doc.id}" data-content-type="${escapeAttr(doc.content_type)}" data-filename="${escapeAttr(doc.filename)}" data-access-level="${escapeAttr(doc.access_level)}"`;
}

// ---- Locked documents: found by listing and search, opened with a code ----
//
// A document above the code you've entered still turns up, but as a locked card:
// its name, type, level and date. The server sends nothing from inside it, so
// there is nothing here to hide - this only asks for the code and, once it is
// accepted, reloads (the same document now comes back in full) and opens it.

const LOCKED_NOTES = {
  team: "opens with the Team code (or the Confidential code and ID number).",
  confidential: "opens with the Confidential code and ID number.",
};

function lockIconSvg() {
  return `<svg class="lock-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><rect x="3" y="7" width="10" height="7.5" rx="1.6" fill="currentColor"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
}

// The note, the button that asks for the code, and the (hidden until asked) code form.
function lockedBodyHtml(item, matched) {
  const level = item.access_level;
  const levelName = ACCESS_LEVEL_NAMES[LEVEL_REQUIRED_ROLE[level]] || level;
  const note = LOCKED_NOTES[level] || "needs an access code.";
  const lead = matched ? "Matches your search, but it's locked —" : "Locked —";
  const off = codeEntryLocked() ? " disabled" : ""; // cards that appear after the third wrong code arrive switched off
  return `
    <div class="locked-row">
      <p class="locked-note">${lockIconSvg()}<span>${escapeHtml(`${lead} ${note}`)}</span></p>
      <button type="button" class="open-document-btn doc-open-toggle doc-unlock-toggle" aria-expanded="false">Unlock to open</button>
    </div>
    <form class="unlock-prompt" autocomplete="off" hidden>
      <input type="text" class="access-code-input unlock-input" inputmode="numeric" autocomplete="off" spellcheck="false" maxlength="32" placeholder="Enter code" aria-label="${escapeAttr(levelName)} access code"${off} />
      <button type="submit" class="access-unlock-btn"${off}>Unlock &amp; open</button>
      <button type="button" class="unlock-cancel-btn">Cancel</button>
      <p class="unlock-error" role="alert"></p>
    </form>`;
}

function setUnlockPromptOpen(card, open) {
  const form = card.querySelector(".unlock-prompt");
  const errorEl = form.querySelector(".unlock-error");
  form.hidden = !open;
  card.querySelectorAll(".open-document-btn").forEach((btn) => btn.setAttribute("aria-expanded", String(open)));
  if (!open) errorEl.textContent = "";
  else if (codeEntryLocked()) errorEl.textContent = CODE_LOCKOUT_MESSAGE; // opened after the third wrong code
  else form.querySelector(".unlock-input").focus();
}

// A code (and, for Confidential, its ID number) is accepted: move to what it
// opens, reload what is on screen (this document now comes back unlocked) and
// open it where the person clicked.
async function openAfterUnlock(card, role, code, accessIdNumber = "") {
  const id = Number(card.dataset.documentId);
  const where = card.closest("#search-results") ? "#search-results" : "#documents-list";

  setAccess(role, code, { reload: false, id: accessIdNumber });
  setAccessMessage(describeUnlock(role), "ok");
  await Promise.all([loadDocuments(), rerunDocumentSearch(), loadAnalytics()]);

  const fresh = document.querySelector(`${where} .document-card[data-document-id="${id}"]`);
  if (!fresh || fresh.classList.contains("is-locked")) return;
  await toggleDocumentViewer(fresh);
  const toggle = fresh.querySelector(".doc-open-toggle");
  if (toggle) toggle.focus();
}

// A level the page doesn't know is treated as the strictest, so it is never opened by mistake.
function cardLevel(card) {
  return LEVEL_REQUIRED_ROLE[card.dataset.accessLevel] ? card.dataset.accessLevel : "confidential";
}

function toggleUnlockPrompt(card) {
  const form = card.querySelector(".unlock-prompt");
  if (form.hidden && roleCanOpen(accessRole, cardLevel(card))) {
    // The code entered earlier already covers this one (the page was just viewing a lower level).
    openAfterUnlock(card, accessRole, accessCode, accessId);
    return;
  }
  setUnlockPromptOpen(card, form.hidden);
}

async function submitUnlockPrompt(form) {
  const card = form.closest(".document-card");
  const input = form.querySelector(".unlock-input");
  const errorEl = form.querySelector(".unlock-error");
  const submitBtn = form.querySelector(".access-unlock-btn");
  const code = input.value.trim();
  errorEl.textContent = "";

  if (codeEntryLocked()) {
    errorEl.textContent = CODE_LOCKOUT_MESSAGE;
    return;
  }
  if (!code) {
    errorEl.textContent = "Enter the access code first.";
    input.focus();
    return;
  }

  submitBtn.disabled = true;
  try {
    const { role, needs_id: needsId } = await requestUnlock(code);
    let granted = role;
    let idNumber = "";
    if (needsId) {
      // The Confidential code is right; the ID number is the other half, typed on the pad.
      const result = await askForId(code, { filename: card.dataset.filename });
      if (!result) {
        errorEl.textContent = codeEntryLocked() ? CODE_LOCKOUT_MESSAGE : "Confidential documents need the ID number too.";
        return;
      }
      ({ id: idNumber, role: granted } = result);
    }
    const level = cardLevel(card);
    if (!roleCanOpen(granted, level)) {
      // A real code, just not for this level: it isn't a wrong guess, so it costs no try.
      const needs = ACCESS_LEVEL_NAMES[LEVEL_REQUIRED_ROLE[level]];
      const needsIdToo = level === "confidential" ? " and ID number" : "";
      errorEl.textContent = `That's the ${ACCESS_LEVEL_NAMES[granted]} code — it doesn't open this one. This needs the ${needs} code${needsIdToo}.`;
      input.focus();
      input.select();
      return;
    }
    await openAfterUnlock(card, granted, code, idNumber);
  } catch (err) {
    errorEl.textContent = codeFailureMessage(err);
    if (!codeEntryLocked()) {
      input.focus();
      input.select();
    }
  } finally {
    submitBtn.disabled = codeEntryLocked();
  }
}

async function describeOpenFailure(res) {
  if (res.status === 403) return "The access code you have unlocked doesn't open this document. Enter the right code above.";
  if (res.status === 404) return "That document isn't there any more.";
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : `Couldn't open it (server returned ${res.status}).`;
}

function setDocumentOpen(card, open) {
  card.classList.toggle("is-open", open);
  card.querySelectorAll(".open-document-btn").forEach((btn) => btn.setAttribute("aria-expanded", String(open)));
  const toggle = card.querySelector(".doc-open-toggle");
  if (toggle) toggle.textContent = open ? "Close" : "Open";
}

async function showDocumentText(viewer, id) {
  const res = await docFetch(`/documents/${id}?role=${encodeURIComponent(getViewerRole())}`);
  if (!res.ok) throw new Error(await describeOpenFailure(res));
  const doc = await res.json();
  viewer.innerHTML = `<pre class="doc-viewer-text" tabindex="0">${escapeHtml(doc.text_content)}</pre>`;
}

function fetchTablePage(id, offset) {
  return docFetch(
    `/documents/${id}/table?role=${encodeURIComponent(getViewerRole())}&offset=${offset}&limit=${TABLE_PAGE_ROWS}`
  );
}

function tableRowsHtml(rows, offset) {
  return rows
    .map((row, i) => {
      const cells = row
        .map((cell) => {
          const title = cell.length > 24 ? ` title="${escapeAttr(cell.slice(0, 500))}"` : "";
          return `<td${title}>${escapeHtml(cell)}</td>`;
        })
        .join("");
      return `<tr><td class="doc-table-index">${(offset + i + 1).toLocaleString()}</td>${cells}</tr>`;
    })
    .join("");
}

function updateTableFooter(viewer) {
  const loaded = Number(viewer.dataset.nextOffset);
  const total = Number(viewer.dataset.total);
  viewer.querySelector(".doc-viewer-count").textContent =
    total === 0
      ? "No rows — this file only has a header."
      : `Showing ${loaded.toLocaleString()} of ${total.toLocaleString()} row${total === 1 ? "" : "s"}`;
  viewer.querySelector(".doc-more-btn").hidden = loaded >= total;
}

async function showCsvTable(viewer, id) {
  const res = await fetchTablePage(id, 0);
  if (res.status === 400) {
    await showDocumentText(viewer, id); // can't be read as a table - show the raw text instead
    return;
  }
  if (!res.ok) throw new Error(await describeOpenFailure(res));
  const table = await res.json();

  const head = table.columns
    .map((c) => `<th scope="col">${c ? escapeHtml(c) : `<span class="doc-table-unnamed">(unnamed)</span>`}</th>`)
    .join("");
  viewer.dataset.nextOffset = String(table.rows.length);
  viewer.dataset.total = String(table.total_rows);
  viewer.innerHTML = `
    <div class="doc-table-wrap" tabindex="0" role="region" aria-label="Rows of this file">
      <table class="doc-table">
        <thead><tr><th scope="col" class="doc-table-index">#</th>${head}</tr></thead>
        <tbody>${tableRowsHtml(table.rows, 0)}</tbody>
      </table>
    </div>
    <div class="doc-viewer-footer">
      <span class="doc-viewer-count"></span>
      <button type="button" class="doc-more-btn">Show more rows</button>
      <button type="button" class="doc-download-btn">Download CSV</button>
    </div>`;
  updateTableFooter(viewer);
}

async function showMoreRows(card, btn) {
  const viewer = card.querySelector(".document-viewer");
  btn.disabled = true;
  try {
    const res = await fetchTablePage(card.dataset.documentId, Number(viewer.dataset.nextOffset));
    if (!res.ok) throw new Error(await describeOpenFailure(res));
    const table = await res.json();

    viewer.querySelector("tbody").insertAdjacentHTML("beforeend", tableRowsHtml(table.rows, table.offset));
    const loaded = table.offset + table.rows.length;
    viewer.dataset.nextOffset = String(loaded);
    viewer.dataset.total = String(table.rows.length ? table.total_rows : loaded);
    updateTableFooter(viewer);
  } catch (err) {
    viewer.querySelector(".doc-viewer-count").textContent = `Couldn't load more rows: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

// The table shows a page at a time; this fetches the whole file for keeping.
async function downloadCsv(card, btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Preparing…";
  try {
    const res = await docFetch(`/documents/${card.dataset.documentId}?role=${encodeURIComponent(getViewerRole())}`);
    if (!res.ok) throw new Error(await describeOpenFailure(res));
    const doc = await res.json();

    const url = URL.createObjectURL(new Blob([`${doc.text_content}\n`], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = card.dataset.filename || "data.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    btn.textContent = original;
  } catch {
    btn.textContent = "Download failed";
    setTimeout(() => {
      btn.textContent = original;
    }, 2000);
  } finally {
    btn.disabled = false;
  }
}

async function toggleDocumentViewer(card) {
  if (card.classList.contains("is-locked")) {
    toggleUnlockPrompt(card); // nothing to show yet: ask for the code instead
    return;
  }
  const viewer = card.querySelector(".document-viewer");
  if (!viewer.hidden) {
    viewer.hidden = true;
    viewer.innerHTML = "";
    setDocumentOpen(card, false);
    return;
  }

  setDocumentOpen(card, true);
  viewer.hidden = false;
  viewer.innerHTML = `<p class="loading">Opening…</p>`;
  try {
    if (card.dataset.contentType === "csv") await showCsvTable(viewer, card.dataset.documentId);
    else await showDocumentText(viewer, card.dataset.documentId);
  } catch (err) {
    viewer.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  }
}

// One listener for both the document list and the search results.
function initDocumentViewer() {
  const section = document.getElementById("knowledge-search");
  if (!section) return;

  section.addEventListener("click", (event) => {
    const openBtn = event.target.closest(".open-document-btn");
    if (openBtn) {
      toggleDocumentViewer(openBtn.closest(".document-card"));
      return;
    }
    const moreBtn = event.target.closest(".doc-more-btn");
    if (moreBtn) {
      showMoreRows(moreBtn.closest(".document-card"), moreBtn);
      return;
    }
    const downloadBtn = event.target.closest(".doc-download-btn");
    if (downloadBtn) {
      downloadCsv(downloadBtn.closest(".document-card"), downloadBtn);
      return;
    }
    const cancelBtn = event.target.closest(".unlock-cancel-btn");
    if (cancelBtn) cancelUnlockPrompt(cancelBtn.closest(".document-card"));
  });

  // A locked card's code box: Enter submits it, Escape puts it away.
  section.addEventListener("submit", (event) => {
    const form = event.target.closest(".unlock-prompt");
    if (!form) return;
    event.preventDefault();
    submitUnlockPrompt(form);
  });
  section.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !event.target.closest(".unlock-prompt")) return;
    cancelUnlockPrompt(event.target.closest(".document-card"));
  });
}

function cancelUnlockPrompt(card) {
  setUnlockPromptOpen(card, false);
  const toggle = card.querySelector(".doc-open-toggle");
  if (toggle) toggle.focus();
}

function renderAnalyticsBar(label, count, maxCount) {
  const pct = maxCount > 0 ? Math.round((count / maxCount) * 100) : 0;
  return `
    <div class="analytics-bar-row">
      <span class="analytics-bar-label">${escapeHtml(label)}</span>
      <div class="check-bar-track"><div class="check-bar-fill" style="width: ${pct}%"></div></div>
      <span class="analytics-bar-count">${count}</span>
    </div>`;
}

function renderAnalyticsQueryList(entries, emptyText) {
  if (!entries.length) {
    return `<p class="empty-copy">${escapeHtml(emptyText)}</p>`;
  }
  return entries
    .map(
      (e) => `
      <div class="analytics-query-row">
        <span class="analytics-query-text">${escapeHtml(e.query)}</span>
        <span class="analytics-query-count">${e.count}</span>
      </div>`
    )
    .join("");
}

function renderAnalytics(summary) {
  const el = document.getElementById("analytics-summary");
  const modeCounts = summary.searches_by_mode;
  const maxModeCount = Math.max(1, ...Object.values(modeCounts));

  el.innerHTML = `
    <p class="check-description">${summary.total_searches.toLocaleString()} search${summary.total_searches === 1 ? "" : "es"} logged</p>
    <div class="analytics-mode-bars">
      ${renderAnalyticsBar("Keyword", modeCounts.keyword, maxModeCount)}
      ${renderAnalyticsBar("Semantic", modeCounts.semantic, maxModeCount)}
      ${renderAnalyticsBar("Hybrid", modeCounts.hybrid, maxModeCount)}
    </div>
    <div class="analytics-columns">
      <div class="analytics-column">
        <h4 class="analytics-column-title">Top queries</h4>
        ${renderAnalyticsQueryList(summary.top_queries, "No searches logged yet.")}
      </div>
      <div class="analytics-column">
        <h4 class="analytics-column-title">Zero-result queries</h4>
        ${renderAnalyticsQueryList(summary.zero_result_queries, "None — every logged query found something.")}
      </div>
    </div>`;
}

async function loadAnalytics() {
  const panel = document.getElementById("search-analytics");
  const role = getViewerRole();

  if (role !== "manager" && role !== "admin") {
    panel.hidden = true;
    return;
  }

  panel.hidden = false;
  const el = document.getElementById("analytics-summary");
  el.innerHTML = `<p class="loading">Loading…</p>`;

  try {
    const res = await docFetch(`/documents/analytics/summary?role=${encodeURIComponent(role)}`);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    renderAnalytics(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="error">Couldn't load analytics (${escapeHtml(err.message)}).</p>`;
  }
}

function initDeleteSearchLogsButton() {
  document.getElementById("delete-search-logs-btn").addEventListener("click", async () => {
    if (!confirm("Are you sure you want to delete all search logs? This cannot be recovered.")) return;

    try {
      const res = await docFetch(`/documents/search-logs?role=${encodeURIComponent(getViewerRole())}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      await loadAnalytics();
    } catch (err) {
      alert(`Couldn't delete search logs (${err.message}).`);
    }
  });
}

// Wraps every case-insensitive occurrence of the query in <mark>. Operates
// on already-escaped HTML with an already-escaped query, so this can never
// introduce markup the caller didn't already produce.
function highlightMatch(escapedSnippet, rawQuery) {
  const escapedQuery = escapeHtml(rawQuery).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!escapedQuery) return escapedSnippet;
  return escapedSnippet.replace(new RegExp(`(${escapedQuery})`, "ig"), "<mark>$1</mark>");
}

function renderSearchResults(results, query, mode) {
  const el = document.getElementById("search-results");
  el.hidden = false;

  if (!results.length) {
    const suffix = mode === "semantic" ? " in meaning" : "";
    el.innerHTML = `<p class="empty-copy">No documents match "${escapeHtml(query)}"${suffix}.</p>`;
    return;
  }

  const lockedCount = results.filter((r) => r.locked).length;
  const lockedNote = lockedCount ? ` &middot; ${lockedCount} locked` : "";
  const count = `<p class="search-results-count">${results.length} result${results.length === 1 ? "" : "s"} for "${escapeHtml(query)}" &middot; ${mode} search${lockedNote}</p>`;
  const cards = results
    .map((r) => {
      if (r.locked) {
        // It matched, and that is all the server says: no excerpt, citation or score.
        return `
        <div class="document-card is-locked" ${documentCardAttrs(r)}>
          <div class="check-card-head">
            <span class="check-code">${escapeHtml(r.content_type.toUpperCase())}</span>
            ${documentNameButton(r.filename, true)}
            ${accessLevelBadge(r.access_level)}
          </div>
          ${lockedBodyHtml(r, true)}
        </div>`;
      }

      const matchedByKeyword = mode === "keyword" || (mode === "hybrid" && r.sources.includes("keyword"));
      const snippetHtml = matchedByKeyword ? highlightMatch(escapeHtml(r.snippet), query) : escapeHtml(r.snippet);

      let badges = "";
      if (mode === "semantic") {
        badges = `<span class="similarity-badge">${Math.round(r.similarity * 100)}% match</span>`;
      } else if (mode === "hybrid") {
        badges = r.sources.map((s) => `<span class="source-badge">${escapeHtml(s)}</span>`).join("");
        if (r.similarity != null) {
          badges += `<span class="similarity-badge">${Math.round(r.similarity * 100)}%</span>`;
        }
      }

      return `
        <div class="document-card" ${documentCardAttrs(r)}>
          <div class="check-card-head">
            <span class="check-code">${escapeHtml(r.content_type.toUpperCase())}</span>
            ${documentNameButton(r.filename)}
            ${accessLevelBadge(r.access_level)}
            ${badges}
          </div>
          <p class="search-snippet">${snippetHtml}</p>
          <div class="search-citation">
            <span class="citation-label">${escapeHtml(r.citation)}</span>
            <span class="search-citation-actions">
              <button type="button" class="open-document-btn doc-open-toggle" aria-expanded="false">Open</button>
              <button type="button" class="copy-citation-btn" data-citation="${encodeURIComponent(r.citation)}">Copy citation</button>
            </span>
          </div>
          <div class="document-viewer" hidden></div>
        </div>`;
    })
    .join("");

  el.innerHTML = count + cards;
}

function initDocumentSearchForm() {
  const form = document.getElementById("document-search-form");
  const input = document.getElementById("document-search-input");
  const resultsEl = document.getElementById("search-results");
  const modeButtons = document.querySelectorAll(".search-mode-btn");
  const roleSelect = document.getElementById("viewer-role-select");
  let mode = "keyword";

  roleSelect.addEventListener("change", () => {
    loadDocuments();
    loadAnalytics();
    if (input.value.trim()) {
      form.requestSubmit();
    }
  });

  modeButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      modeButtons.forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      mode = btn.dataset.mode;
    });
  });

  resultsEl.addEventListener("click", (event) => {
    const btn = event.target.closest(".copy-citation-btn");
    if (!btn) return;
    const citation = decodeURIComponent(btn.dataset.citation);
    const original = btn.textContent;
    navigator.clipboard
      .writeText(citation)
      .then(() => {
        btn.textContent = "Copied!";
      })
      .catch(() => {
        btn.textContent = "Copy failed";
      })
      .finally(() => {
        setTimeout(() => {
          btn.textContent = original;
        }, 1500);
      });
  });

  async function runSearch() {
    const query = input.value.trim();

    if (!query) {
      resultsEl.hidden = true;
      resultsEl.innerHTML = "";
      return;
    }

    resultsEl.hidden = false;
    resultsEl.innerHTML = `<p class="loading">Searching…</p>`;

    const endpoint =
      mode === "semantic" ? "/documents/semantic-search" : mode === "hybrid" ? "/documents/hybrid-search" : "/documents/search";

    try {
      const res = await docFetch(`${endpoint}?q=${encodeURIComponent(query)}&role=${encodeURIComponent(getViewerRole())}`);
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      renderSearchResults(await res.json(), query, mode);
      loadAnalytics(); // this search was just logged server-side - reflect it immediately
    } catch (err) {
      resultsEl.innerHTML = `<p class="error">Search failed (${escapeHtml(err.message)}).</p>`;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch();
  });

  // Unlocking a document from a card re-runs whatever search is on screen, so its results come back unlocked.
  rerunDocumentSearch = () => (input.value.trim() ? runSearch() : Promise.resolve());
}

function initDocumentUploadForm() {
  const form = document.getElementById("document-upload-form");
  const fileInput = document.getElementById("document-file-input");
  const fileLabel = document.getElementById("document-file-label");
  const statusEl = document.getElementById("document-upload-status");
  const btn = document.getElementById("document-upload-btn");

  fileInput.addEventListener("change", () => {
    turnAwayIfTooBig(fileInput, statusEl, "Try a smaller file.");
    fileLabel.textContent = fileInput.files.length ? fileInput.files[0].name : "Choose a file";
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    statusEl.classList.remove("form-status-error");
    statusEl.textContent = "";

    if (!fileInput.files.length) {
      statusEl.classList.add("form-status-error");
      statusEl.textContent = "Choose a file first.";
      return;
    }

    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Uploading…";

    try {
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      formData.append("access_level", document.getElementById("document-access-level").value);
      const res = await fetch("/documents", { method: "POST", body: formData });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Server returned ${res.status}`);
      }

      const created = await res.json();
      fileInput.value = "";
      fileLabel.textContent = "Choose a file";
      const versionNote = created.version > 1 ? ` (new version, v${created.version})` : "";
      statusEl.textContent = `✓ Uploaded "${created.filename}"${versionNote} — ${created.char_count.toLocaleString()} characters extracted.`;
      await loadDocuments();
    } catch (err) {
      statusEl.classList.add("form-status-error");
      statusEl.textContent = `Upload failed: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  });
}

// ---- Save a check's report to Knowledge Search (opt-in, chosen per run) ----

const REPORT_ACCESS_HINTS = {
  public: "Anyone can open it — no code needed.",
  team: "Opens with the Team code (or the Confidential code and ID number).",
  confidential: "Opens with the Confidential code and ID number only.",
};
const REPORT_SCOPE_NOTE = "Saves the report and every uploaded row. Re-runs add a new version.";
const MAX_SAVED_DATA_BYTES = 25 * 1024 * 1024; // keep in step with _MAX_SAVED_DATA_BYTES in routers/runs.py

// The access-level dropdown only exists once the box is ticked.
function initSaveReportOption() {
  const box = document.getElementById("save-report-checkbox");
  const options = document.getElementById("save-report-options");
  const select = document.getElementById("save-report-access-level");
  const hint = document.getElementById("save-report-hint");
  if (!box || !options || !select || !hint) return;

  const refresh = () => {
    options.hidden = !box.checked;
    hint.textContent = `${REPORT_ACCESS_HINTS[select.value] || ""} ${REPORT_SCOPE_NOTE}`.trim();
  };
  box.addEventListener("change", refresh);
  select.addEventListener("change", refresh);
  refresh(); // also re-syncs if the browser restored a ticked box
}

function getReportOptions() {
  const box = document.getElementById("save-report-checkbox");
  const select = document.getElementById("save-report-access-level");
  return { enabled: Boolean(box && box.checked), accessLevel: select ? select.value : "team" };
}

function setReportStatus(text, tone) {
  const el = document.getElementById("save-report-status");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("is-working", tone === "working");
  el.classList.toggle("is-error", tone === "error");
}

// What was saved, and how to open it from where the person is right now.
function describeSavedReport(saved) {
  const level = saved.access_level;
  const levelLabel = level.charAt(0).toUpperCase() + level.slice(1);
  const isNew = saved.created || Boolean(saved.data && saved.data.created);
  const what = isNew ? "Saved to Knowledge Search" : "Already in Knowledge Search";
  const parts = [`report v${saved.version}`];
  if (saved.data) parts.push(`data v${saved.data.version}`);
  let message = `✓ ${what} as ${levelLabel} — ${parts.join(" and ")}.`;

  if (saved.data_error) {
    message += ` ${saved.data_error}`;
  }

  // It is in the list either way (locked ones are listed too) - what differs is whether the code entered so far opens it.
  if (roleCanOpen(accessRole, level)) {
    message += " Click it in the Knowledge Search list below to open it.";
  } else {
    const andId = level === "confidential" ? " and ID number" : "";
    message += ` It's in the Knowledge Search list below — it opens with the ${levelLabel} code${andId}.`;
  }
  return message;
}

// Runs after the check's results are already on screen. Never throws: a
// failed save is reported in its own line and doesn't turn a good run into
// "Run failed".
async function saveRunReport(datasetName, run, accessLevel, dataFile) {
  setReportStatus("Saving to Knowledge Search… the first save can take a few seconds.", "working");

  // The run doesn't keep the rows, so they are sent again here. A file over the
  // server's limit isn't worth uploading just to be turned down.
  const dataTooBig = Boolean(dataFile) && dataFile.size > MAX_SAVED_DATA_BYTES;

  let saved;
  try {
    const formData = new FormData();
    formData.append("access_level", accessLevel);
    if (dataFile && !dataTooBig) formData.append("data_file", dataFile);
    const res = await fetch(`/datasets/${encodeURIComponent(datasetName)}/runs/${run.id}/knowledge-report`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `Server returned ${res.status}`);
    }
    saved = await res.json();
  } catch (err) {
    setReportStatus(`The check ran, but it wasn't saved to Knowledge Search: ${err.message}`, "error");
    return;
  }

  if (dataTooBig) {
    saved.data_error = `The data file is over ${MAX_SAVED_DATA_BYTES / (1024 * 1024)} MB, so only the report was saved.`;
  }
  setReportStatus(describeSavedReport(saved), saved.data_error ? "error" : "ok");
  await loadDocuments();
}

// ---- Upload limits: turn a too-big file away before it is sent ----
//
// The server enforces the limits (app/limits.py) and publishes them at GET
// /limits; this page asks, so its check always matches the server's. The
// numbers below hold only until that answer arrives - or if it never does.

let uploadLimits = { max_upload_bytes: 25 * 1024 * 1024, max_upload_mb: 25, max_check_rows: 1000000 };

function formatMegabytes(bytes) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// What to tell the person about a file over the size limit, or "" if it is fine.
function tooBigMessage(file) {
  if (!file || file.size <= uploadLimits.max_upload_bytes) return "";
  return `${file.name} is ${formatMegabytes(file.size)} — the limit is ${uploadLimits.max_upload_mb} MB per file.`;
}

// For a file picker's change handler: a file over the limit is un-chosen and the
// person is told why (true); a file that is fine clears an old complaint.
function turnAwayIfTooBig(input, statusEl, advice = "") {
  const message = tooBigMessage(input.files[0]);
  if (!message) {
    if (statusEl.classList.contains("form-status-error")) {
      statusEl.classList.remove("form-status-error");
      statusEl.textContent = "";
    }
    return false;
  }
  input.value = "";
  statusEl.classList.add("form-status-error");
  statusEl.textContent = `${message} ${advice}`.trim();
  return true;
}

function renderUploadLimitHint() {
  const el = document.getElementById("upload-limits");
  if (!el) return;
  el.textContent = `CSV files up to ${uploadLimits.max_upload_mb} MB or ${uploadLimits.max_check_rows.toLocaleString()} rows.`;
}

async function loadUploadLimits() {
  try {
    const res = await fetch("/limits");
    if (!res.ok) return;
    uploadLimits = { ...uploadLimits, ...(await res.json()) };
    renderUploadLimitHint();
  } catch {
    // The numbers above are right unless the server was set up with different limits.
  }
}

// ---- Try a sample: a made-up business dataset, checked in one click ----
//
// The server builds a random one (app/sample_datasets.py) and returns the CSV
// with its first rows. The CSV goes through the same form and the same check as
// a file the person picked, so a sample run is a real run. The bar under the
// form then offers the data itself: the first rows as a table, or all of it as
// a download.

const SAMPLE_BUTTON_LABEL = "Try a sample";

// Fills in the dataset label box the way typing it would.
function setDatasetLabel(name) {
  document.getElementById("dataset-name").value = name;
  const chipLabel = document.getElementById("dataset-chip-label");
  chipLabel.textContent = name;
  chipLabel.removeAttribute("data-empty");
  document.getElementById("dataset-field").open = false;
}

// Puts a File in a file picker, as if it had been chosen there.
function chooseFile(input, file) {
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function renderSampleTable(sample) {
  const table = document.getElementById("sample-table");
  table.textContent = "";

  const headRow = table.createTHead().insertRow();
  for (const name of sample.columns) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = name;
    headRow.appendChild(th);
  }

  const body = table.createTBody();
  for (const row of sample.preview_rows) {
    const tr = body.insertRow();
    for (const value of row) {
      const td = tr.insertCell();
      if (value === "") {
        // Spelled out: a blank cell is exactly what the Missing Values check is about.
        td.className = "is-blank";
        td.textContent = "blank";
      } else {
        td.textContent = value;
      }
    }
  }

  const shown = sample.preview_rows.length;
  document.getElementById("sample-data-note").textContent =
    shown < sample.row_count
      ? `Showing the first ${shown.toLocaleString()} of ${sample.row_count.toLocaleString()} rows. Download CSV has all of them.`
      : `All ${shown.toLocaleString()} rows.`;
}

function initSampleButton({ form, fileInput, statusEl, runBtn }) {
  const sampleBtn = document.getElementById("sample-btn");
  const bar = document.getElementById("sample-bar");
  const viewBtn = document.getElementById("sample-view-btn");
  const panel = document.getElementById("sample-data-panel");
  const downloadLink = document.getElementById("sample-download");

  let active = null; // the sample whose results are on screen (or being checked), with its File
  let lastKind = ""; // so the next click never gives the same kind again
  let downloadUrl = "";
  let restoreFocus = false; // the click came with the button focused: put focus back when the run ends

  function setViewOpen(open) {
    viewBtn.setAttribute("aria-expanded", String(open));
    viewBtn.textContent = open ? "Hide data" : "View data";
    panel.hidden = !open;
  }

  function hideBar() {
    active = null;
    bar.hidden = true;
    setViewOpen(false);
  }

  // A disabled button drops focus; hand it back so keyboard users can go again.
  function giveFocusBack() {
    const lost = !document.activeElement || document.activeElement === document.body;
    if (restoreFocus && lost) sampleBtn.focus({ preventScroll: true });
    restoreFocus = false;
  }

  // On a phone the score is below the fold when the check finishes: scroll just
  // far enough to show the sample note with the score under it.
  function bringResultsIntoView() {
    const score = document.getElementById("scorecard");
    if (!score || score.getBoundingClientRect().top < window.innerHeight - 160) return;
    const top = window.scrollY + bar.getBoundingClientRect().top - 16;
    window.scrollTo({ top, behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }

  function showBar(sample) {
    document.getElementById("sample-title").textContent = sample.title;
    document.getElementById("sample-size").textContent =
      `${sample.row_count.toLocaleString()} rows × ${sample.columns.length} columns`;
    document.getElementById("sample-desc").textContent = sample.description;

    if (downloadUrl) URL.revokeObjectURL(downloadUrl);
    downloadUrl = URL.createObjectURL(new Blob([sample.csv], { type: "text/csv" }));
    downloadLink.href = downloadUrl;
    downloadLink.download = sample.filename;

    renderSampleTable(sample);
    setViewOpen(false);
    bar.hidden = false;
    bringResultsIntoView();
  }

  async function fetchSample() {
    const res = await fetch(`/samples/random?exclude=${encodeURIComponent(lastKind)}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `Server returned ${res.status}`);
    }
    return res.json();
  }

  sampleBtn.addEventListener("click", async () => {
    restoreFocus = document.activeElement === sampleBtn;
    sampleBtn.disabled = true;
    sampleBtn.textContent = "Getting a sample…";
    statusEl.classList.remove("form-status-error");
    statusEl.textContent = "";
    hideBar();

    try {
      const sample = await fetchSample();
      const file = new File([sample.csv], sample.filename, { type: "text/csv" });
      lastKind = sample.kind;
      active = { ...sample, file };

      setDatasetLabel(sample.dataset_name);
      chooseFile(fileInput, file);
      // The same submit as pressing Run check: validation, progress, results and
      // "save to Knowledge Search" all behave exactly as they do for a chosen file.
      if (form.requestSubmit) form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
    } catch (err) {
      active = null;
      statusEl.classList.add("form-status-error");
      statusEl.textContent = `Couldn't load a sample: ${err.message}`;
    } finally {
      sampleBtn.textContent = SAMPLE_BUTTON_LABEL;
      sampleBtn.disabled = runBtn.disabled;
      if (!runBtn.disabled) giveFocusBack(); // no run started (the sample couldn't be loaded)
    }
  });

  viewBtn.addEventListener("click", () => setViewOpen(panel.hidden));

  return {
    isSample: (file) => Boolean(active) && active.file === file,
    // A run begins: anything but this sample's own run clears the sample bar,
    // and a check in progress keeps a second one from being started.
    runStarted(file) {
      if (!(active && active.file === file)) hideBar();
      sampleBtn.disabled = true;
    },
    runSucceeded(file) {
      if (active && active.file === file) showBar(active);
    },
    runEnded() {
      sampleBtn.disabled = false;
      giveFocusBack();
    },
    // The results on screen belong to another dataset label now.
    labelChanged(name) {
      if (active && active.dataset_name !== name) hideBar();
    },
  };
}

function initUploadForm() {
  const form = document.getElementById("upload-form");
  const fileInput = document.getElementById("file-input");
  const fileLabel = document.getElementById("file-btn-label");
  const statusEl = document.getElementById("form-status");
  const btn = document.getElementById("run-btn");

  initDatasetField();
  initRelationshipsField();
  initSaveReportOption();
  const sampleUi = initSampleButton({ form, fileInput, statusEl, runBtn: btn });

  fileInput.addEventListener("change", () => {
    turnAwayIfTooBig(fileInput, statusEl, "Try a smaller extract of it, or a sample.");
    fileLabel.textContent = fileInput.files.length ? fileInput.files[0].name : "Choose CSV";
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    statusEl.textContent = "";
    statusEl.classList.remove("form-status-error");

    setReportStatus("", "ok");

    const name = document.getElementById("dataset-name").value.trim();
    if (!name || !fileInput.files.length) {
      statusEl.classList.add("form-status-error");
      statusEl.textContent = "Pick a dataset label and a CSV file first.";
      return;
    }

    // Read the options now: what was ticked when Run was pressed is what this
    // run saves, even if the box is changed while the check is running. The
    // file is held here too - the input is cleared as soon as the run succeeds.
    const reportOptions = getReportOptions();
    const dataFile = fileInput.files[0];
    const isSampleRun = sampleUi.isSample(dataFile); // a sample has no related files to check against

    sampleUi.runStarted(dataFile);
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Running…";

    try {
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      if (!isSampleRun) appendRelationships(formData);
      const res = await fetch(`/datasets/${encodeURIComponent(name)}/runs`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Server returned ${res.status}`);
      }

      const created = await res.json();

      fileInput.value = "";
      fileLabel.textContent = "Choose CSV";
      if (!isSampleRun) {
        document.getElementById("relationships-list").innerHTML = "";
        renderRelationshipResult(created.relationship_feedback);
      }

      statusEl.classList.remove("form-status-error");
      statusEl.textContent = summarizeRun(created);

      await loadDataset(name);
      sampleUi.runSucceeded(dataFile);

      if (reportOptions.enabled) {
        btn.textContent = "Saving report…";
        await saveRunReport(name, created, reportOptions.accessLevel, dataFile);
      }
    } catch (err) {
      statusEl.classList.add("form-status-error");
      statusEl.textContent = `Run failed: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
      sampleUi.runEnded();
    }
  });

  document.getElementById("dataset-name").addEventListener("change", (event) => {
    const name = event.target.value.trim();
    sampleUi.labelChanged(name);
    if (name) {
      loadDataset(name);
    } else {
      renderNoLabelPrompt();
    }
  });
}

function levelBadge(level) {
  const cls = level === "INFO" ? "level-info" : level === "WARN" ? "level-warn" : "level-error";
  return `<span class="level-badge ${cls}">${escapeHtml(level)}</span>`;
}

function renderLogsList(logs) {
  const el = document.getElementById("logs-list");
  if (!logs.length) {
    el.innerHTML = `<p class="empty-copy">No logs ingested yet.</p>`;
    return;
  }
  el.innerHTML = logs
    .slice(0, 30)
    .map(
      (l) => `
        <div class="log-row">
          <span class="log-timestamp">${formatTimestamp(l.timestamp, true)}</span>
          ${levelBadge(l.level)}
          <span class="log-service">${escapeHtml(l.service)}</span>
          <span class="log-message">${escapeHtml(l.message)}</span>
        </div>`
    )
    .join("");
}

function renderMetricsList(metrics) {
  const el = document.getElementById("metrics-list");
  if (!metrics.length) {
    el.innerHTML = `<p class="empty-copy">No metrics ingested yet.</p>`;
    return;
  }
  el.innerHTML = metrics
    .slice(0, 30)
    .map(
      (m) => `
        <div class="metric-row">
          <span class="log-timestamp">${formatTimestamp(m.timestamp, true)}</span>
          <span class="metric-name">${escapeHtml(m.metric_name)}</span>
          <span class="log-service">${escapeHtml(m.service)}</span>
          <span class="metric-value">${m.value.toLocaleString()}</span>
        </div>`
    )
    .join("");
}

function renderAnomaliesList(anomalies) {
  const el = document.getElementById("anomalies-list");
  if (!anomalies.length) {
    el.innerHTML = `<p class="empty-copy">No anomalies detected — every metric is within its normal range.</p>`;
    return;
  }
  el.innerHTML = anomalies
    .slice(0, 30)
    .reverse() // most recent anomaly first, matching the logs/metrics panels
    .map(
      (a) => `
        <div class="anomaly-row">
          <span class="log-timestamp">${formatTimestamp(a.timestamp, true)}</span>
          <span class="metric-name">${escapeHtml(a.metric_name)}</span>
          <span class="log-service">${escapeHtml(a.service)}</span>
          <span class="anomaly-detail" title="value ${a.value.toLocaleString()} · baseline ${a.baseline_mean.toLocaleString()} ± ${a.baseline_stdev.toLocaleString()}">
            <span class="z-score-badge">z=${a.z_score}</span>
          </span>
        </div>`
    )
    .join("");
}

async function loadAnomalies() {
  const el = document.getElementById("anomalies-list");
  el.innerHTML = `<p class="loading">Loading…</p>`;
  const service = getIncidentServiceFilter();
  const url = service ? `/incidents/anomalies?service=${encodeURIComponent(service)}` : "/incidents/anomalies";
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    renderAnomaliesList(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="error">Couldn't load anomalies (${escapeHtml(err.message)}).</p>`;
  }
}

function renderLogBurstsList(bursts) {
  const el = document.getElementById("log-bursts-list");
  if (!bursts.length) {
    el.innerHTML = `<p class="empty-copy">No error bursts detected.</p>`;
    return;
  }
  el.innerHTML = bursts
    .slice(0, 30)
    .reverse() // most recent burst first, matching the other panels
    .map(
      (b) => `
        <div class="anomaly-row">
          <span class="log-timestamp">${formatTimestamp(b.timestamp, true)}</span>
          <span class="metric-name">${Math.round(b.value)}/min</span>
          <span class="log-service">${escapeHtml(b.service)}</span>
          <span class="anomaly-detail" title="baseline ${b.baseline_mean.toLocaleString()} ± ${b.baseline_stdev.toLocaleString()}">
            <span class="z-score-badge">z=${b.z_score}</span>
          </span>
        </div>`
    )
    .join("");
}

async function loadLogBursts() {
  const el = document.getElementById("log-bursts-list");
  el.innerHTML = `<p class="loading">Loading…</p>`;
  const service = getIncidentServiceFilter();
  const url = service ? `/incidents/log-bursts?service=${encodeURIComponent(service)}` : "/incidents/log-bursts";
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    renderLogBurstsList(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="error">Couldn't load error bursts (${escapeHtml(err.message)}).</p>`;
  }
}

// A compact per-service timeline: one horizontal track per service, with a
// dot for every signal positioned by when it happened relative to the
// incident's start/end - the cascade shape (one service's dots starting,
// another service's dots following minutes later) is immediately visible
// here in a way a scrollable row list can't show at a glance.
function renderIncidentTimelineSvg(incident) {
  const rowHeight = 26;
  const labelWidth = 132;
  const width = 640;
  const trackRight = width - 12;
  const topPadding = 10;
  const height = incident.services.length * rowHeight + topPadding;

  const start = new Date(incident.started_at).getTime();
  const end = new Date(incident.ended_at).getTime();
  const span = Math.max(end - start, 1); // avoid divide-by-zero when every signal shares one timestamp
  const rootCauseService = incident.root_cause.length > 1 ? incident.root_cause[0].service : null;

  const rows = incident.services
    .map((service, i) => {
      const y = topPadding + i * rowHeight + rowHeight / 2;
      const isRootCause = service === rootCauseService;
      const dots = incident.signals
        .filter((s) => s.service === service)
        .map((s) => {
          const t = new Date(s.timestamp).getTime();
          const x = labelWidth + ((t - start) / span) * (trackRight - labelWidth);
          const color = isRootCause ? "var(--fail)" : "var(--accent)";
          const tooltip = `${service} · ${s.metric_name} · ${s.signal_type === "log_burst" ? "log burst" : "metric"} · z=${s.z_score} · ${formatTimestamp(s.timestamp, true)}`;
          return `<circle cx="${x.toFixed(1)}" cy="${y}" r="4" fill="${color}"><title>${escapeHtml(tooltip)}</title></circle>`;
        })
        .join("");

      return `
        <text x="0" y="${y + 4}" class="timeline-label${isRootCause ? " timeline-label-root-cause" : ""}">${escapeHtml(service)}</text>
        <line x1="${labelWidth}" y1="${y}" x2="${trackRight}" y2="${y}" class="timeline-track" />
        ${dots}`;
    })
    .join("");

  return `<svg viewBox="0 0 ${width} ${height}" class="incident-timeline-svg" role="img" aria-label="Signal timeline for this incident">${rows}</svg>`;
}

function incidentActionsHtml(incident) {
  if (incident.status === "resolved") {
    const note = incident.resolution_note
      ? `<p class="resolution-note">&ldquo;${escapeHtml(incident.resolution_note)}&rdquo;</p>`
      : "";
    return `
      <div class="incident-actions">
        <span class="status-badge status-resolved">resolved</span>
        ${note}
        <button type="button" class="incident-action-btn" data-action="reopen" data-incident-id="${incident.id}">Reopen</button>
      </div>`;
  }

  const statusBadgeCls = incident.status === "acknowledged" ? "status-acknowledged" : "status-open";
  const ackBtn =
    incident.status === "open"
      ? `<button type="button" class="incident-action-btn" data-action="acknowledge" data-incident-id="${incident.id}">Acknowledge</button>`
      : "";

  return `
    <div class="incident-actions">
      <span class="status-badge ${statusBadgeCls}">${escapeHtml(incident.status)}</span>
      ${ackBtn}
      <button type="button" class="incident-action-btn resolve-toggle-btn" data-incident-id="${incident.id}">Resolve</button>
      <form class="resolve-form" data-incident-id="${incident.id}" hidden>
        <input type="text" class="resolve-note-input" placeholder="Resolution note (optional)" />
        <button type="submit" class="resolve-confirm-btn">Confirm resolve</button>
      </form>
    </div>`;
}

function renderCorrelatedIncidents(incidents) {
  const el = document.getElementById("correlated-incidents-list");
  if (!incidents.length) {
    el.innerHTML = `<p class="empty-copy">No incidents detected — nothing anomalous to correlate yet.</p>`;
    return;
  }

  // Most severe incidents first, then the more signal-rich ones within a
  // severity tier - critical/warning/info is a stronger sort key than raw
  // signal count, since a huge pile of noise blips shouldn't outrank a
  // real critical incident.
  const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 };
  const sorted = [...incidents].sort((a, b) => {
    const severityDiff = SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity];
    return severityDiff !== 0 ? severityDiff : b.signal_count - a.signal_count;
  });

  el.innerHTML = sorted
    .map((incident) => {
      // Root cause is only a meaningful call when there's more than one
      // service to choose between - a singleton incident has nothing to
      // rank against.
      const topCause = incident.root_cause.length > 1 ? incident.root_cause[0] : null;

      const severityCls =
        incident.severity === "critical" ? "access-confidential" : incident.severity === "warning" ? "access-team" : "access-public";
      const severityBadge = `<span class="access-badge ${severityCls}">${escapeHtml(incident.severity)}</span>`;

      const serviceBadges = incident.services
        .map((s) => `<span class="source-badge">${escapeHtml(s)}${s === topCause?.service ? " (root cause)" : ""}</span>`)
        .join("");

      const rootCauseCallout = topCause
        ? `<p class="root-cause-callout">
             <span class="root-cause-label">Likely root cause:</span>
             <span class="root-cause-service">${escapeHtml(topCause.service)}</span>
             <span class="root-cause-reason">started first &middot; peak z=${topCause.max_z_score}</span>
           </p>`
        : "";

      const signalRows = incident.signals
        .map(
          (s) => `
            <div class="anomaly-row">
              <span class="log-timestamp">${formatTimestamp(s.timestamp, true)}</span>
              <span class="metric-name">${escapeHtml(s.metric_name)}</span>
              <span class="log-service">${escapeHtml(s.service)}</span>
              <span class="anomaly-detail">
                <span class="source-badge">${s.signal_type === "log_burst" ? "log burst" : "metric"}</span>
                <span class="z-score-badge">z=${s.z_score}</span>
              </span>
            </div>`
        )
        .join("");

      const timeline = incident.signal_count > 1 ? renderIncidentTimelineSvg(incident) : "";

      return `
        <div class="incident-card">
          <div class="incident-card-head">
            ${severityBadge}
            <span class="incident-time-range">${formatTimestamp(incident.started_at, true)} &rarr; ${formatTimestamp(incident.ended_at, true)}</span>
            ${serviceBadges}
            <span class="incident-signal-count">${incident.signal_count} signal${incident.signal_count === 1 ? "" : "s"}</span>
          </div>
          ${rootCauseCallout}
          ${timeline}
          ${incidentActionsHtml(incident)}
          <button type="button" class="view-history-btn incident-toggle-btn" data-incident-id="${incident.id}">View signals</button>
          <div class="incident-signals" hidden>${signalRows}</div>
        </div>`;
    })
    .join("");
}

async function loadCorrelatedIncidents() {
  const el = document.getElementById("correlated-incidents-list");
  el.innerHTML = `<p class="loading">Loading…</p>`;
  try {
    const res = await fetch("/incidents/correlated");
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    renderCorrelatedIncidents(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="error">Couldn't load incidents (${escapeHtml(err.message)}).</p>`;
  }
}

function renderIncidentHistorySummary(summary) {
  const el = document.getElementById("incident-history-summary");
  const counts = summary.counts_by_status;
  const mttr = summary.mean_time_to_resolution_minutes;

  el.innerHTML = `
    <div class="history-summary-stats">
      <div class="history-stat"><span class="history-stat-number">${summary.total_incidents}</span><span class="history-stat-label">Total tracked</span></div>
      <div class="history-stat"><span class="history-stat-number">${counts.open || 0}</span><span class="history-stat-label">Open</span></div>
      <div class="history-stat"><span class="history-stat-number">${counts.acknowledged || 0}</span><span class="history-stat-label">Acknowledged</span></div>
      <div class="history-stat"><span class="history-stat-number">${counts.resolved || 0}</span><span class="history-stat-label">Resolved</span></div>
      <div class="history-stat"><span class="history-stat-number">${mttr != null ? mttr + "m" : "—"}</span><span class="history-stat-label">Mean time to resolution</span></div>
    </div>`;
}

async function loadIncidentHistorySummary() {
  const el = document.getElementById("incident-history-summary");
  el.innerHTML = `<p class="loading">Loading…</p>`;
  try {
    const res = await fetch("/incidents/history/summary");
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    renderIncidentHistorySummary(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="error">Couldn't load incident history (${escapeHtml(err.message)}).</p>`;
  }
}

async function postIncidentAction(incidentId, action, body) {
  const res = await fetch(`/incidents/correlated/${incidentId}/${action}`, {
    method: "POST",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`Server returned ${res.status}`);
  await loadCorrelatedIncidents();
  await loadIncidentHistorySummary();
}

function initCorrelatedIncidentsList() {
  const el = document.getElementById("correlated-incidents-list");

  el.addEventListener("click", async (event) => {
    const toggleBtn = event.target.closest(".incident-toggle-btn");
    if (toggleBtn) {
      const panel = toggleBtn.nextElementSibling;
      panel.hidden = !panel.hidden;
      toggleBtn.textContent = panel.hidden ? "View signals" : "Hide signals";
      return;
    }

    const resolveToggleBtn = event.target.closest(".resolve-toggle-btn");
    if (resolveToggleBtn) {
      const form = resolveToggleBtn.nextElementSibling;
      form.hidden = !form.hidden;
      return;
    }

    const actionBtn = event.target.closest(".incident-action-btn[data-action]");
    if (actionBtn) {
      try {
        await postIncidentAction(actionBtn.dataset.incidentId, actionBtn.dataset.action);
      } catch (err) {
        alert(`Couldn't update the incident (${err.message}).`);
      }
    }
  });

  el.addEventListener("submit", async (event) => {
    const form = event.target.closest(".resolve-form");
    if (!form) return;
    event.preventDefault();
    const note = form.querySelector(".resolve-note-input").value.trim();
    try {
      await postIncidentAction(form.dataset.incidentId, "resolve", { note: note || null });
    } catch (err) {
      alert(`Couldn't resolve the incident (${err.message}).`);
    }
  });
}

function getIncidentServiceFilter() {
  return document.getElementById("incident-service-select")?.value || "";
}

async function loadIncidentServices() {
  const select = document.getElementById("incident-service-select");
  try {
    const res = await fetch("/incidents/services");
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const services = await res.json();
    const current = select.value;
    select.innerHTML =
      `<option value="">All services</option>` +
      services.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
    if (services.includes(current)) select.value = current;
  } catch (err) {
    // Non-critical - the filter just stays at "All services" if this fails.
  }
}

async function loadLogs() {
  const el = document.getElementById("logs-list");
  el.innerHTML = `<p class="loading">Loading…</p>`;
  const service = getIncidentServiceFilter();
  const url = service ? `/incidents/logs?service=${encodeURIComponent(service)}` : "/incidents/logs";
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    renderLogsList(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="error">Couldn't load logs (${escapeHtml(err.message)}).</p>`;
  }
}

async function loadMetrics() {
  const el = document.getElementById("metrics-list");
  el.innerHTML = `<p class="loading">Loading…</p>`;
  const service = getIncidentServiceFilter();
  const url = service ? `/incidents/metrics?service=${encodeURIComponent(service)}` : "/incidents/metrics";
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    renderMetricsList(await res.json());
  } catch (err) {
    el.innerHTML = `<p class="error">Couldn't load metrics (${escapeHtml(err.message)}).</p>`;
  }
}

function initIncidentServiceFilter() {
  document.getElementById("incident-service-select").addEventListener("change", () => {
    loadLogs();
    loadMetrics();
    loadAnomalies();
    loadLogBursts();
  });
}

function initRunIncidentAnalysisButton() {
  const btn = document.getElementById("run-incident-analysis-btn");
  const statusEl = document.getElementById("run-incident-analysis-status");

  btn.addEventListener("click", async () => {
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Running…";
    statusEl.classList.remove("run-status-error");
    statusEl.textContent = "";

    try {
      // A direct check against the pipeline's own end result - if this
      // fails, something in detection/correlation actually broke, which
      // is exactly what "did the run go through" needs to know. The
      // load*() calls below have their own internal error handling per
      // panel and don't reject, so they can't answer that on their own.
      const res = await fetch("/incidents/correlated");
      if (!res.ok) throw new Error(`Server returned ${res.status}`);

      await loadIncidentServices();
      await loadLogs();
      await loadMetrics();
      await loadAnomalies();
      await loadLogBursts();
      await loadCorrelatedIncidents();
      await loadIncidentHistorySummary();

      statusEl.textContent = "✓ Analysis ran successfully.";
    } catch (err) {
      statusEl.classList.add("run-status-error");
      statusEl.textContent = `✗ Analysis failed: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  });
}

function initDeleteIncidentLogsButton() {
  document.getElementById("delete-incident-logs-btn").addEventListener("click", async () => {
    if (!confirm("Are you sure you want to delete all logs? This cannot be recovered.")) return;

    try {
      const res = await fetch("/incidents/logs", { method: "DELETE" });
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      await loadIncidentServices();
      await loadLogs();
      await loadLogBursts();
      await loadCorrelatedIncidents();
      await loadIncidentHistorySummary();
    } catch (err) {
      alert(`Couldn't delete logs (${err.message}).`);
    }
  });
}

function initLogsUploadForm() {
  const form = document.getElementById("logs-upload-form");
  const fileInput = document.getElementById("logs-file-input");
  const fileLabel = document.getElementById("logs-file-label");
  const statusEl = document.getElementById("logs-upload-status");
  const btn = document.getElementById("logs-upload-btn");

  fileInput.addEventListener("change", () => {
    turnAwayIfTooBig(fileInput, statusEl, "Try a shorter time window.");
    fileLabel.textContent = fileInput.files.length ? fileInput.files[0].name : "Choose logs CSV";
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    statusEl.classList.remove("form-status-error");
    statusEl.textContent = "";

    if (!fileInput.files.length) {
      statusEl.classList.add("form-status-error");
      statusEl.textContent = "Choose a file first.";
      return;
    }

    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Uploading…";

    try {
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      const res = await fetch("/incidents/logs/upload", { method: "POST", body: formData });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Server returned ${res.status}`);
      }

      const summary = await res.json();
      fileInput.value = "";
      fileLabel.textContent = "Choose logs CSV";
      statusEl.textContent = `✓ Ingested ${summary.rows_ingested.toLocaleString()} log rows across ${summary.services.length} service(s).`;
      await loadIncidentServices();
      await loadLogs();
      await loadLogBursts();
      await loadCorrelatedIncidents();
      await loadIncidentHistorySummary();
    } catch (err) {
      statusEl.classList.add("form-status-error");
      statusEl.textContent = `Upload failed: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  });
}

function initMetricsUploadForm() {
  const form = document.getElementById("metrics-upload-form");
  const fileInput = document.getElementById("metrics-file-input");
  const fileLabel = document.getElementById("metrics-file-label");
  const statusEl = document.getElementById("metrics-upload-status");
  const btn = document.getElementById("metrics-upload-btn");

  fileInput.addEventListener("change", () => {
    turnAwayIfTooBig(fileInput, statusEl, "Try a shorter time window.");
    fileLabel.textContent = fileInput.files.length ? fileInput.files[0].name : "Choose metrics CSV";
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    statusEl.classList.remove("form-status-error");
    statusEl.textContent = "";

    if (!fileInput.files.length) {
      statusEl.classList.add("form-status-error");
      statusEl.textContent = "Choose a file first.";
      return;
    }

    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Uploading…";

    try {
      const formData = new FormData();
      formData.append("file", fileInput.files[0]);
      const res = await fetch("/incidents/metrics/upload", { method: "POST", body: formData });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Server returned ${res.status}`);
      }

      const summary = await res.json();
      fileInput.value = "";
      fileLabel.textContent = "Choose metrics CSV";
      statusEl.textContent = `✓ Ingested ${summary.rows_ingested.toLocaleString()} metric rows across ${summary.services.length} service(s).`;
      await loadIncidentServices();
      await loadMetrics();
      await loadAnomalies();
      await loadCorrelatedIncidents();
      await loadIncidentHistorySummary();
    } catch (err) {
      statusEl.classList.add("form-status-error");
      statusEl.textContent = `Upload failed: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  });
}

initPageViews();
initUploadForm();
initScrollReveal();
initDemoIntro();
initHeroQuoteRotator();
initDocumentUploadForm();
initDocumentSearchForm();
initDocumentsList();
initDocumentViewer();
// A refresh always starts locked (public documents open, the rest locked until
// their code is typed in again), so the list can load straight away.
initAccessPanel();
loadDocuments();
loadAnalytics();
initDeleteSearchLogsButton();
initLogsUploadForm();
initMetricsUploadForm();
initIncidentServiceFilter();
initRunIncidentAnalysisButton();
initDeleteIncidentLogsButton();
loadIncidentServices();
loadLogs();
loadMetrics();
loadAnomalies();
loadLogBursts();
initCorrelatedIncidentsList();
// History summary reflects records that /incidents/correlated persists as
// a side effect - it must run after that call resolves, not in parallel,
// or it can read the incidents table before those rows exist.
loadCorrelatedIncidents().then(loadIncidentHistorySummary);
initScrollToUpload();
loadUploadLimits();

const initialName = document.getElementById("dataset-name").value.trim();
if (initialName) {
  loadDataset(initialName);
} else {
  renderNoLabelPrompt();
}
