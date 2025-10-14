// static/script.js

const form = document.getElementById("caption-form");
const urlInput = document.getElementById("youtube-url");

const statusBox = document.getElementById("status-container");
const statusMsg = document.getElementById("status-message");
const progressBar = document.getElementById("progress-bar");

const resultBox = document.getElementById("result-container");
const resultMsg = document.getElementById("result-message");
const downloadLink = document.getElementById("download-link");

function showStatus(msg, progressPct = null) {
  statusBox.classList.remove("hidden");
  statusMsg.textContent = msg || "";
  if (progressPct == null) return;
  progressBar.style.width = `${Math.max(0, Math.min(100, progressPct))}%`;
}

function hideStatus() {
  statusBox.classList.add("hidden");
  statusMsg.textContent = "";
  progressBar.style.width = "0%";
}

function showResult(msg) {
  resultBox.classList.remove("hidden");
  resultMsg.textContent = msg || "";
}

function hideResult() {
  resultBox.classList.add("hidden");
  resultMsg.textContent = "";
  downloadLink.classList.add("hidden");
  downloadLink.removeAttribute("href");
}

async function postForm(url, data) {
  const body = new URLSearchParams();
  for (const [k, v] of Object.entries(data)) body.append(k, v);
  const res = await fetch(url, { method: "POST", body });
  const json = await res.json();
  if (!res.ok) {
    const msg = json && json.message ? json.message : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return json;
}

function setDownloadFromFilePath(resp) {
  // Prefer server-provided URL if present (see backend patch below)
  if (resp.download_url) {
    downloadLink.href = resp.download_url;
    downloadLink.textContent = "Download Subtitle File";
    downloadLink.classList.remove("hidden");
    return;
  }

  // Fallback: derive from file_path
  let filePath = resp.file_path || "";
  // Normalize Windows backslashes -> forward slashes
  filePath = filePath.replace(/\\/g, "/");

  // Expecting: "temp/<session>/<file>.srt"
  // Use a regex so we’re resilient to extra slashes
  const m = filePath.match(/^temp\/([^/]+)\/(.+\.srt)$/i);
  if (!m) {
    // Last-ditch graceful fallback: don't crash UI
    downloadLink.classList.add("hidden");
    console.error("Unexpected file_path format:", resp.file_path);
    return;
  }

  const sessionId = m[1];
  const nameOnly = m[2]; // may contain subfolders, but typically just "<file>.srt"

  downloadLink.href = `/download/${encodeURIComponent(
    sessionId
  )}/${encodeURIComponent(nameOnly)}`;
  downloadLink.textContent = "Download Subtitle File";
  downloadLink.classList.remove("hidden");
}

function renderISLVocab(matches, counts) {
  const box = document.getElementById("isl-vocab-container");
  const grid = document.getElementById("isl-vocab");
  if (!box || !grid) return;

  grid.innerHTML = "";
  if (!matches || !matches.length) {
    box.classList.remove("hidden");
    grid.innerHTML =
      '<div class="vocab-item" style="grid-column:1/-1;">No ISL words found in list.</div>';
    return;
  }
  matches.forEach((w) => {
    const div = document.createElement("div");
    div.className = "vocab-item";
    div.textContent = counts && counts[w] ? `${w} (${counts[w]})` : w;
    grid.appendChild(div);
  });
  box.classList.remove("hidden");
}

async function extractISL(sessionId, filePath) {
  const body = new URLSearchParams();
  body.append("session_id", sessionId);
  if (filePath) body.append("file_path", filePath);
  const res = await fetch("/isl-extract", { method: "POST", body });
  const json = await res.json();
  if (!res.ok) throw new Error(json.message || `HTTP ${res.status}`);
  return json;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  hideResult();
  showStatus("Checking captions…", 10);

  const url = urlInput.value.trim();
  if (!url) {
    showResult("Please paste a YouTube URL.");
    hideStatus();
    return;
  }

  try {
    // Step 1: Ask backend to get/create SRT (process)
    const p = await postForm("/process", { url });
    const sessionId = p.session_id;

    let srtPath = p.file_path; // may be undefined if transcription path required

    if (p.status === "success" && srtPath) {
      // already have SRT from YT
      showStatus("Formatting for written English…", 70);
    } else if (p.status === "info") {
      // need to download audio, then transcribe
      showStatus("Downloading audio…", 40);
      const d = await postForm("/download-audio", {
        url,
        session_id: sessionId,
      });

      showStatus("Transcribing audio…", 60);
      const t = await postForm("/transcribe", {
        audio_path: d.audio_path,
        session_id: sessionId,
      });
      srtPath = t.file_path;

      showStatus("Formatting for written English…", 80);
    } else {
      hideStatus();
      showResult(p.message || "Unexpected response.");
      return;
    }

    // Step 2: Formalize (spoken -> written)
    const f = await postForm("/formalize", {
      session_id: sessionId,
      file_path: srtPath,
    });

    // After you obtain `f` from /formalize:
    try {
      const ex = await extractISL(f.session_id, f.file_path); // analyze the formal file
      renderISLVocab(ex.unique_matches, ex.counts);
      renderAffectedTables(
        ex.affected_present || [],
        ex.affected_absent || [],
        ex.affected_lemmas || []
      );
    } catch (e) {
      console.warn("ISL extract failed:", e);
      renderISLVocab([], {}); // show empty state gracefully
    }

    // After you obtain `session_id` and have a .formal.srt ready, call:
    async function runISLExtract(session_id, file_pathOrFilename = {}) {
      const form = new FormData();
      form.append("session_id", session_id);
      if (file_pathOrFilename.file_path)
        form.append("file_path", file_pathOrFilename.file_path);
      if (file_pathOrFilename.filename)
        form.append("filename", file_pathOrFilename.filename);

      const res = await fetch("/isl-extract", { method: "POST", body: form });
      const data = await res.json();
      if (data.status !== "success")
        throw new Error(data.message || "ISL extract failed");

      // (1) Old grid (optional): data.unique_matches and data.counts
      // renderDetectedISLGrid(data.unique_matches, data.counts);

      // (2) New tables
      renderAffectedTables(
        data.affected_present,
        data.affected_absent,
        data.affected_lemmas
      );

      return data;
    }

    function renderAffectedTables(present, absent, affectedAll) {
      const cont = document.getElementById("affected-container");
      const tbodyP = document.querySelector("#tbl-present tbody");
      const tbodyA = document.querySelector("#tbl-absent tbody");
      const originalsDiv = document.getElementById("affected-originals");
      cont.classList.remove("hidden");
      tbodyP.innerHTML = "";
      tbodyA.innerHTML = "";
      originalsDiv.innerHTML = "";

      // sort by count desc then lemma
      const sortByCount = (a, b) =>
        b.count - a.count || a.lemma.localeCompare(b.lemma);
      [...present].sort(sortByCount).forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${escapeHtml(r.lemma)}</td><td>${r.count}</td>`;
        tbodyP.appendChild(tr);
      });
      [...absent].sort(sortByCount).forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${escapeHtml(r.lemma)}</td><td>${r.count}</td>`;
        tbodyA.appendChild(tr);
      });

      // originals per lemma (nice for debugging)
      const byLemma = {};
      (affectedAll || []).forEach((e) => (byLemma[e.lemma] = e.originals));
      Object.keys(byLemma)
        .sort()
        .forEach((lem) => {
          const originals = byLemma[lem]
            .map((o) => `<code>${escapeHtml(o)} → ${escapeHtml(lem)}</code>`)
            .join(" ");
          const p = document.createElement("p");
          p.innerHTML = originals;
          originalsDiv.appendChild(p);
        });
    }

    function escapeHtml(s) {
      return s.replace(
        /[&<>"']/g,
        (c) =>
          ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
          }[c])
      );
    }

    hideStatus();
    showResult("Ready! This is the formal written-English version.");
    setDownloadFromFilePath(f);
  } catch (err) {
    hideStatus();
    showResult(`Error: ${err.message}`);
  }
});
