
    if (window.location.protocol === "file:") {
      window.location.replace("http://127.0.0.1:7860/");
    }

    const healthEl = document.getElementById("health");
    fetch("/api/health")
      .then(r => r.json())
      .then(data => {
        healthEl.textContent = data.success ? "Environment ready" : "Environment incomplete";
        healthEl.style.color = data.success ? "var(--green)" : "var(--amber)";
      })
      .catch(() => {
        healthEl.textContent = "Health check failed";
        healthEl.style.color = "var(--amber)";
      });

    const activeAnimations = new Map();

    function renderResult(el, data, options = {}) {
      if (activeAnimations.has(el.id)) {
        cancelAnimationFrame(activeAnimations.get(el.id));
        activeAnimations.delete(el.id);
      }
      if (!data.success) {
        const log = data.log ? `<pre>${escapeHtml((data.log.stderr || "") + "\n" + (data.log.stdout || ""))}</pre>` : "";
        el.innerHTML = `<div class="error">${escapeHtml(data.error || "Task failed")}</div>${log}`;
        return;
      }
      el.innerHTML = [
        `<div class="ok">Done: ${escapeHtml(data.filename)}</div>`,
        `<div>${escapeHtml(data.output_path || "")}</div>`,
        `<a href="${data.download_url}">Download FBX</a>`
      ].join("");
      if (data.preview) {
        mountPreview(el, data.preview);
      } else if (options.showMissingPreview) {
        const msg = document.createElement("div");
        msg.className = "error";
        msg.textContent = "This result has no playable preview.";
        el.appendChild(msg);
      }
    }

    function renderHistory(items) {
      const list = document.getElementById("historyList");
      if (!items.length) {
        list.textContent = "No history yet.";
        return;
      }
      list.innerHTML = "";
      for (const item of items) {
        const row = document.createElement("div");
        row.className = "history-item";

        const main = document.createElement("div");
        const title = document.createElement("div");
        title.className = "history-title";
        title.textContent = item.label || item.filename || item.task_id;
        const meta = document.createElement("div");
        meta.className = "history-meta";
        const kind = item.kind === "v2m" ? "Video extraction" : "Text to motion";
        const created = item.created_at ? new Date(item.created_at * 1000).toLocaleString() : "";
        meta.textContent = `${kind} 路 ${item.filename || ""} 路 ${created}`;
        main.append(title, meta);

        const actions = document.createElement("div");
        actions.className = "history-actions";
        const view = document.createElement("button");
        view.type = "button";
        view.textContent = "View";
        view.addEventListener("click", () => {
          const target = item.kind === "v2m" ? document.getElementById("v2mResult") : document.getElementById("t2mResult");
          renderResult(target, item, { showMissingPreview: true });
          target.scrollIntoView({ behavior: "smooth", block: "center" });
        });
        const download = document.createElement("a");
        download.href = item.download_url;
        download.textContent = "Download";
        actions.append(view, download);
        row.append(main, actions);
        list.appendChild(row);
      }
    }

    async function refreshHistory() {
      const list = document.getElementById("historyList");
      try {
        const response = await fetch(`/api/history?preview=1&t=${Date.now()}`, { cache: "no-store" });
        const data = await response.json();
        renderHistory(data.items || []);
      } catch (error) {
        list.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
      }
    }
    async function loadLatest() {
      try {
        const response = await fetch(`/api/latest?t=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) return;
        const data = await response.json();
        if (!data.success) return;
        const target = data.kind === "v2m" ? document.getElementById("v2mResult") : document.getElementById("t2mResult");
        renderResult(target, data, { showMissingPreview: true });
      } catch (error) {
        console.warn("latest preview load failed", error);
      }
    }


    function mountPreview(el, preview) {
      if (preview.type === "video") {
        const video = document.createElement("video");
        video.className = "preview";
        video.src = preview.url;
        video.controls = true;
        video.loop = true;
        video.muted = true;
        video.playsInline = true;
        el.appendChild(video);
        return;
      }
      if (preview.type === "skeleton2d") {
        const canvas = document.createElement("canvas");
        canvas.className = "preview";
        canvas.width = 720;
        canvas.height = 450;
        el.appendChild(canvas);
        if (preview.data) {
          playSkeleton(el.id, canvas, preview.data);
          return;
        }
        fetch(`${preview.url}${preview.url.includes("?") ? "&" : "?"}t=${Date.now()}`, { cache: "no-store" })
          .then(r => r.json())
          .then(data => playSkeleton(el.id, canvas, data))
          .catch(error => {
            const msg = document.createElement("div");
            msg.className = "error";
            msg.textContent = `Preview failed to load: ${error.message}`;
            el.appendChild(msg);
          });
      }
    }

    function playSkeleton(key, canvas, data) {
      const ctx = canvas.getContext("2d");
      const frames = (data.joints || []).filter(frame =>
        Array.isArray(frame) && frame.every(point =>
          Array.isArray(point) &&
          point.length >= 3 &&
          point.every(value => Number.isFinite(Number(value)))
        )
      );
      const chains = (data.chains || []).filter(chain =>
        Array.isArray(chain) && chain.length > 1 && chain.every(index => Number.isInteger(Number(index)))
      ).map(chain => chain.map(index => Number(index)));
      const edges = (data.edges || []).filter(edge =>
        Array.isArray(edge) && edge.length >= 2 && Number.isInteger(Number(edge[0])) && Number.isInteger(Number(edge[1]))
      ).map(edge => [Number(edge[0]), Number(edge[1])]);
      const drawEdges = chains.length
        ? chains.flatMap(chain => chain.slice(0, -1).map((joint, index) => [joint, chain[index + 1]]))
        : edges;
      if (!frames.length || !drawEdges.length) {
        ctx.fillStyle = "#0f1720";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#f8fafc";
        ctx.font = "18px Inter, Segoe UI, sans-serif";
        ctx.fillText("棰勮鏁版嵁涓虹┖鎴栭鏋堕摼鏃犳晥", 28, 42);
        return;
      }

      const projectedBounds = frames.flat().map(p => ({
        x: p[0],
        y: p[1]
      }));
      const minViewX = Math.min(...projectedBounds.map(p => p.x));
      const maxViewX = Math.max(...projectedBounds.map(p => p.x));
      const minY = Math.min(...projectedBounds.map(p => p.y));
      const maxY = Math.max(...projectedBounds.map(p => p.y));
      const padding = 44;
      const availableW = canvas.width - padding * 2;
      const availableH = canvas.height - padding * 2;
      const rangeX = Math.max(0.001, maxViewX - minViewX);
      const rangeY = Math.max(0.001, maxY - minY);
      const scale = Math.min(availableW / rangeX, availableH / rangeY);
      const centerX = (minViewX + maxViewX) / 2;
      const centerY = (minY + maxY) / 2;
      const fps = data.fps || 20;
      const start = performance.now();
      const chainColors = ["#7dd3fc", "#86efac", "#fef08a", "#fca5a5", "#c4b5fd"];

      function project(point) {
        const viewX = point[0];
        return [
          canvas.width / 2 + (viewX - centerX) * scale,
          canvas.height / 2 - (point[1] - centerY) * scale
        ];
      }

      function draw(now = start) {
        const elapsed = Math.max(0, now - start);
        const index = Math.floor((elapsed / 1000) * fps) % frames.length;
        const frame = frames[index];
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#0f1720";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "rgba(248,250,252,0.7)";
        ctx.font = "13px Inter, Segoe UI, sans-serif";
        ctx.fillText(`${frames.length}f / ${frame.length}j / ${drawEdges.length}b`, 16, 24);
        ctx.strokeStyle = "rgba(255,255,255,0.12)";
        ctx.lineWidth = 1;
        for (let i = 0; i < 5; i++) {
          const y = canvas.height - 38 - i * 70;
          ctx.beginPath();
          ctx.moveTo(34, y);
          ctx.lineTo(canvas.width - 34, y);
          ctx.stroke();
        }
        ctx.lineWidth = 4;
        ctx.lineCap = "round";
        for (let edgeIndex = 0; edgeIndex < drawEdges.length; edgeIndex++) {
          const [a, b] = drawEdges[edgeIndex];
          if (!Array.isArray(frame[a]) || !Array.isArray(frame[b])) continue;
          ctx.strokeStyle = chainColors[Math.min(chainColors.length - 1, Math.floor(edgeIndex / 4))];
          const pa = project(frame[a]);
          const pb = project(frame[b]);
          ctx.beginPath();
          ctx.moveTo(pa[0], pa[1]);
          ctx.lineTo(pb[0], pb[1]);
          ctx.stroke();
        }
        ctx.fillStyle = "#f8fafc";
        for (const joint of frame) {
          if (!Array.isArray(joint)) continue;
          const p = project(joint);
          ctx.beginPath();
          ctx.arc(p[0], p[1], 4, 0, Math.PI * 2);
          ctx.fill();
        }
        activeAnimations.set(key, requestAnimationFrame(draw));
      }
      draw(start);
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      })[ch]);
    }

    async function submitForm(form, url, resultEl) {
      const button = form.querySelector("button");
      button.disabled = true;
      resultEl.textContent = "杩愯涓紝妯″瀷鍔犺浇鍜?Blender 瀵煎嚭闇€瑕佷竴鐐规椂闂?..";
      try {
        const response = await fetch(url, { method: "POST", body: new FormData(form) });
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
          const text = await response.text();
          throw new Error(`Service returned non-JSON content: HTTP ${response.status}. ${text.slice(0, 120)}`);
        }
        const data = await response.json();
        if (data.job_id) {
          await pollJob(data.job_id, resultEl);
          return;
        }
        renderResult(resultEl, data);
        if (data.success) {
          refreshHistory();
        }
      } catch (error) {
        resultEl.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
      } finally {
        button.disabled = false;
      }
    }

    async function pollJob(jobId, resultEl) {
      resultEl.innerHTML = `<div class="ok">Job submitted: ${escapeHtml(jobId)}</div><div>Background processing is running; the page will refresh automatically.</div>`;
      while (true) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}?t=${Date.now()}`, { cache: "no-store" });
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
          throw new Error(`浠诲姟鐘舵€佽繑鍥炰簡闈?JSON 鍐呭锛孒TTP ${response.status}`);
        }
          resultEl.innerHTML = `<div class="ok">${escapeHtml(job.message || "Background processing...")}</div><div>Job ID: ${escapeHtml(jobId)}</div>`;
        if (!job.success) {
          renderResult(resultEl, job);
          return;
        }
        if (job.status === "queued" || job.status === "running") {
          throw new Error(`Job status returned non-JSON content: HTTP ${response.status}`);
          continue;
        }
        if (job.status === "done") {
          renderResult(resultEl, job.result);
          refreshHistory();
          return;
        }
        if (job.status === "failed") {
          renderResult(resultEl, job.result || { success: false, error: job.message || "浠诲姟澶辫触" });
          return;
        }
      }
    }

    document.getElementById("t2mForm").addEventListener("submit", event => {
      event.preventDefault();
      submitForm(event.currentTarget, "/api/t2m", document.getElementById("t2mResult"));
    });

    document.getElementById("v2mForm").addEventListener("submit", event => {
      event.preventDefault();
      submitForm(event.currentTarget, "/api/v2m", document.getElementById("v2mResult"));
    });

    document.getElementById("refreshHistory").addEventListener("click", refreshHistory);
    loadLatest();
    refreshHistory();
  
