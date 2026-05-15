/* Free Claude Code · SPA shell + router.
 *
 * Vanilla JS, no build step. Each page module exports `render(container)`.
 * Server route serves index.html for any /app/* path; this script takes over.
 */

import { render as homePage } from "/app/assets/pages/home.js";
import { render as chatPage } from "/app/assets/pages/chat.js";
import { render as projectsPage } from "/app/assets/pages/projects.js";
import { render as usagePage } from "/app/assets/pages/usage.js";
import { render as filesPage } from "/app/assets/pages/files.js";
import { render as auditPage } from "/app/assets/pages/audit.js";
import { render as skillsPage } from "/app/assets/pages/skills.js";
import { render as settingsPage } from "/app/assets/pages/settings.js";
import { render as setupPage } from "/app/assets/pages/setup.js";
import { render as notFoundPage } from "/app/assets/pages/notfound.js";

const routes = [
  { path: "/app/", title: "Home", crumb: "Home", page: homePage },
  { path: "/app/chat", title: "Chat", crumb: "Chat", page: chatPage },
  { path: "/app/projects", title: "Projects", crumb: "Projects", page: projectsPage },
  { path: "/app/usage", title: "Usage", crumb: "Usage", page: usagePage },
  { path: "/app/files", title: "File edits", crumb: "File edits", page: filesPage },
  { path: "/app/audit", title: "Audit log", crumb: "Audit log", page: auditPage },
  { path: "/app/skills", title: "Skills", crumb: "Skills", page: skillsPage },
  { path: "/app/settings", title: "Settings", crumb: "Settings", page: settingsPage },
  { path: "/app/setup", title: "Setup", crumb: "Setup", page: setupPage },
];

const PALETTE_ITEMS = [
  ...routes.map((r) => ({ label: r.title, route: r.route ?? r.path, kind: "Page" })),
  { label: "Toggle theme", action: toggleTheme, kind: "Action" },
  { label: "Reload current page", action: () => mountRoute(window.location.pathname), kind: "Action" },
];

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function normalizePath(path) {
  if (!path.startsWith("/app")) return "/app/";
  if (path === "/app") return "/app/";
  return path.replace(/\/+$/, "") || "/app/";
}

function findRoute(path) {
  const normalized = normalizePath(path);
  return (
    routes.find((r) => r.path === normalized) ||
    routes.find((r) => normalized.startsWith(r.path + "/")) ||
    null
  );
}

function highlightNav(path) {
  $$(".nav-link").forEach((link) => {
    const route = link.dataset.route;
    if (!route) return;
    const isActive = route === "/app/" ? path === "/app/" : path.startsWith(route);
    link.classList.toggle("active", isActive);
  });
}

function setBreadcrumb(crumb) {
  $("#breadcrumb").innerHTML = `<strong>${escapeHtml(crumb)}</strong>`;
}

async function mountRoute(rawPath) {
  const path = normalizePath(rawPath);
  const route = findRoute(path) ?? {
    title: "Not found",
    crumb: "404",
    page: notFoundPage,
  };
  document.title = `${route.title} · Free Claude Code`;
  highlightNav(path);
  setBreadcrumb(route.crumb);
  const container = $("#page");
  container.innerHTML = "<div class=\"spinner\"></div>";
  try {
    const result = route.page(container, { route, path });
    if (result instanceof Promise) await result;
  } catch (err) {
    container.innerHTML = "";
    showToast(`Failed to render ${route.title}: ${err.message}`, "error");
    console.error(err);
  }
}

function navigate(path, replace = false) {
  const normalized = normalizePath(path);
  if (normalized === window.location.pathname) {
    mountRoute(normalized);
    return;
  }
  const fn = replace ? "replaceState" : "pushState";
  window.history[fn]({}, "", normalized);
  mountRoute(normalized);
}

function attachLinkHandlers() {
  $$(".nav-link").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      navigate(link.dataset.route);
    });
  });
  document.body.addEventListener("click", (event) => {
    const anchor = event.target.closest("a[href^='/app']");
    if (!anchor) return;
    if (anchor.target === "_blank" || event.metaKey || event.ctrlKey) return;
    event.preventDefault();
    navigate(anchor.getAttribute("href"));
  });
}

window.addEventListener("popstate", () => mountRoute(window.location.pathname));

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

/* ---------- toasts ---------- */

const toastStack = () => $("#toasts");

export function showToast(message, level = "") {
  const node = document.createElement("div");
  node.className = `toast ${level}`.trim();
  node.textContent = message;
  toastStack().appendChild(node);
  setTimeout(() => node.remove(), 4000);
}

/* ---------- theme ---------- */

function loadTheme() {
  const stored = localStorage.getItem("fcc-theme");
  if (stored === "light") document.documentElement.dataset.theme = "light";
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const next = current === "light" ? "dark" : "light";
  if (next === "light") {
    document.documentElement.dataset.theme = "light";
    localStorage.setItem("fcc-theme", "light");
  } else {
    delete document.documentElement.dataset.theme;
    localStorage.setItem("fcc-theme", "dark");
  }
}

/* ---------- command palette ---------- */

let paletteIndex = 0;
let paletteFilter = "";

function openPalette() {
  paletteIndex = 0;
  paletteFilter = "";
  $("#paletteInput").value = "";
  renderPalette();
  $("#palette").classList.remove("hidden");
  $("#paletteInput").focus();
}

function closePalette() {
  $("#palette").classList.add("hidden");
}

function paletteVisible() {
  return PALETTE_ITEMS.filter((item) =>
    item.label.toLowerCase().includes(paletteFilter.toLowerCase()),
  );
}

function renderPalette() {
  const list = $("#paletteResults");
  list.innerHTML = "";
  paletteVisible().forEach((item, idx) => {
    const li = document.createElement("li");
    if (idx === paletteIndex) li.classList.add("focused");
    li.innerHTML = `<span>${escapeHtml(item.label)}</span><span class="meta">${item.kind}</span>`;
    li.addEventListener("click", () => activatePaletteItem(idx));
    list.appendChild(li);
  });
}

function activatePaletteItem(idx) {
  const item = paletteVisible()[idx];
  if (!item) return;
  closePalette();
  if (item.route) navigate(item.route);
  if (item.action) item.action();
}

function bindPalette() {
  $("#paletteOpen").addEventListener("click", openPalette);
  $("#paletteInput").addEventListener("input", (event) => {
    paletteFilter = event.target.value;
    paletteIndex = 0;
    renderPalette();
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#palette").classList.contains("hidden") ? openPalette() : closePalette();
    }
    if ($("#palette").classList.contains("hidden")) return;
    if (event.key === "Escape") closePalette();
    if (event.key === "ArrowDown") {
      event.preventDefault();
      paletteIndex = Math.min(paletteIndex + 1, paletteVisible().length - 1);
      renderPalette();
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      paletteIndex = Math.max(0, paletteIndex - 1);
      renderPalette();
    }
    if (event.key === "Enter") {
      event.preventDefault();
      activatePaletteItem(paletteIndex);
    }
  });
}

/* ---------- status / footer ---------- */

async function loadStatus() {
  try {
    const status = await fetch("/admin/api/status").then((r) => r.json());
    $("#serverStatus").textContent = status.status || "online";
    if (status.version) $("#versionTag").textContent = `v${status.version}`;
  } catch {
    $("#serverStatus").textContent = "offline";
    $("#serverStatus").className = "status-pill error";
  }
  try {
    const cost = await fetch("/admin/api/usage/cost-today")
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    if (cost && typeof cost.usd === "number") {
      $("#costToday").textContent = `$${cost.usd.toFixed(3)}`;
    } else {
      $("#costToday").textContent = "—";
    }
  } catch {
    $("#costToday").textContent = "—";
  }
}

/* ---------- shared utilities exposed to pages ---------- */

export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json", ...(options.headers ?? {}) },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

export const navigateTo = navigate;
export const utilEscapeHtml = escapeHtml;

/* ---------- boot ---------- */

function boot() {
  loadTheme();
  attachLinkHandlers();
  bindPalette();
  $("#themeToggle").addEventListener("click", toggleTheme);
  loadStatus();
  mountRoute(window.location.pathname);
}

boot();
