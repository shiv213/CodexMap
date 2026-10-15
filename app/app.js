const map = document.querySelector("#map"),
  nodes = document.querySelector("#nodes"),
  preview = document.querySelector("#preview");
let saved = {};
try {
  saved = JSON.parse(localStorage.getItem("codex-chat-map-v1") || "{}");
} catch {}
for (const p of Object.values(saved)) {
  if (p.doneTurnId) p.done = true;
  delete p.doneTurnId;
}
let serverSaved = {},
  pending = {},
  inFlight = {},
  saving = false,
  saveFailed = false,
  initialized = false,
  stateVersion = -1;
let chats = [],
  hovered = null,
  drag = null,
  closeTimer,
  previewVersion = "",
  opening = false;
let view = "map";
const isArchived = (chat) => Boolean(saved[chat.id]?.done && chat.archived);
const mapChats = () => chats.filter((chat) => !isArchived(chat));
function setView(next) {
  view = next;
  hidePreview();
  document.querySelector("#map").hidden = next !== "map";
  document.querySelector("#archive").hidden = next !== "archive";
  document
    .querySelector("#map-tab")
    .setAttribute("aria-pressed", String(next === "map"));
  document
    .querySelector("#archive-tab")
    .setAttribute("aria-pressed", String(next === "archive"));
  if (next === "map") render();
}
document
  .querySelector("#map-tab")
  .addEventListener("click", () => setView("map"));
document
  .querySelector("#archive-tab")
  .addEventListener("click", () => setView("archive"));
function renderArchive() {
  const archived = chats.filter(isArchived);
  document.querySelector("#archive-count").textContent = archived.length || "";
  document.querySelector("#archive-empty").hidden = archived.length > 0;
  const list = document.querySelector("#archive-list");
  const key = JSON.stringify(archived.map((c) => [c.id, c.title]));
  if (list.dataset.content === key) return;
  list.dataset.content = key;
  list.replaceChildren();
  for (const chat of archived) {
    const row = document.createElement("div");
    row.className = "archive-row";
    row.dataset.chatId = chat.id;
    const title = document.createElement("button");
    title.className = "archive-title";
    title.textContent = chat.title;
    title.addEventListener("click", () => showPreview(chat.id, title));
    const restore = document.createElement("button");
    restore.className = "archive-restore";
    restore.textContent = "Unmark done";
    restore.addEventListener("click", () => {
      toggleDone(chat.id);
      hidePreview();
    });
    row.append(title, restore);
    list.append(row);
  }
}
const elements = new Map();
const mobileMetrics = new Map();
const mobilePositions = new Map();
const compact = () =>
  matchMedia("(max-width: 700px), (max-height: 500px)").matches;
const touchUI = () => compact() || matchMedia("(hover: none)").matches;
function canonical(p) {
  return {
    x: p.x,
    y: p.y,
    done: Boolean(p.done),
    seen: p.seen || null,
    mobile: p.mobile?.manual ? p.mobile : null,
  };
}
function applyPatch(target, patch) {
  for (const [key, value] of Object.entries(patch || {})) {
    if (value === null) delete target[key];
    else target[key] = value;
  }
  return target;
}
function applyServerState(data) {
  if (data.stateVersion < stateVersion) return;
  stateVersion = data.stateVersion;
  serverSaved = data.state || {};
  const next = {};
  for (const id of new Set([
    ...Object.keys(saved),
    ...Object.keys(serverSaved),
  ])) {
    const previous = saved[id] || {},
      remote = serverSaved[id];
    const p = remote
      ? {
          x: remote.x ?? previous.x ?? 0.25,
          y: remote.y ?? previous.y ?? 0.25,
          done: Boolean(remote.done),
          ...(remote.seen ? { seen: remote.seen } : {}),
          ...(remote.mobile ? { mobile: remote.mobile } : {}),
        }
      : { ...previous };
    applyPatch(p, inFlight[id]);
    applyPatch(p, pending[id]);
    if (drag?.id === id) {
      p.x = previous.x;
      p.y = previous.y;
      if (previous.mobile) p.mobile = previous.mobile;
    }
    next[id] = p;
  }
  saved = next;
}
function persist() {
  for (const [id, p] of Object.entries(saved)) {
    const value = canonical(p),
      before = canonical(
        applyPatch(
          applyPatch({ ...serverSaved[id] }, inFlight[id]),
          pending[id],
        ),
      );
    for (const key of Object.keys(value)) {
      if (JSON.stringify(value[key]) !== JSON.stringify(before[key])) {
        (pending[id] ||= {})[key] = value[key];
      }
    }
  }
  flushSaves();
}
async function flushSaves() {
  if (saving || !Object.keys(pending).length) return;
  saving = true;
  inFlight = pending;
  pending = {};
  try {
    const response = await fetch("/api/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates: inFlight }),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw Error();
    const result = await response.json();
    inFlight = {};
    saveFailed = false;
    applyServerState(result);
    render();
    try {
      localStorage.setItem("codex-chat-map-v1", JSON.stringify(saved));
    } catch {}
  } catch {
    for (const [id, patch] of Object.entries(inFlight))
      pending[id] = { ...patch, ...pending[id] };
    inFlight = {};
    saveFailed = true;
    document.querySelector("#connection").textContent =
      "Save failed · retrying";
  } finally {
    saving = false;
    if (Object.keys(pending).length)
      setTimeout(flushSaves, saveFailed ? 3000 : 0);
  }
}
function position(el, p) {
  el.style.left = `${p.x * 100}%`;
  el.style.top = `${p.y * 100}%`;
}
function getPosition(id) {
  return compact() ? mobilePositions.get(id) || saved[id].mobile : saved[id];
}
function moveNode(id, x, y) {
  const p = saved[id];
  p.x = clamp(x);
  p.y = clamp(y);
  if (compact()) {
    const metrics = mobileMetrics.get(id);
    const marginX = (metrics.width / 2 + 2) / nodes.clientWidth;
    const marginY = (metrics.height / 2 + 2) / nodes.clientHeight;
    p.x = Math.max(marginX, Math.min(1 - marginX, p.x));
    p.y = Math.max(marginY, Math.min(1 - marginY, p.y));
    p.mobile = { x: p.x, y: p.y, manual: true };
    mobilePositions.set(id, p.mobile);
  } else delete p.mobile;
  position(elements.get(id), getPosition(id));
}
function prepareMobileLayout() {
  map.style.minHeight = "";
  mobileMetrics.clear();
  mobilePositions.clear();
  if (!compact() || view !== "map") return;
  const groups = [[], [], [], []];
  mapChats().forEach((chat) => {
    const p = saved[chat.id];
    groups[(p.y >= 0.5 ? 2 : 0) + (p.x >= 0.5 ? 1 : 0)].push(chat);
  });
  const width = nodes.clientWidth,
    height = nodes.clientHeight;
  const usableWidth = width / 2 - 16,
    usableHeight = Math.max(24, height / 2 - 48);
  groups.forEach((group, quadrant) => {
    const maxColumns = Math.max(1, Math.floor(usableWidth / 44));
    const maxRows = Math.max(1, Math.floor(usableHeight / 44));
    const columns = Math.min(
      maxColumns,
      Math.max(Math.min(2, group.length), Math.ceil(group.length / maxRows)),
    );
    const rows = Math.max(1, Math.ceil(group.length / Math.max(1, columns)));
    const cellWidth = usableWidth / Math.max(1, columns),
      cellHeight = usableHeight / rows;
    const nodeWidth = Math.min(78, cellWidth - 4),
      nodeHeight = Math.min(44, cellHeight - 2);
    group
      .sort((a, b) => a.id.localeCompare(b.id))
      .forEach((chat, i) => {
        mobileMetrics.set(chat.id, {
          width: nodeWidth,
          height: nodeHeight,
          dot: Math.min(22, nodeWidth - 8, nodeHeight - 8),
          dense: nodeWidth < 56 || nodeHeight < 38,
        });
        if (!saved[chat.id].mobile?.manual)
          saved[chat.id].mobile = {
            x:
              ((quadrant % 2 ? width / 2 : 0) +
                8 +
                ((i % columns) + 0.5) * cellWidth) /
              width,
            y:
              ((quadrant >= 2 ? height / 2 : 0) +
                40 +
                (Math.floor(i / columns) + 0.5) * cellHeight) /
              height,
          };
      });
  });
  // Resolve collisions after fitting older saved positions to a smaller viewport.
  const occupied = [];
  mapChats()
    .sort(
      (a, b) =>
        Number(Boolean(saved[b.id].mobile?.manual)) -
          Number(Boolean(saved[a.id].mobile?.manual)) ||
        a.id.localeCompare(b.id),
    )
    .forEach((chat) => {
      const p = saved[chat.id].mobile,
        metric = mobileMetrics.get(chat.id);
      const qx = saved[chat.id].x >= 0.5 ? width / 2 : 0,
        qy = saved[chat.id].y >= 0.5 ? height / 2 : 0;
      const x0 = qx + 8 + metric.width / 2,
        x1 = qx + width / 2 - 8 - metric.width / 2;
      const y0 = qy + 40 + metric.height / 2,
        y1 = qy + height / 2 - 8 - metric.height / 2;
      const desired = {
        x: Math.max(x0, Math.min(x1, p.x * width)),
        y: Math.max(y0, Math.min(y1, p.y * height)),
      };
      const fits = (point) =>
        occupied.every(
          (box) =>
            Math.abs(point.x - box.x) >= (metric.width + box.width) / 2 + 1 ||
            Math.abs(point.y - box.y) >= (metric.height + box.height) / 2 + 1,
        );
      let point = desired;
      if (!fits(point)) {
        const candidates = [];
        for (let x = x0; x <= x1 + 0.1; x += 4)
          for (let y = y0; y <= y1 + 0.1; y += 4) candidates.push({ x, y });
        candidates.sort(
          (a, b) =>
            Math.hypot(a.x - desired.x, a.y - desired.y) -
            Math.hypot(b.x - desired.x, b.y - desired.y),
        );
        point = candidates.find(fits) || desired;
      }
      occupied.push({ ...point, width: metric.width, height: metric.height });
      mobilePositions.set(chat.id, { x: point.x / width, y: point.y / height });
    });
}
function toggleDone(id) {
  const current = chats.find((c) => c.id === id);
  if (!current) return;
  current.archived = false;
  saved[id].done = !saved[id].done;
  if (!saved[id].done) delete saved[id].seen;
  persist();
  render();
}
function clamp(n) {
  return Math.max(0.045, Math.min(0.955, n));
}
function startPosition(chat) {
  const active = chat.status === "running";
  const high = chat.pinned;
  const peers = mapChats().filter(
    (c) => (c.status === "running") === active && c.pinned === high,
  );
  const i = peers.findIndex((c) => c.id === chat.id);
  const cols = Math.min(
    3,
    Math.max(2, Math.ceil(Math.sqrt(peers.length * 1.5))),
  );
  return {
    x: (active ? 0.09 : 0.59) + (i % cols) * (0.32 / Math.max(1, cols - 1)),
    y:
      (high ? 0.13 : 0.63) +
      Math.floor(i / cols) *
        (0.26 / Math.max(1, Math.ceil(peers.length / cols) - 1)),
  };
}
function render() {
  chats.forEach((chat) => {
    if (!saved[chat.id]) saved[chat.id] = startPosition(chat);
  });
  renderArchive();
  if (view !== "map") {
    if (hovered) updatePreview();
    return;
  }
  prepareMobileLayout();
  const visible = mapChats();
  const ids = new Set(visible.map((c) => c.id));
  for (const [id, el] of elements)
    if (!ids.has(id)) {
      el.remove();
      elements.delete(id);
    }
  visible.forEach((chat) => {
    let el = elements.get(chat.id);
    if (!el) {
      el = document.createElement("button");
      el.type = "button";
      el.className = "node";
      el.dataset.id = chat.id;
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.setAttribute("aria-hidden", "true");
      const check = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "svg",
      );
      check.classList.add("check");
      check.setAttribute("viewBox", "0 0 24 24");
      check.setAttribute("fill", "none");
      const checkPath = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "path",
      );
      checkPath.setAttribute("d", "M5 12l4 4L19 6");
      check.append(checkPath);
      dot.append(check);
      const label = document.createElement("span");
      label.className = "label";
      el.append(dot, label);
      nodes.append(el);
      elements.set(chat.id, el);
      el.addEventListener("pointerenter", () => {
        if (!drag && !touchUI()) showPreview(chat.id, el);
      });
      el.addEventListener("pointerleave", scheduleClose);
      el.addEventListener("focus", () => {
        if (!touchUI()) showPreview(chat.id, el);
      });
      el.addEventListener("blur", scheduleClose);
      el.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        toggleDone(chat.id);
        hidePreview();
      });
      el.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        drag = {
          id: chat.id,
          el,
          x: event.clientX,
          y: event.clientY,
          moved: false,
        };
        el.setPointerCapture(event.pointerId);
      });
      el.addEventListener("pointermove", (event) => {
        if (!drag || drag.el !== el) return;
        if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 5)
          drag.moved = true;
        if (!drag.moved) return;
        hidePreview();
        el.classList.add("dragging");
        const b = nodes.getBoundingClientRect();
        moveNode(
          chat.id,
          (event.clientX - b.left) / b.width,
          (event.clientY - b.top) / b.height,
        );
      });
      el.addEventListener("pointerup", () => {
        if (!drag || drag.el !== el) return;
        el.dataset.suppressClick = String(drag.moved);
        el.classList.remove("dragging");
        drag = null;
        persist();
      });
      el.addEventListener("pointercancel", () => {
        drag = null;
        el.dataset.suppressClick = "true";
        el.classList.remove("dragging");
        persist();
      });
      el.addEventListener("click", () => {
        if (el.dataset.suppressClick === "true") {
          el.dataset.suppressClick = "false";
          return;
        }
        if (touchUI()) {
          showPreview(chat.id, el);
          return;
        }
        openChat(chat.id);
      });
      el.addEventListener("keydown", (event) => {
        const shift = {
          ArrowLeft: [-0.035, 0],
          ArrowRight: [0.035, 0],
          ArrowUp: [0, -0.035],
          ArrowDown: [0, 0.035],
        }[event.key];
        if (shift) {
          event.preventDefault();
          const p = getPosition(chat.id);
          moveNode(chat.id, p.x + shift[0], p.y + shift[1]);
          persist();
          hidePreview();
        }
      });
    }
    const p = saved[chat.id];
    const metrics = mobileMetrics.get(chat.id);
    el.classList.toggle("dense", Boolean(metrics?.dense));
    if (metrics) {
      el.style.setProperty("--mobile-node-width", `${metrics.width}px`);
      el.style.setProperty("--mobile-node-height", `${metrics.height}px`);
      el.style.setProperty("--mobile-dot-size", `${metrics.dot}px`);
    }
    const markedDone = Boolean(p.done);
    el.classList.toggle("running", chat.status === "running");
    el.classList.toggle("finished", chat.status === "finished");
    el.classList.toggle("seen", p.seen === chat.turnId);
    el.classList.toggle("marked-done", markedDone);
    el.querySelector(".label").textContent = chat.title;
    el.setAttribute(
      "aria-label",
      `${chat.title}, ${markedDone ? "marked done" : chat.status}. ${touchUI() ? "Tap for preview and actions." : "Click to open in Codex. Right-click to toggle done. Arrow keys move."}`,
    );
    if (!drag || drag.id !== chat.id) position(el, getPosition(chat.id));
  });
  document.querySelector("#empty").hidden = visible.length > 0;
  document.querySelector("#empty").textContent = chats.length
    ? "All chats are in Archived."
    : "No chats have run today. New runs will appear here.";
  if (hovered && !drag) updatePreview();
}
function scheduleClose() {
  if (touchUI()) return;
  clearTimeout(closeTimer);
  closeTimer = setTimeout(hidePreview, 600);
}
function hidePreview() {
  preview.hidden = true;
  document.querySelector("#backdrop").hidden = true;
  document.body.classList.remove("preview-open");
  hovered = null;
  previewVersion = "";
}
function showPreview(id, el) {
  if (preview.hidden || hovered !== id)
    document.querySelector("#messages").dataset.chatId = "";
  clearTimeout(closeTimer);
  hovered = id;
  preview.hidden = false;
  updatePreview();
  if (touchUI()) {
    document.querySelector("#backdrop").hidden = false;
    document.body.classList.add("preview-open");
  }
  if (compact()) {
    preview.style.left = "";
    preview.style.top = "";
    return;
  }
  const b = el.getBoundingClientRect(),
    width = preview.offsetWidth,
    height = preview.offsetHeight;
  let x = b.left + b.width / 2 + 20;
  if (x + width > innerWidth - 12) x = b.left + b.width / 2 - width - 20;
  preview.style.left = `${Math.max(12, Math.min(innerWidth - width - 12, x))}px`;
  preview.style.top = `${Math.max(12, Math.min(innerHeight - height - 12, b.top - 16))}px`;
}
function updatePreview() {
  const chat = chats.find((c) => c.id === hovered);
  if (!chat) return hidePreview();
  const markedDone = Boolean(saved[chat.id]?.done);
  const version = JSON.stringify([
    chat.id,
    chat.title,
    chat.status,
    markedDone,
    chat.messages,
  ]);
  if (version === previewVersion) return;
  previewVersion = version;
  document.querySelector("#toggle-done").textContent = markedDone
    ? "Unmark done"
    : "Mark done";
  document.querySelector("#preview-title").textContent = chat.title;
  document.querySelector("#preview-status").textContent =
    `${markedDone ? "marked done" : chat.status} · ${new Date(chat.updatedAt * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  const container = document.querySelector("#messages");
  const scroll = container.scrollTop;
  const stickToBottom =
    container.dataset.chatId !== chat.id ||
    container.scrollHeight - container.clientHeight - scroll < 24;
  container.dataset.chatId = chat.id;
  container.replaceChildren();
  if (!chat.messages.length)
    container.textContent = "No recent messages available.";
  chat.messages.forEach((message) => {
    const article = document.createElement("article");
    article.className = `message ${message.role}`;
    const role = document.createElement("span");
    role.className = "role";
    role.textContent = message.role === "assistant" ? "Codex" : "You";
    const text = document.createElement("p");
    text.textContent = message.text;
    article.append(role, text);
    container.append(article);
  });
  container.scrollTop = stickToBottom ? container.scrollHeight : scroll;
}
async function openChat(id) {
  if (opening) return;
  opening = true;
  try {
    const response = await fetch(`/api/open/${id}`, { method: "POST" });
    if (!response.ok) throw Error("Could not open chat");
    const chat = chats.find((c) => c.id === id);
    if (chat.status === "finished") saved[id].seen = chat.turnId;
    persist();
    render();
  } catch {
    document.querySelector("#connection").textContent =
      "Could not open chat in Codex";
  } finally {
    opening = false;
  }
}
preview.addEventListener("pointerenter", () => clearTimeout(closeTimer));
preview.addEventListener("pointerleave", scheduleClose);
preview.addEventListener("focusin", () => clearTimeout(closeTimer));
preview.addEventListener("focusout", scheduleClose);
document.querySelector("#open-chat").addEventListener("click", () => {
  if (hovered) openChat(hovered);
});
if (
  /iPhone|iPad|iPod/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
) {
  document.querySelector("#open-chat").firstChild.textContent = "Open on Mac ";
}
document.querySelector("#toggle-done").addEventListener("click", () => {
  if (hovered) toggleDone(hovered);
});
document.querySelector("#close-preview").addEventListener("click", hidePreview);
document.querySelector("#backdrop").addEventListener("click", hidePreview);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hidePreview();
});
document.addEventListener("pointerdown", (event) => {
  if (!preview.contains(event.target) && !event.target.closest(".node"))
    hidePreview();
});
let layoutWidth = innerWidth;
window.addEventListener("resize", () => {
  if (innerWidth !== layoutWidth) {
    layoutWidth = innerWidth;
    hidePreview();
  }
  render();
});
window.visualViewport?.addEventListener("resize", render);
async function refresh() {
  try {
    const response = await fetch("/api/chats", {
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw Error();
    const data = await response.json();
    chats = data.threads;
    applyServerState(data);
    render();
    if (!initialized) {
      const updates = Object.fromEntries(
        chats
          .filter((c) => !serverSaved[c.id])
          .map((c) => [c.id, canonical(saved[c.id])]),
      );
      if (Object.keys(updates).length) {
        const migration = await fetch("/api/state", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ updates, initialize: true }),
          signal: AbortSignal.timeout(15000),
        });
        if (!migration.ok) throw Error();
        applyServerState(await migration.json());
        render();
      }
      initialized = true;
    }
    document.querySelector("#connection").textContent = saveFailed
      ? "Save failed · retrying"
      : `${chats.filter((c) => c.status === "running").length} running · ${chats.length} chats`;
  } catch {
    document.querySelector("#connection").textContent =
      "Disconnected · retrying";
  }
  setTimeout(refresh, 4000);
}
refresh();
