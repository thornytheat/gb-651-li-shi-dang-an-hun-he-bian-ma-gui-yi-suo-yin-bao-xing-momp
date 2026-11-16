/* M2 编码复核工作台：候选对照、乐观并发决定、可续跑发布、版本对账。 */
const $ = (sel, root = document) => root.querySelector(sel);
const api = async (url, options = {}) => {
  const res = await fetch(url, {
    headers: { "content-type": "application/json" }, ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.message || res.statusText), {
    code: body.error, status: res.status,
  });
  return body;
};
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function loadFiles() {
  const { files } = await api("/api/encoding/review/files");
  const tbody = $("#fileTable tbody");
  tbody.innerHTML = files.map((f) => `
    <tr data-file-id="${esc(f.file_id)}">
      <td>${esc(f.file_id)}</td><td class="num">${f.source_line}</td>
      <td class="num">${esc(f.blob_hex)}</td><td class="num">v${f.current_version}</td>
      <td>${esc(f.latest_encoding || "—")} <span class="panel__meta">${esc(f.latest_reason || "")}</span></td>
      <td class="num">${f.index_versions}</td>
      <td><button class="btn btn--small" data-open="${esc(f.file_id)}">复核</button></td>
    </tr>`).join("");
  return files;
}

async function openFile(fileId) {
  const data = await api(`/api/encoding/review/files/${encodeURIComponent(fileId)}`);
  $("#detailPanel").hidden = false;
  $("#historyPanel").hidden = false;
  $("#detailMeta").textContent = `${data.file.file_id} · L${data.file.source_line} · v${data.file.current_version}`;
  $("#ambiguousHint").textContent = data.ambiguous
    ? `同一字节存在多种完整解读（${data.tied_encodings.join(" / ")}），历史自动裁决：${data.auto.encoding}，需人工复核`
    : "仅一种完整解读";
  const autoEnc = data.auto.encoding;
  $("#candidateTable tbody").innerHTML = data.candidates.map((c) => `
    <tr>
      <td>${esc(c.encoding)}${c.encoding === autoEnc ? ' <span class="panel__meta">(历史评分选择)</span>' : ""}</td>
      <td class="num">${c.score}</td>
      <td>${esc(c.text)}</td>
      <td><button class="btn btn--small" data-pick="${esc(c.encoding)}">选用</button></td>
    </tr>`).join("");
  $("#candidateTable tbody").querySelectorAll("[data-pick]").forEach((btn) =>
    btn.addEventListener("click", () => {
      $("#decisionForm").encoding.value = btn.dataset.pick;
      $("#decisionMsg").textContent = `已选择 ${btn.dataset.pick}，请填写理由后提交`;
    }));
  const form = $("#decisionForm");
  form.file_id.value = fileId;
  form.base_version.value = data.file.current_version;
  form.encoding.value = "";
  $("#baseLabel").textContent = data.file.current_version;
  $("#decisionMsg").textContent = "";
  renderHistory(data);
}

function renderHistory(data) {
  $("#historyMeta").textContent = data.file.file_id;
  const decRows = data.history.map((d) => `
    <tr><td class="num">v${d.version}</td><td class="num">v${d.base_version}</td>
      <td>${esc(d.encoding)}</td><td>${esc(d.text)}</td><td>${esc(d.reason_code)}</td>
      <td>${esc(d.reason_text)}</td><td>${esc(d.reviewer)}</td><td>${esc(d.created_at)}</td></tr>`);
  const idxRows = data.index_versions.map((v) => `
    <tr class="idx-row"><td class="num">索引 v${v.version}</td><td class="num">—</td>
      <td>${esc(v.encoding)}</td><td>${esc(v.text)} <span class="panel__meta">档号:${esc(v.archive_id)}</span></td>
      <td>PUBLISHED</td><td>${esc(v.publish_run)}</td><td>${esc(v.published_by)}</td>
      <td>${esc(v.published_at)}</td></tr>`);
  $("#historyTable tbody").innerHTML = idxRows.concat(decRows).join("");
}

async function submitDecision(event) {
  event.preventDefault();
  const form = event.target;
  const msg = $("#decisionMsg");
  if (!form.encoding.value) { msg.textContent = "请先在候选表中选用一种编码"; return; }
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.base_version = Number(payload.base_version);
  try {
    const data = await api("/api/encoding/review/decisions", {
      method: "POST", body: JSON.stringify(payload),
    });
    msg.textContent = `已保存 v${data.saved_decision.version}（${data.saved_decision.encoding}）`;
    await loadFiles();
    await openFile(payload.file_id);
  } catch (err) {
    msg.textContent = err.code === "STALE_VERSION"
      ? `已拒绝过期决定：${err.message}，请刷新后基于最新版本重提`
      : `提交失败：${err.message}`;
  }
}

async function publish() {
  const out = $("#publishResult");
  try {
    const data = await api("/api/encoding/review/publish", {
      method: "POST", body: JSON.stringify({}),
    });
    out.textContent = JSON.stringify({
      run_id: data.run_id, status: data.status,
      done: data.items.filter((i) => i.done).length, total: data.items.length,
      failures: data.failures,
    }, null, 2);
    await loadFiles();
    await loadReconcile();
  } catch (err) {
    out.textContent = `发布失败（旧版索引保留）：${err.message}`;
  }
}

async function loadReconcile() {
  const data = await api("/api/encoding/review/reconcile");
  $("#reconcileTable tbody").innerHTML = data.files.map((r) => `
    <tr>
      <td>${esc(r.file_id)}<br><span class="panel__meta">L${r.source_line}</span></td>
      <td>${esc(r.baseline ? r.baseline.archive_id : "—")}</td>
      <td>${esc(r.current ? r.current.archive_id : "—")}</td>
      <td>${esc(r.baseline ? r.baseline.encoding : "—")}</td>
      <td>${esc(r.current ? r.current.encoding : "—")}</td>
      <td>${r.diff && r.diff.changed_archive_id ? '<span class="sev">DIFF</span>' : "—"}</td>
    </tr>`).join("");
}

$("#seedBtn").addEventListener("click", async () => {
  const data = await api("/api/encoding/review/seed", { method: "POST" });
  $("#seedStatus").textContent = `已登记 ${data.files.length} 个文件并写入 v0 基线`;
  await loadFiles();
  await loadReconcile();
});
$("#refreshFiles").addEventListener("click", loadFiles);
$("#registerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const data = await api("/api/encoding/review/files", {
    method: "POST",
    body: JSON.stringify({
      blob_hex: form.blob_hex.value.trim(),
      source_line: Number(form.source_line.value),
    }),
  });
  form.reset();
  await loadFiles();
  await openFile(data.file.file_id);
});
$("#fileTable").addEventListener("click", (event) => {
  const btn = event.target.closest("[data-open]");
  if (btn) openFile(btn.dataset.open);
});
$("#decisionForm").addEventListener("submit", submitDecision);
$("#publishBtn").addEventListener("click", publish);
$("#reconcileBtn").addEventListener("click", loadReconcile);
loadFiles().catch(() => {});
loadReconcile().catch(() => {});
