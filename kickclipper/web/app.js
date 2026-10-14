/* KickClipper — frontend (vanilla JS, sin build) */
"use strict";

const $ = (id) => document.getElementById(id);
const api = async (path, opts = {}) => {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error((await res.text()).slice(0, 300));
  return res.json();
};

const STAGES = ["downloading", "transcribing", "analyzing", "scoring", "rendering"];
const STAGE_LABEL = {
  queued: "EN COLA", downloading: "DESCARGANDO", transcribing: "TRANSCRIBIENDO",
  analyzing: "ANALIZANDO", scoring: "PUNTUANDO IA", rendering: "RENDERIZANDO",
  done: "LISTO", error: "ERROR", cancelled: "CANCELADO",
};

let jobs = [];
let clips = [];
let selectedJob = null;
let modalClip = null;
let streamerSocials = {};  // {canal: {red: url}} — redes configuradas (opcional)

const FMT_LABEL = { short: "shorts", video: "videos" };
const channelOf = (job) => {
  const m = (job?.url || "").match(/kick\.com\/([A-Za-z0-9_.-]+)/);
  return m ? m[1].toLowerCase() : null;
};

/* ---------------- stack local ---------------- */

async function refreshStatus() {
  try {
    const s = await api("/status");
    setLed("gear-ffmpeg", s.ffmpeg);
    setLed("gear-whisper", true);
    setLed("gear-ollama", s.ollama);
    setLed("gear-vision", s.vision);
    setLed("gear-chat", s.chat_signal);
    $("vision-detail").textContent = s.vision ? "facecam · hablante activo" : "instala opencv-python-headless";
    $("chat-detail").textContent = s.chat_signal ? "picos de mensajes" : "desactivado (KC_CHAT_SIGNAL)";
    $("whisper-detail").textContent = `${s.whisper_engine} · ${s.whisper_model}`;
    $("ollama-detail").textContent = s.ollama
      ? s.ollama_model : "servidor apagado — ejecuta `ollama serve`";
    streamerSocials = s.streamer_socials || {};
  } catch { /* servidor aún arrancando */ }
}

function setLed(gearId, ok) {
  const led = document.querySelector(`#${gearId} .led`);
  led.classList.toggle("on", !!ok);
  led.classList.toggle("off", !ok);
}

/* ---------------- jobs ---------------- */

function fmtDur(sec) {
  if (!sec) return "";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
const fmtT = (s) => fmtDur(Math.round(s)) || "0:00";
const esc = (t) => (t || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function renderJobs() {
  const zone = $("jobs");
  $("jobs-empty").hidden = jobs.length > 0;
  zone.querySelectorAll(".job").forEach((el) => el.remove());

  for (const job of jobs) {
    const el = document.createElement("div");
    el.className = "job" + (selectedJob === job.id ? " selected" : "");
    el.dataset.id = job.id;

    const working = STAGES.includes(job.status) || job.status === "queued";
    const stageIdx = STAGES.indexOf(job.status);
    const stateCls = job.status === "done" ? "done" : job.status === "error" ? "error" : working ? "working" : "";

    el.innerHTML = `
      <div class="job-top">
        ${job.thumbnail ? `<img class="job-thumb" src="${esc(job.thumbnail)}" loading="lazy">` : `<div class="job-thumb"></div>`}
        <div class="job-info">
          <div class="job-title">${esc(job.title || job.url)}</div>
          <div class="job-channel">${esc(job.channel || "")} ${job.duration ? "· " + fmtDur(job.duration) : ""}${job.options?.formats ? " · " + job.options.formats.map((f) => FMT_LABEL[f] || f).join(" + ") : ""}${job.options?.sections ? " · recorte " + esc(job.options.sections) : ""}</div>
        </div>
        <span class="job-state ${stateCls}">${STAGE_LABEL[job.status] || job.status}</span>
        <div class="job-actions">
          ${working ? `<button class="cancel" title="Cancelar">⏹</button>` : ""}
          ${job.status === "error" || job.status === "cancelled" ? `<button class="retry" title="Reintentar">⟳</button>` : ""}
          <button class="del" title="Borrar">✕</button>
        </div>
      </div>
      <div class="job-pipe">
        ${STAGES.map((s, i) => `
          <div class="pipe-step ${stageIdx > i || job.status === "done" ? "donestep" : ""}" data-stage="${s}">
            <div class="fill" style="width:${stageIdx === i ? Math.round((job.stage_progress || 0) * 100) : 0}%"></div>
          </div>`).join("")}
      </div>
      <div class="job-msg ${job.status === "error" ? "error" : ""}" data-msg>${esc(job.error || "")}</div>
    `;

    el.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      selectJob(job.id);
    });
    el.querySelector(".del")?.addEventListener("click", async () => {
      if (!confirm("¿Borrar este trabajo y sus clips?")) return;
      await api(`/jobs/${job.id}`, { method: "DELETE" });
      jobs = jobs.filter((j) => j.id !== job.id);
      if (selectedJob === job.id) { selectedJob = null; clips = []; renderClips(); }
      renderJobs();
    });
    el.querySelector(".cancel")?.addEventListener("click", () => api(`/jobs/${job.id}/cancel`, { method: "POST" }));
    el.querySelector(".retry")?.addEventListener("click", () => api(`/jobs/${job.id}/retry`, { method: "POST" }));
    zone.appendChild(el);
  }
}

async function selectJob(id) {
  selectedJob = id;
  renderJobs();
  clips = await api(`/jobs/${id}/clips`);
  renderClips();
}

/* ---------------- clips ---------------- */

function renderClips() {
  const zone = $("clips-zone");
  const grid = $("clip-grid");
  if (!selectedJob || !clips.length) { zone.hidden = true; return; }
  zone.hidden = false;
  const job = jobs.find((j) => j.id === selectedJob);
  $("clips-title").textContent = `CLIPS · ${(job?.title || "").slice(0, 50) || selectedJob}`;
  $("clips-count").textContent = `${clips.length} detectados`;
  grid.innerHTML = "";

  clips.forEach((clip, i) => {
    const el = document.createElement("div");
    el.className = "clip";
    el.style.animationDelay = `${i * 40}ms`;
    const dur = clip.end - clip.start;
    const fmt = clip.render_options?.format || "short";
    el.innerHTML = `
      <div class="clip-thumb">
        <div class="no-render">SIN RENDER</div>
        <img src="/api/clips/${clip.id}/thumb?t=${clip.status === "rendered" ? Date.now() : "0"}"
             loading="lazy" onerror="this.remove()">
        <span class="clip-score">★ ${Number(clip.score).toFixed(1)}</span>
        <span class="clip-status-chip ${clip.status}">${{ candidate: "candidato", rendering: "render…", rendered: "✓ mp4", error: "error" }[clip.status] || ""}</span>
        <span class="clip-fmt ${fmt}">${fmt === "video" ? "VIDEO 16:9" : "SHORT"}</span>
        <span class="clip-dur">${dur >= 90 ? fmtT(dur) : Math.round(dur) + "s"}</span>
      </div>
      <div class="clip-body">
        <div class="clip-title">${esc(clip.title || clip.transcript?.slice(0, 70) || "Clip sin título")}</div>
        <div class="clip-range">${fmtT(clip.start)} → ${fmtT(clip.end)}</div>
      </div>
    `;
    el.addEventListener("click", () => openModal(clip));
    grid.appendChild(el);
  });
}

/* ---------------- modal ---------------- */

// Descripción lista para pegar: texto del LLM + redes del streamer + hashtags
function publishDesc(clip, channel) {
  const parts = [];
  if (clip.description) parts.push(clip.description);
  const links = Object.entries(
    streamerSocials[channel] || (channel ? { Kick: `https://kick.com/${channel}` } : {})
  );
  if (links.length) {
    parts.push(`Sigue a ${channel} en sus redes:\n` +
      links.map(([red, url]) => `${red}: ${url}`).join("\n"));
  }
  const tags = (clip.hashtags || []).map((h) => `#${h}`).join(" ");
  if (tags) parts.push(tags);
  return parts.join("\n\n");
}

const LAYOUT_LABEL = {
  split: "cámara + gameplay", speaker: "foco en quien habla", follow: "sigue a la persona",
  blur: "fondo difuminado", crop: "recorte central",
};
const SIGNAL_LABEL = { audio: "audio", chat: "chat", hook: "gancho", reactions: "reacciones", density: "habla" };

function renderSignals(clip) {
  const meta = clip.meta || {};
  const sig = meta.signals || {};
  const bars = Object.entries(SIGNAL_LABEL)
    .filter(([k]) => typeof sig[k] === "number")
    .map(([k, label]) => `<div class="sig"><span>${label}</span><i style="--v:${Math.round(sig[k] * 100)}%"></i></div>`)
    .join("");
  const chips = [];
  if (meta.category) chips.push(`<span class="chip">${esc(meta.category)}</span>`);
  const used = clip.render_options?.layout_used;
  if (used) chips.push(`<span class="chip">encuadre: ${esc(LAYOUT_LABEL[used] || used)}</span>`);
  if (meta.llm_scores?.hook !== undefined) {
    const l = meta.llm_scores;
    chips.push(`<span class="chip">IA · gancho ${l.hook} · se entiende ${l.standalone} · remate ${l.payoff}</span>`);
  }
  $("m-signals").innerHTML = (chips.length ? `<div class="chips">${chips.join("")}</div>` : "") + bars;
}

/* ---------------- cámara marcada a mano ---------------- */

let modalCam = null;  // [x, y, w, h] normalizado, null = detección automática

/** Abre el selector sobre una imagen y resuelve con el rectángulo (o null). */
function pickRect(imgSrc) {
  return new Promise((resolve) => {
    const picker = $("cam-picker"), stage = $("cam-stage"), img = $("cam-img"), sel = $("cam-sel");
    picker.hidden = false;
    sel.hidden = true;
    img.src = imgSrc;
    let start = null, rect = null;
    const clamp = (v) => Math.min(Math.max(v, 0), 1);
    const pos = (e) => {
      const b = img.getBoundingClientRect();
      return [clamp((e.clientX - b.left) / b.width), clamp((e.clientY - b.top) / b.height)];
    };
    const draw = () => {
      sel.hidden = false;
      Object.assign(sel.style, { left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${rect[2] * 100}%`, height: `${rect[3] * 100}%` });
    };
    const down = (e) => { e.preventDefault(); start = pos(e); rect = [start[0], start[1], 0, 0]; stage.setPointerCapture(e.pointerId); };
    const move = (e) => {
      if (!start) return;
      const p = pos(e);
      rect = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.abs(p[0] - start[0]), Math.abs(p[1] - start[1])];
      draw();
    };
    const up = () => { start = null; };
    const finish = (value) => {
      stage.removeEventListener("pointerdown", down);
      stage.removeEventListener("pointermove", move);
      stage.removeEventListener("pointerup", up);
      $("cam-ok").onclick = $("cam-cancel").onclick = null;
      picker.hidden = true;
      resolve(value);
    };
    stage.addEventListener("pointerdown", down);
    stage.addEventListener("pointermove", move);
    stage.addEventListener("pointerup", up);
    $("cam-ok").onclick = () => finish(rect && rect[2] > 0.02 && rect[3] > 0.02 ? rect.map((v) => Math.round(v * 1e4) / 1e4) : null);
    $("cam-cancel").onclick = () => finish(undefined);
  });
}

function showCamState() {
  $("m-cam-state").textContent = modalCam ? "cámara marcada a mano ✓" : "cámara: detección automática";
  $("m-cam-clear").hidden = !modalCam;
}

$("m-cam-btn").addEventListener("click", async () => {
  if (!modalClip) return;
  const t = (parseFloat($("m-start").value) + parseFloat($("m-end").value)) / 2;
  const rect = await pickRect(`/api/clips/${modalClip.id}/frame?t=${t.toFixed(2)}&_=${Date.now()}`);
  if (rect === undefined) return;  // cancelado
  modalCam = rect;
  if (rect) $("m-layout").value = "split";
  showCamState();
});
$("m-cam-clear").addEventListener("click", () => { modalCam = null; showCamState(); });

function openModal(clip) {
  modalClip = clip;
  modalCam = clip.render_options?.cam_rect || null;
  showCamState();
  const ro = clip.render_options || {};
  const fmt = ro.format || "short";
  $("m-title").value = clip.title || "";
  $("m-hook").value = ro.hook_text || clip.meta?.hook_text || "";
  $("m-start").value = clip.start;
  $("m-end").value = clip.end;
  $("m-layout").value = ro.layout || "auto";

  const job = jobs.find((j) => j.id === clip.job_id);
  $("m-format").value = fmt;
  $("m-capstyle").value = ro.subtitles === false ? "none" : (ro.caption_style || (fmt === "video" ? "clean" : "viral"));
  $("m-showtitle").checked = (ro.show_hook ?? ro.show_title ?? fmt === "short") !== false;
  $("m-jumpcut").checked = ro.jumpcut !== false && fmt === "short";
  $("m-preview").checked = !!ro.hook_preview;
  $("m-desc").value = publishDesc(clip, channelOf(job));
  renderSignals(clip);
  $("m-reason").textContent = clip.reason || "";
  $("m-tags").innerHTML = (clip.hashtags || []).map((h) => `<span>#${esc(h)}</span>`).join("");
  $("m-progress").hidden = true;
  updateModalVideo(clip);
  $("modal").hidden = false;
}

function updateModalVideo(clip) {
  const video = $("modal-video");
  const ready = clip.status === "rendered" && clip.file;
  $("player-placeholder").hidden = ready;
  video.hidden = !ready;
  $("m-download").hidden = !ready;
  $("m-exported").hidden = !ready;
  if (ready) {
    video.src = `/api/clips/${clip.id}/video?t=${Date.now()}`;
    $("m-download").href = video.src;
    const rel = clip.file.replace(/^.*\/output\//, "output/");
    $("m-exported").textContent = `📁 ${rel}`;
  } else {
    video.removeAttribute("src");
  }
}

function closeModal() {
  $("modal").hidden = true;
  $("modal-video").pause?.();
  modalClip = null;
}

$("modal-close").addEventListener("click", closeModal);
// En videos los subtítulos no son necesarios; en shorts sí por defecto
$("m-format").addEventListener("change", () => {
  const video = $("m-format").value === "video";
  $("m-capstyle").value = video ? "none" : "viral";
  $("m-jumpcut").checked = !video;
  $("m-showtitle").checked = !video;
});

async function copyToClipboard(btn, text) {
  try { await navigator.clipboard.writeText(text); } catch { return; }
  const original = btn.textContent;
  btn.textContent = "✓ copiado";
  setTimeout(() => { btn.textContent = original; }, 1200);
}
$("m-copy-title").addEventListener("click", () => copyToClipboard($("m-copy-title"), $("m-title").value));
$("m-copy-desc").addEventListener("click", () => copyToClipboard($("m-copy-desc"), $("m-desc").value));
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

$("m-render").addEventListener("click", async () => {
  if (!modalClip) return;
  const btn = $("m-render");
  btn.disabled = true;
  $("m-progress").hidden = false;
  $("m-progress-bar").style.width = "2%";
  try {
    const updated = await api(`/clips/${modalClip.id}/render`, {
      method: "POST",
      body: JSON.stringify({
        title: $("m-title").value,
        hook_text: $("m-hook").value.trim(),
        start: parseFloat($("m-start").value),
        end: parseFloat($("m-end").value),
        layout: $("m-layout").value,
        format: $("m-format").value,
        subtitles: $("m-capstyle").value !== "none",
        caption_style: $("m-capstyle").value === "none" ? null : $("m-capstyle").value,
        show_hook: $("m-showtitle").checked,
        jumpcut: $("m-jumpcut").checked,
        hook_preview: $("m-preview").checked,
        ...(modalCam ? { cam_rect: modalCam } : { clear_cam: true }),
      }),
    });
    patchClip(updated);
    if (modalClip && modalClip.id === updated.id) { modalClip = updated; updateModalVideo(updated); renderSignals(updated); }
  } catch (err) {
    alert("Error al renderizar: " + err.message);
  } finally {
    btn.disabled = false;
    $("m-progress").hidden = true;
    renderClips();
  }
});

function patchClip(updated) {
  const i = clips.findIndex((c) => c.id === updated.id);
  if (i >= 0) clips[i] = updated; else clips.push(updated);
}

/* ---------------- formulario ---------------- */

$("job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("go-btn");
  btn.disabled = true;
  try {
    const job = await api("/jobs", {
      method: "POST",
      body: JSON.stringify({
        url: $("url-input").value.trim(),
        sections: $("sections-input").value.trim() || null,
        auto_render: parseInt($("autorender-input").value, 10),
        formats: $("formats-input").value.split(","),
      }),
    });
    // el evento SSE del worker puede llegar antes que esta respuesta:
    // sin esta comprobación el job aparecería duplicado
    if (!jobs.some((j) => j.id === job.id)) jobs.unshift(job);
    selectedJob = job.id;
    clips = [];
    renderJobs();
    renderClips();
    $("url-input").value = "";
  } catch (err) {
    alert("No se pudo crear el trabajo: " + err.message);
  } finally {
    btn.disabled = false;
  }
});

/* ---------------- SSE ---------------- */

function connectSSE() {
  const src = new EventSource("/api/events");
  src.addEventListener("open", () => {
    $("sse-dot").classList.add("on");
    $("sse-label").textContent = "en vivo";
  });
  src.addEventListener("error", () => {
    $("sse-dot").classList.remove("on");
    $("sse-label").textContent = "reconectando…";
  });
  src.addEventListener("update", (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === "job") {
      const i = jobs.findIndex((j) => j.id === ev.job.id);
      if (i >= 0) jobs[i] = ev.job; else jobs.unshift(ev.job);
      renderJobs();
      if (ev.job.id === selectedJob && ev.job.status === "done") selectJob(selectedJob);
    } else if (ev.type === "progress") {
      updateJobProgress(ev);
    } else if (ev.type === "clips" && ev.job_id === selectedJob) {
      clips = ev.clips;
      renderClips();
    } else if (ev.type === "clip") {
      patchClip(ev.clip);
      renderClips();
      if (modalClip && modalClip.id === ev.clip.id) { modalClip = ev.clip; updateModalVideo(ev.clip); }
    } else if (ev.type === "clip_progress" && modalClip && ev.clip_id === modalClip.id) {
      $("m-progress").hidden = false;
      $("m-progress-bar").style.width = `${Math.round(ev.progress * 100)}%`;
      $("m-progress-label").textContent = `${ev.message} ${Math.round(ev.progress * 100)}%`;
    }
  });
}

function updateJobProgress(ev) {
  const job = jobs.find((j) => j.id === ev.job_id);
  if (job) { job.status = ev.stage; job.stage_progress = ev.progress; }
  const el = document.querySelector(`.job[data-id="${ev.job_id}"]`);
  if (!el) { renderJobs(); return; }
  const stageIdx = STAGES.indexOf(ev.stage);
  el.querySelectorAll(".pipe-step").forEach((step, i) => {
    step.classList.toggle("donestep", i < stageIdx);
    step.querySelector(".fill").style.width = i === stageIdx ? `${Math.round(ev.progress * 100)}%` : i < stageIdx ? "100%" : "0%";
  });
  const state = el.querySelector(".job-state");
  state.textContent = STAGE_LABEL[ev.stage] || ev.stage;
  state.className = "job-state working";
  el.querySelector("[data-msg]").textContent = ev.message || "";
}

/* ---------------- arranque ---------------- */

(async function init() {
  refreshStatus();
  setInterval(refreshStatus, 15000);
  connectSSE();
  jobs = await api("/jobs");
  renderJobs();
  if (jobs.length) selectJob(jobs[0].id);
})();
