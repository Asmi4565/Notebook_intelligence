// Notebook Intelligence System (NIS) - Frontend Controller

// Automatic global fetch enhancer: guarantees cookies and authorization tokens are always sent
const _nativeFetch = window.fetch;
window.fetch = function(input, init) {
  init = init || {};
  if (!init.credentials) {
    init.credentials = "include";
  }
  const token = localStorage.getItem("nis_token") || localStorage.getItem("sb_access_token");
  if (token) {
    if (!init.headers) {
      init.headers = {};
    }
    if (init.headers instanceof Headers) {
      if (!init.headers.has("Authorization")) {
        init.headers.set("Authorization", `Bearer ${token}`);
      }
    } else if (Array.isArray(init.headers)) {
      if (!init.headers.some(([k]) => k.toLowerCase() === "authorization")) {
        init.headers.push(["Authorization", `Bearer ${token}`]);
      }
    } else if (typeof init.headers === "object") {
      if (!init.headers["Authorization"] && !init.headers["authorization"]) {
        init.headers["Authorization"] = `Bearer ${token}`;
      }
    }
  }
  return _nativeFetch(input, init);
};

let currentUser = null;
let currentNotebook = localStorage.getItem("nis_current_notebook") || "current_session";
let currentPage = 1;
let pagesData = [];
let quizData = [];
let selectedPages = new Set();
let supabaseClient = null;

// Hydrate and persist note pages locally so they never disappear on refresh
function getCachedPages() {
  try {
    const raw = localStorage.getItem(`nis_cached_pages_${currentNotebook}`);
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

function setCachedPages(pages) {
  try {
    if (pages && pages.length > 0) {
      localStorage.setItem(`nis_cached_pages_${currentNotebook}`, JSON.stringify(pages));
    }
  } catch (e) {
    // If quota exceeded, store lightweight version
    try {
      const lightweight = pages.map(p => ({
        ...p,
        image_url: (p.image_url && p.image_url.length < 300000) ? p.image_url : null
      }));
      localStorage.setItem(`nis_cached_pages_${currentNotebook}`, JSON.stringify(lightweight));
    } catch (err) {}
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  initTabs();
  initModals();
  initDropZone();
  try {
    await checkAuthUser();
  } catch (e) {
    console.warn("Auth initialization info:", e);
  }
  try {
    await checkSystemHealthConfig();
  } catch (e) {}
  try {
    await initSupabaseClient();
  } catch (e) {}

  // Hydrate immediately from cache before network requests
  const cached = getCachedPages();
  if (cached.length > 0) {
    pagesData = cached;
    updateHeaderStats();
    renderThumbnails();
    populateQuizSourceFilters();
    renderViewerPage(currentPage);
  }

  await fetchNotebooks();
  await loadPages();
});

function initDropZone() {
  const dropzone = document.getElementById("dropzone");
  if (!dropzone) return;

  ['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add('drag-active');
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove('drag-active');
    }, false);
  });

  dropzone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    const files = dt ? dt.files : null;
    if (files && files.length > 0) {
      handleFileUpload(files);
    }
  }, false);
}

async function checkSystemHealthConfig() {
  try {
    const res = await fetch("/health");
    if (res.ok) {
      const data = await res.json();
      if (data.disable_code_lab) {
        const codeTab = document.querySelector('.tab-btn[data-target="view-code"]');
        if (codeTab) codeTab.style.display = "none";
      }
    }
  } catch (e) {}
}

async function initSupabaseClient() {
  try {
    const res = await fetch("/api/config");
    if (res.ok) {
      const config = await res.json();
      if (config.supabase_url && config.supabase_anon_key && typeof supabase !== 'undefined') {
        supabaseClient = supabase.createClient(config.supabase_url, config.supabase_anon_key);
      }
    }
  } catch (e) {
    console.warn("Failed to initialize Supabase client", e);
  }
}

function getAuthHeaders(extraHeaders = {}) {
  const headers = { ...extraHeaders };
  const token = localStorage.getItem("nis_token") || localStorage.getItem("sb_access_token");
  if (token && !headers["Authorization"]) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

function handleOAuthHashToken() {
  if (window.location.hash && window.location.hash.includes("access_token=")) {
    try {
      const params = new URLSearchParams(window.location.hash.substring(1));
      const token = params.get("access_token");
      if (token) {
        localStorage.setItem("sb_access_token", token);
        history.replaceState(null, "", window.location.pathname);
      }
    } catch (e) {
      console.warn("OAuth hash token parse error:", e);
    }
  }
}

function getFormattedDisplayName(user) {
  if (!user) return "My Account";
  const name = (user.name || "").trim();
  if (name && name.toLowerCase() !== "user" && name.toLowerCase() !== "test user") {
    return name;
  }
  if (user.email) {
    const raw = user.email.split("@")[0].replace(/[._-]+/g, " ");
    const formatted = raw.split(" ").filter(Boolean).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
    if (formatted) return formatted;
  }
  if (name) return name;
  return "My Account";
}

async function checkAuthUser() {
  handleOAuthHashToken();
  try {
    // 1. Immediately hydrate cached user to avoid unstyled fallback flash
    const cachedUserStr = localStorage.getItem("nis_user");
    if (cachedUserStr) {
      try {
        currentUser = JSON.parse(cachedUserStr);
        updateUserWidget(currentUser);
      } catch (e) {}
    }

    // 2. Validate current session with backend API (sends both Bearer token and cookie)
    let res = await fetch("/api/auth/me", { credentials: "include" });

    if (!res.ok) {
      const existingToken = localStorage.getItem("nis_token") || localStorage.getItem("sb_access_token");
      if (!existingToken) {
        // No session token present — redirect to landing/login
        localStorage.removeItem("nis_token");
        localStorage.removeItem("nis_user");
        currentUser = null;
        const onAppPage = window.location.pathname === "/app" || window.location.pathname === "/app/";
        if (onAppPage) {
          window.location.href = "/";
        }
        return;
      }
      // If token exists in localStorage, maintain cached user rather than kicking out
      console.warn("Auth check returned status:", res.status, "- preserving cached session");
      return;
    }

    const data = await res.json();
    if (data && data.user) {
      currentUser = data.user;
      const formattedName = getFormattedDisplayName(currentUser);
      if (!currentUser.name || currentUser.name.trim().toLowerCase() === "user" || currentUser.name.trim().toLowerCase() === "test user") {
        currentUser.name = formattedName;
      }
      if (data.token) {
        localStorage.setItem("nis_token", data.token);
      }
      // Update cached user so future page loads show the correct name immediately
      localStorage.setItem("nis_user", JSON.stringify(currentUser));
      updateUserWidget(currentUser);
    }
  } catch (err) {
    console.error("Auth check error:", err);
    // On network errors keep showing cached user rather than blanking the widget
    if (!currentUser) {
      const cachedUserStr = localStorage.getItem("nis_user");
      if (cachedUserStr) {
        try { currentUser = JSON.parse(cachedUserStr); updateUserWidget(currentUser); } catch (e) {}
      }
    }
  }
}

function updateUserWidget(user) {
  const nameLabel = document.getElementById("userNameLabel");
  const avatarBadge = document.getElementById("userAvatarBadge");
  if (!user) return;
  const displayName = getFormattedDisplayName(user);
  if (nameLabel) {
    nameLabel.textContent = displayName;
  }
  if (avatarBadge) {
    avatarBadge.textContent = displayName.charAt(0).toUpperCase();
  }
}

async function openProfileModal() {
  if (!currentUser) {
    const cached = localStorage.getItem("nis_user");
    if (cached) {
      try { currentUser = JSON.parse(cached); } catch (e) {}
    }
  }
  if (!currentUser) {
    try {
      const res = await fetch("/api/auth/me", { credentials: "include" });
      if (res.ok) {
        const d = await res.json();
        currentUser = d.user;
      }
    } catch (e) {}
  }
  if (!currentUser) {
    window.location.href = "/";
    return;
  }

  const nameInput = document.getElementById("profileNameInput");
  const nameDisplay = document.getElementById("profileNameDisplay");
  const emailDisplay = document.getElementById("profileEmailDisplay");
  const avatarBadge = document.getElementById("profileAvatarBadge");
  const alertMsg = document.getElementById("profileAlertMsg");

  const displayName = getFormattedDisplayName(currentUser);
  if (nameInput) nameInput.value = (currentUser.name && currentUser.name.toLowerCase() !== "user") ? currentUser.name : displayName;
  if (nameDisplay) nameDisplay.textContent = displayName;
  if (emailDisplay) emailDisplay.textContent = currentUser.email || "";
  if (avatarBadge) avatarBadge.textContent = displayName.charAt(0).toUpperCase();
  if (alertMsg) alertMsg.style.display = "none";

  loadStudyPreferences();
  loadUserProfileStats();

  openModal("userProfileModal");
}

function loadStudyPreferences() {
  const diffSelect = document.getElementById("prefDifficulty");
  const styleSelect = document.getElementById("prefAiStyle");
  const savedDiff = localStorage.getItem("nis_pref_difficulty") || "Intermediate";
  const savedStyle = localStorage.getItem("nis_pref_ai_style") || "balanced";
  if (diffSelect) diffSelect.value = savedDiff;
  if (styleSelect) styleSelect.value = savedStyle;
}

function saveStudyPreferences() {
  const diffSelect = document.getElementById("prefDifficulty");
  const styleSelect = document.getElementById("prefAiStyle");
  if (diffSelect) localStorage.setItem("nis_pref_difficulty", diffSelect.value);
  if (styleSelect) localStorage.setItem("nis_pref_ai_style", styleSelect.value);
}

async function loadUserProfileStats() {
  try {
    const res = await fetch("/api/user/stats", { headers: getAuthHeaders() });
    if (res.ok) {
      const data = await res.json();
      const stats = data.stats || {};
      
      const nbEl = document.getElementById("profileStatNotebooks");
      const pgEl = document.getElementById("profileStatPages");
      const fldEl = document.getElementById("profileStatFolders");
      if (nbEl) nbEl.textContent = stats.total_notebooks || 0;
      if (pgEl) pgEl.textContent = stats.total_pages || 0;
      if (fldEl) fldEl.textContent = stats.total_folders || 0;

      const ocrUsage = stats.ocr_usage || 0;
      const ocrLimit = stats.ocr_limit || 50;
      const aiUsage = stats.ai_usage || 0;
      const aiLimit = stats.ai_limit || 50;

      const ocrLabel = document.getElementById("profileOcrQuotaLabel");
      const ocrBar = document.getElementById("profileOcrQuotaBar");
      if (ocrLabel) ocrLabel.textContent = `${ocrUsage} / ${ocrLimit} uploads`;
      if (ocrBar) {
        const pct = Math.min(100, Math.round((ocrUsage / ocrLimit) * 100));
        ocrBar.style.width = `${pct}%`;
        ocrBar.style.background = pct >= 90 ? "#ef4444" : "var(--primary)";
      }

      const aiLabel = document.getElementById("profileAiQuotaLabel");
      const aiBar = document.getElementById("profileAiQuotaBar");
      if (aiLabel) aiLabel.textContent = `${aiUsage} / ${aiLimit} requests`;
      if (aiBar) {
        const pct = Math.min(100, Math.round((aiUsage / aiLimit) * 100));
        aiBar.style.width = `${pct}%`;
        aiBar.style.background = pct >= 90 ? "#ef4444" : "#10b981";
      }

      const memberBadge = document.getElementById("profileMemberSinceBadge");
      if (memberBadge && stats.member_since) {
        memberBadge.textContent = `Member since ${stats.member_since}`;
      }
    }
  } catch (err) {
    console.warn("Could not load user stats:", err);
  }
}

async function saveProfileName() {
  const input = document.getElementById("profileNameInput");
  if (!input) return;
  const newName = input.value.trim();
  if (!newName) {
    alert("Please enter a valid name.");
    return;
  }
  try {
    const res = await fetch("/api/user/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName })
    });
    const data = await res.json();
    if (res.ok) {
      if (currentUser) currentUser.name = newName;
      updateUserWidget(currentUser);
      const nameDisplay = document.getElementById("profileNameDisplay");
      if (nameDisplay) nameDisplay.textContent = newName;
      const cached = localStorage.getItem("nis_user");
      if (cached) {
        try {
          const u = JSON.parse(cached);
          u.name = newName;
          localStorage.setItem("nis_user", JSON.stringify(u));
        } catch (e) {}
      }
      alert("✅ Name updated successfully!");
    } else {
      alert("Error: " + (data.detail || "Failed to update profile name."));
    }
  } catch (err) {
    alert("Network error updating profile.");
  }
}

async function saveNewPassword() {
  const currentPw = document.getElementById("profileCurrentPw").value;
  const newPw = document.getElementById("profileNewPw").value;
  const confirmPw = document.getElementById("profileConfirmPw").value;
  const alertMsg = document.getElementById("profileAlertMsg");

  if (alertMsg) alertMsg.style.display = "none";

  if (!newPw || newPw.length < 6) {
    showProfileAlert("New password must be at least 6 characters.", "error");
    return;
  }
  if (newPw !== confirmPw) {
    showProfileAlert("New passwords do not match. Please re-enter.", "error");
    return;
  }

  try {
    let supaSuccess = false;
    if (supabaseClient) {
      try {
        const { data: supaData, error } = await supabaseClient.auth.updateUser({
          password: newPw
        });
        if (!error && supaData) {
          supaSuccess = true;
        }
      } catch (e) {
        console.warn("Supabase update password failed:", e);
      }
    }

    const res = await fetch("/api/user/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ current_password: currentPw, new_password: newPw })
    });
    const data = await res.json();
    if (res.ok || supaSuccess) {
      document.getElementById("profileCurrentPw").value = "";
      document.getElementById("profileNewPw").value = "";
      document.getElementById("profileConfirmPw").value = "";
      showProfileAlert("✅ Password updated successfully!", "success");
    } else {
      showProfileAlert("⚠️ " + (data.detail || "Failed to update password."), "error");
    }
  } catch (err) {
    showProfileAlert("Network error updating password.", "error");
  }
}

function showProfileAlert(msg, type) {
  const alertMsg = document.getElementById("profileAlertMsg");
  if (alertMsg) {
    alertMsg.textContent = msg;
    alertMsg.style.display = "block";
    if (type === "success") {
      alertMsg.style.background = "var(--success-bg)";
      alertMsg.style.color = "var(--success)";
      alertMsg.style.border = "1px solid var(--success)";
    } else {
      alertMsg.style.background = "var(--error-bg)";
      alertMsg.style.color = "var(--error)";
      alertMsg.style.border = "1px solid var(--error)";
    }
  }
}

async function logoutUser() {
  try {
    if (supabaseClient) {
      await supabaseClient.auth.signOut();
    }
    localStorage.removeItem("nis_token");
    localStorage.removeItem("nis_user");
    localStorage.removeItem("sb_access_token");
    const headers = getAuthHeaders();
    await fetch("/api/auth/logout", { method: "POST", headers });
  } catch (e) {
    console.error("Logout error:", e);
  } finally {
    window.location.href = "/";
  }
}

// ==================== Theme & Mobile Navigation ====================
function initTheme() {
  const savedTheme = localStorage.getItem("nis_theme") || "light";
  document.documentElement.setAttribute("data-theme", savedTheme);
  updateThemeIcon(savedTheme);
}

function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute("data-theme") || "light";
  const newTheme = currentTheme === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", newTheme);
  localStorage.setItem("nis_theme", newTheme);
  updateThemeIcon(newTheme);
}

function updateThemeIcon(theme) {
  const btn = document.getElementById("themeToggleBtn");
  if (btn) {
    btn.innerHTML = theme === "dark" ? "☀️" : "🌙";
  }
}

function toggleMobileSidebar() {
  const sidebar = document.getElementById("sidebar");
  if (sidebar) {
    sidebar.classList.toggle("mobile-open");
  }
}

const VIEW_TITLES = {
  "view-upload": "Upload & Status",
  "view-folders": "Folders & Collections",
  "view-viewer": "Notebook Viewer",
  "view-search": "Search View",
  "view-ai": "Ask / Quiz",
  "view-code": "Code Lab"
};

function updateViewTitle(viewId) {
  const titleEl = document.getElementById("viewTitle");
  if (titleEl && VIEW_TITLES[viewId]) {
    titleEl.textContent = VIEW_TITLES[viewId];
  }
}

// ==================== Tabs Management ====================
function initTabs() {
  const tabs = document.querySelectorAll(".tab-btn");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".view-panel").forEach(p => p.classList.remove("active"));
      
      tab.classList.add("active");
      const targetId = tab.dataset.target;
      const targetPanel = document.getElementById(targetId);
      if (targetPanel) {
        targetPanel.classList.add("active");
      }
      
      updateViewTitle(targetId);

      // Close mobile sidebar on nav selection
      const sidebar = document.getElementById("sidebar");
      if (sidebar) sidebar.classList.remove("mobile-open");

      if (targetId === "view-viewer") {
        renderFolderSelectAndPills();
        renderThumbnails();
        renderViewerPage(currentPage);
      } else if (targetId === "view-folders") {
        fetchFolders();
        renderFolderSelectAndPills();
      } else if (targetId === "view-upload") {
        renderFolderSelectAndPills();
        renderThumbnails();
      } else if (targetId === "view-ai") {
        populateQuizSourceFilters();
      } else if (targetId === "view-code") {
        syncCodeEditor();
      }
      
      renderMath();
    });
  });
}

function switchTab(tabId) {
  const btn = document.querySelector(`.tab-btn[data-target="${tabId}"]`);
  if (btn) btn.click();
}

// ==================== Modal Controls ====================
function initModals() {
  const overlays = document.querySelectorAll(".modal-overlay");
  overlays.forEach(overlay => {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) {
        closeModal(overlay.id);
      }
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal-overlay.active").forEach(m => closeModal(m.id));
    }
  });
}

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add("active");
    modal.style.display = "flex";
    const firstInput = modal.querySelector("input");
    if (firstInput) firstInput.focus();
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.remove("active");
    modal.style.display = "none";
  }
}

// ==================== Stat Card Navigation ====================
function onStatCardClick(type) {
  if (type === "courses" || type === "folders") {
    switchTab("view-folders");
  } else if (type === "digitized") {
    const grid = document.getElementById("thumbnailGrid");
    if (grid) {
      grid.scrollIntoView({ behavior: "smooth" });
    }
  } else if (type === "quizzes") {
    switchTab("view-ai");
  } else if (type === "code") {
    switchTab("view-code");
  }
}

// ==================== Math & LaTeX Rendering ====================
function renderMath() {
  if (window.renderMathInElement) {
    window.renderMathInElement(document.body, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false }
      ],
      throwOnError: false
    });
  }
}

// ==================== Notebooks Management ====================
async function fetchNotebooks() {
  try {
    const res = await fetch("/api/notebooks", { headers: getAuthHeaders() });
    const list = await res.json();
    const select = document.getElementById("notebookSelect");
    const sidebarList = document.getElementById("sidebarNotebooksList");
    
    if (list && list.length > 0) {
      if (!list.some(nb => nb.id === currentNotebook)) {
        currentNotebook = list[0].id;
      }
    }
    
    if (select) {
      select.innerHTML = "";
      list.forEach(nb => {
        const opt = document.createElement("option");
        opt.value = nb.id;
        opt.textContent = `${nb.name} (${nb.page_count || 0} pgs)`;
        if (nb.id === currentNotebook) opt.selected = true;
        select.appendChild(opt);
      });
    }

    if (sidebarList) {
      sidebarList.innerHTML = "";
      list.forEach(nb => {
        const item = document.createElement("div");
        item.className = `sidebar-nb-item ${nb.id === currentNotebook ? 'active' : ''}`;
        item.innerHTML = `
          <div class="sidebar-nb-info" onclick="onNotebookChange('${nb.id}')">
            <span class="nb-icon">📓</span>
            <span class="nb-name">${nb.name}</span>
            <span class="nb-count">${nb.page_count || 0}</span>
          </div>
          ${list.length > 1 ? `<button class="sidebar-nb-del-btn" onclick="deleteNotebook(event, '${nb.id}')" title="Delete Notebook">🗑️</button>` : ''}
        `;
        sidebarList.appendChild(item);
      });
    }
  } catch (e) {
    console.error("Error fetching notebooks", e);
  }
}

function onNotebookChange(newId) {
  currentNotebook = newId;
  localStorage.setItem("nis_current_notebook", newId);
  currentPage = 1;
  selectedPages.clear();
  fetchNotebooks();
  loadPages();
}

async function createNewNotebook() {
  const nameInput = document.getElementById("newNotebookName");
  const name = nameInput.value.trim();
  if (!name) return;
  
  const id = name.toLowerCase().replace(/[^a-z0-9]/g, "_") + "_" + Date.now().toString().slice(-4);
  try {
    const res = await fetch("/api/notebooks", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ id, name })
    });
    if (res.ok) {
      nameInput.value = "";
      closeModal("newNotebookModal");
      currentNotebook = id;
      localStorage.setItem("nis_current_notebook", id);
      await fetchNotebooks();
      loadPages();
    }
  } catch (e) {
    alert("Failed to create notebook.");
  }
}

async function deleteNotebook(e, notebookId) {
  if (e) e.stopPropagation();
  if (!confirm("Are you sure you want to delete this notebook and all its notes? This action cannot be undone.")) {
    return;
  }
  try {
    const res = await fetch(`/api/notebooks/${notebookId}`, {
      method: "DELETE",
      headers: getAuthHeaders()
    });
    if (res.ok) {
      currentNotebook = "current_session";
      await fetchNotebooks();
      loadPages();
    } else {
      alert("Could not delete notebook.");
    }
  } catch (err) {
    alert("Error deleting notebook.");
  }
}

// ==================== Folder Management & State ====================
let activeFolderFilter = "all";
let availableFolders = [];

async function fetchFolders() {
  try {
    const res = await fetch(`/api/folders?notebook_id=${currentNotebook}`, { headers: getAuthHeaders() });
    if (res.ok) {
      const data = await res.json();
      availableFolders = data.folders || [];
    }
  } catch (e) {
    availableFolders = [];
  }
  renderFolderSelectAndPills();
}

function getFilteredPages() {
  if (activeFolderFilter === "__unassigned__") {
    return pagesData.filter(p => !p.folder);
  } else if (activeFolderFilter && activeFolderFilter !== "all") {
    return pagesData.filter(p => (p.folder || "") === activeFolderFilter);
  }
  return pagesData;
}

function openFolderInViewer(folderName) {
  activeFolderFilter = folderName;
  renderFolderSelectAndPills();
  renderThumbnails();
  switchTab("view-viewer");
  const filtered = getFilteredPages();
  if (filtered.length > 0) {
    renderViewerPage(filtered[0].page_number);
  } else {
    clearViewerDisplay();
  }
}

function onViewerFolderFilterChange(val) {
  activeFolderFilter = val;
  renderFolderSelectAndPills();
  renderThumbnails();
  const filtered = getFilteredPages();
  if (filtered.length > 0) {
    renderViewerPage(filtered[0].page_number);
  } else {
    clearViewerDisplay();
  }
}

function exportCurrentViewerFolderPdf() {
  exportFolderPdf(activeFolderFilter || "all");
}

async function exportFolderPdf(folderName) {
  try {
    const safeFolder = folderName || "all";
    const exportBtn = document.getElementById("exportViewerPdfBtn");
    const origText = exportBtn ? exportBtn.textContent : "";
    if (exportBtn) {
      exportBtn.disabled = true;
      exportBtn.textContent = "⏳ Generating PDF...";
    }
    
    const url = `/api/folders/${encodeURIComponent(safeFolder)}/export-pdf?notebook_id=${encodeURIComponent(currentNotebook)}`;
    const res = await fetch(url, { headers: getAuthHeaders() });
    
    if (exportBtn) {
      exportBtn.disabled = false;
      exportBtn.textContent = origText;
    }
    
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || "Could not generate collective PDF.");
      return;
    }
    
    const blob = await res.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = downloadUrl;
    const cleanTitle = safeFolder === 'all' ? 'All_Notes' : (safeFolder === '__unassigned__' ? 'Unassigned_Notes' : safeFolder.replace(/[^\w\-]/g, '_'));
    a.download = `${cleanTitle}_collective_notes.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(downloadUrl);
  } catch (err) {
    console.error("Export PDF error:", err);
    alert("Network error while generating collective PDF.");
  }
}

async function autoAssignCurrentPageToFolder(folderVal) {
  if (pagesData.length === 0) return;
  const page = pagesData.find(p => p.page_number === currentPage);
  if (!page) return;

  const prevFolder = page.folder || "";
  page.folder = folderVal || "";
  
  setCachedPages(pagesData);
  renderFolderSelectAndPills();
  renderThumbnails();

  try {
    const res = await fetch(`/api/pages/${currentPage}/folder?notebook_id=${currentNotebook}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ folder: folderVal || "" })
    });
    if (!res.ok) {
      page.folder = prevFolder;
      setCachedPages(pagesData);
      renderFolderSelectAndPills();
      renderThumbnails();
      alert("Failed to update folder assignment.");
    } else {
      await fetchFolders();
      populateQuizSourceFilters();
      updateDashboardStats();
    }
  } catch (err) {
    page.folder = prevFolder;
    setCachedPages(pagesData);
    renderFolderSelectAndPills();
    renderThumbnails();
    alert("Network error updating folder assignment.");
  }
}

function toggleViewerMoreMenu(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById("viewerMoreMenu");
  if (!menu) return;
  menu.style.display = menu.style.display === "block" ? "none" : "block";
}

document.addEventListener("click", () => {
  const menu = document.getElementById("viewerMoreMenu");
  if (menu && menu.style.display === "block") {
    menu.style.display = "none";
  }
});

function renderFolderSelectAndPills() {
  const uploadSelect = document.getElementById("uploadFolderSelect");
  if (uploadSelect) {
    const currentVal = uploadSelect.value || "";
    uploadSelect.innerHTML = `<option value="">No Folder (Unassigned)</option>`;
    availableFolders.forEach(f => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = `📁 ${f}`;
      if (f === currentVal) opt.selected = true;
      uploadSelect.appendChild(opt);
    });
  }

  const viewerSelect = document.getElementById("viewerFolderSelect");
  if (viewerSelect) {
    const currentVal = viewerSelect.value || "";
    viewerSelect.innerHTML = `<option value="">Unassigned</option>`;
    availableFolders.forEach(f => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = `📁 ${f}`;
      if (f === currentVal) opt.selected = true;
      viewerSelect.appendChild(opt);
    });
    const curPage = pagesData.find(p => p.page_number === currentPage);
    if (curPage) {
      viewerSelect.value = curPage.folder || "";
    }
  }

  const viewerFilterSelect = document.getElementById("viewerFolderFilterSelect");
  if (viewerFilterSelect) {
    viewerFilterSelect.innerHTML = `<option value="all" ${activeFolderFilter === 'all' ? 'selected' : ''}>📁 All Notes (${pagesData.length})</option>`;
    availableFolders.forEach(f => {
      const count = pagesData.filter(p => (p.folder || "") === f).length;
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = `📁 ${f} (${count})`;
      if (f === activeFolderFilter) opt.selected = true;
      viewerFilterSelect.appendChild(opt);
    });
    const unassignedCount = pagesData.filter(p => !p.folder).length;
    if (unassignedCount > 0) {
      const opt = document.createElement("option");
      opt.value = "__unassigned__";
      opt.textContent = `📄 Unassigned (${unassignedCount})`;
      if (activeFolderFilter === "__unassigned__") opt.selected = true;
      viewerFilterSelect.appendChild(opt);
    }
  }

  const pillsContainer = document.getElementById("folderFilterPills");
  if (pillsContainer) {
    pillsContainer.innerHTML = "";
    
    // "All Folders" pill
    const allPill = document.createElement("button");
    allPill.className = `folder-pill ${activeFolderFilter === 'all' ? 'active' : ''}`;
    allPill.setAttribute("data-folder", "all");
    allPill.onclick = () => filterByFolder('all');
    allPill.innerHTML = `📁 All Notes (${pagesData.length})`;
    pillsContainer.appendChild(allPill);

    // Individual folder pills
    availableFolders.forEach(f => {
      const count = pagesData.filter(p => (p.folder || "") === f).length;
      const pill = document.createElement("button");
      pill.className = `folder-pill ${activeFolderFilter === f ? 'active' : ''}`;
      pill.setAttribute("data-folder", f);
      pill.onclick = () => filterByFolder(f);
      pill.innerHTML = `📁 ${escapeHtml(f)} (${count})`;
      pillsContainer.appendChild(pill);
    });

    const unassignedCount = pagesData.filter(p => !p.folder).length;
    if (unassignedCount > 0 && availableFolders.length > 0) {
      const unassignedPill = document.createElement("button");
      unassignedPill.className = `folder-pill ${activeFolderFilter === '__unassigned__' ? 'active' : ''}`;
      unassignedPill.setAttribute("data-folder", "__unassigned__");
      unassignedPill.onclick = () => filterByFolder('__unassigned__');
      unassignedPill.innerHTML = `📄 Unassigned (${unassignedCount})`;
      pillsContainer.appendChild(unassignedPill);
    }
  }

  const foldersGrid = document.getElementById("foldersGrid");
  if (foldersGrid) {
    foldersGrid.innerHTML = "";
    
    // "All Notes" overview card
    const allCard = document.createElement("div");
    allCard.className = "card";
    allCard.style = "padding: 20px; display: flex; flex-direction: column; justify-content: space-between; border-radius: var(--radius-md); border: 1px solid var(--border); background: var(--card-bg); min-height: 140px;";
    allCard.innerHTML = `
      <div>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-size: 2rem;">📂</span>
          <span class="badge badge-primary">${pagesData.length} Notes</span>
        </div>
        <h3 style="margin: 0; font-size: 1.1rem; color: var(--text);">All Notes</h3>
        <p style="color: var(--text-muted); font-size: 0.85rem; margin: 6px 0 16px 0;">Complete collection across all notes & uploads.</p>
      </div>
      <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: auto;">
        <button class="btn btn-primary btn-sm" onclick="openFolderInViewer('all')">👁️ View in Viewer</button>
        <button class="btn btn-secondary btn-sm" onclick="exportFolderPdf('all')">📥 Export PDF</button>
      </div>
    `;
    foldersGrid.appendChild(allCard);

    availableFolders.forEach(f => {
      const count = pagesData.filter(p => (p.folder || "") === f).length;
      const card = document.createElement("div");
      card.className = "card";
      card.style = "position: relative; padding: 20px; display: flex; flex-direction: column; justify-content: space-between; border-radius: var(--radius-md); border: 1px solid var(--border); background: var(--card-bg); min-height: 140px;";
      card.innerHTML = `
        <div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <span style="font-size: 2rem;">📁</span>
            <button onclick="deleteFolder(event, '${escapeHtml(f)}')" style="background: rgba(239, 68, 68, 0.12); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 4px; padding: 2px 6px; font-size: 0.75rem; cursor: pointer;" title="Delete Folder">🗑️ Delete</button>
          </div>
          <h3 style="margin: 0; font-size: 1.1rem; color: var(--text); word-break: break-word;">${escapeHtml(f)}</h3>
          <p style="color: var(--text-muted); font-size: 0.85rem; margin: 6px 0 16px 0;">${count} note${count === 1 ? '' : 's'} inside this collection</p>
        </div>
        <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: auto;">
          <button class="btn btn-primary btn-sm" onclick="openFolderInViewer('${escapeHtml(f)}')">👁️ View in Viewer</button>
          <button class="btn btn-secondary btn-sm" onclick="exportFolderPdf('${escapeHtml(f)}')">📥 Export PDF</button>
        </div>
      `;
      foldersGrid.appendChild(card);
    });

    const unassignedCount = pagesData.filter(p => !p.folder).length;
    if (unassignedCount > 0) {
      const unassignedCard = document.createElement("div");
      unassignedCard.className = "card";
      unassignedCard.style = "padding: 20px; display: flex; flex-direction: column; justify-content: space-between; border-radius: var(--radius-md); border: 1px solid var(--border); background: var(--card-bg); min-height: 140px;";
      unassignedCard.innerHTML = `
        <div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <span style="font-size: 2rem;">📄</span>
            <span class="badge badge-secondary">${unassignedCount} Notes</span>
          </div>
          <h3 style="margin: 0; font-size: 1.1rem; color: var(--text);">Unassigned Notes</h3>
          <p style="color: var(--text-muted); font-size: 0.85rem; margin: 6px 0 16px 0;">Notes not yet assigned to a specific folder.</p>
        </div>
        <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: auto;">
          <button class="btn btn-primary btn-sm" onclick="openFolderInViewer('__unassigned__')">👁️ View in Viewer</button>
          <button class="btn btn-secondary btn-sm" onclick="exportFolderPdf('__unassigned__')">📥 Export PDF</button>
        </div>
      `;
      foldersGrid.appendChild(unassignedCard);
    }
  }
}

function filterByFolder(folderName) {
  activeFolderFilter = folderName;
  renderFolderSelectAndPills();
  renderThumbnails();
}

async function createNewFolder() {
  const nameInput = document.getElementById("newFolderName");
  if (!nameInput) return;
  const name = nameInput.value.trim();
  if (!name) {
    alert("Please enter a folder name.");
    return;
  }
  try {
    const res = await fetch("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ name })
    });
    if (res.ok) {
      if (!availableFolders.includes(name)) {
        availableFolders.push(name);
      }
      activeFolderFilter = name;
      nameInput.value = "";
      closeModal("newFolderModal");
      renderFolderSelectAndPills();
      const uploadSelect = document.getElementById("uploadFolderSelect");
      if (uploadSelect) uploadSelect.value = name;
      renderThumbnails();
    } else {
      const data = await res.json();
      alert("Error: " + (data.detail || "Could not create folder."));
    }
  } catch (err) {
    alert("Failed to create folder.");
  }
}

async function deleteFolder(e, folderName) {
  if (e) e.stopPropagation();
  if (!confirm(`Are you sure you want to delete folder "${folderName}"? Notes inside will not be deleted; they will be moved to Unassigned.`)) {
    return;
  }
  try {
    const res = await fetch(`/api/folders/${encodeURIComponent(folderName)}?notebook_id=${currentNotebook}`, {
      method: "DELETE",
      headers: getAuthHeaders()
    });
    if (res.ok) {
      if (activeFolderFilter === folderName) {
        activeFolderFilter = "all";
      }
      await fetchFolders();
      await loadPages();
    } else {
      alert("Could not delete folder.");
    }
  } catch (err) {
    alert("Network error deleting folder.");
  }
}

async function saveCurrentPageToFolder() {
  if (pagesData.length === 0) return;
  const select = document.getElementById("viewerFolderSelect");
  if (!select) return;
  const folderVal = select.value.trim();
  await updatePageFolder(currentPage, folderVal);
  alert(folderVal ? `✅ Note saved to folder "${folderVal}"!` : "✅ Note unassigned from folder.");
}

async function updatePageFolder(pageNumber, newFolder) {
  try {
    const res = await fetch(`/api/pages/${pageNumber}/folder?notebook_id=${currentNotebook}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ folder: newFolder })
    });
    if (res.ok) {
      const p = pagesData.find(page => page.page_number === pageNumber);
      if (p) p.folder = newFolder;
      setCachedPages(pagesData);
      await fetchFolders();
      renderFolderSelectAndPills();
      renderThumbnails();
      updateHeaderStats();
      updateDashboardStats();
      populateQuizSourceFilters();
      const viewerSel = document.getElementById("viewerFolderSelect");
      if (viewerSel && currentPage === pageNumber) viewerSel.value = newFolder || "";
      if (activeFolderFilter && activeFolderFilter !== "all") {
        const filtered = getFilteredPages();
        if (filtered.length > 0 && !filtered.some(pg => pg.page_number === currentPage)) {
          renderViewerPage(filtered[0].page_number);
        } else if (filtered.length === 0) {
          clearViewerDisplay();
        }
      }
    }
  } catch (e) {
    alert("Failed to move note to folder.");
  }
}

function updateHeaderStats() {
  const badge = document.getElementById("pageCountBadge");
  if (badge) badge.textContent = `📄 ${pagesData.length} Page${pagesData.length === 1 ? '' : 's'}`;
  
  const codeBadge = document.getElementById("codeCountBadge");
  const codeCount = pagesData.filter(p => p.is_code).length;
  if (codeBadge) {
    if (codeCount > 0) {
      codeBadge.style.display = "inline-flex";
      codeBadge.textContent = `💻 ${codeCount} Code Snippet${codeCount === 1 ? '' : 's'}`;
    } else {
      codeBadge.style.display = "none";
    }
  }
  updateDashboardStats();
}

function updateSelectedControls() {
  const btn = document.getElementById("deleteSelectedBtn");
  const countText = document.getElementById("selectedCountText");
  const checkbox = document.getElementById("selectAllCheckbox");

  if (countText) countText.textContent = selectedPages.size;
  if (btn) {
    btn.style.display = selectedPages.size > 0 ? "inline-flex" : "none";
    const isCurrentFilterFolder = Boolean(activeFolderFilter && activeFolderFilter !== "all" && activeFolderFilter !== "__unassigned__");
    if (!isCurrentFilterFolder) {
      const folderProtectedCount = Array.from(selectedPages).filter(num => {
        const p = pagesData.find(page => page.page_number === num);
        return p && p.folder && p.folder.trim() !== "";
      }).length;
      if (folderProtectedCount > 0) {
        btn.title = `${folderProtectedCount} note(s) in folders are protected and will be preserved; only unassigned scans will be deleted.`;
      } else {
        btn.title = "Delete selected unassigned scans";
      }
    } else {
      btn.title = `Delete selected notes from folder "${activeFolderFilter}"`;
    }
  }
  if (checkbox) checkbox.checked = pagesData.length > 0 && selectedPages.size === pagesData.length;
}

function toggleSelectAll(checked) {
  if (checked) {
    pagesData.forEach(p => selectedPages.add(p.page_number));
  } else {
    selectedPages.clear();
  }
  updateSelectedControls();
  renderThumbnails();
}

function toggleSelectPage(pageNumber) {
  if (selectedPages.has(pageNumber)) {
    selectedPages.delete(pageNumber);
  } else {
    selectedPages.add(pageNumber);
  }
  updateSelectedControls();
  renderThumbnails();
}

function renderDocumentTags() {
  const container = document.getElementById("documentTagsContainer");
  if (!container) return;
  container.innerHTML = "";

  const docsMap = {};
  pagesData.forEach(p => {
    const fn = p.source_filename || "Unknown Document";
    docsMap[fn] = (docsMap[fn] || 0) + 1;
  });

  Object.entries(docsMap).forEach(([fn, count]) => {
    const tag = document.createElement("div");
    tag.style.cssText = "display: flex; align-items: center; gap: 6px; padding: 4px 10px; background: var(--input-bg); border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: 0.82rem; font-weight: 500; color: var(--text);";
    tag.innerHTML = `
      <span>📄 ${escapeHtml(fn)} (${count} pgs)</span>
      <button onclick="deleteDocument('${escapeHtml(fn)}')" style="background: none; border: none; cursor: pointer; color: var(--error); opacity: 0.7; font-size: 0.9rem;" title="Delete all pages from this file">✕</button>
    `;
    container.appendChild(tag);
  });
}

function renderThumbnails() {
  const grid = document.getElementById("thumbnailGrid");
  if (!grid) return;
  grid.innerHTML = "";

  let filteredPages = pagesData;
  if (activeFolderFilter === "__unassigned__") {
    filteredPages = pagesData.filter(p => !p.folder);
  } else if (activeFolderFilter !== "all") {
    filteredPages = pagesData.filter(p => (p.folder || "") === activeFolderFilter);
  }

  if (filteredPages.length === 0) {
    const label = activeFolderFilter === 'all' ? 'this notebook' : (activeFolderFilter === '__unassigned__' ? 'Unassigned' : `folder "${activeFolderFilter}"`);
    grid.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 40px 20px; text-align: center; color: var(--text-muted); background: var(--input-bg); border: 2px dashed var(--border); border-radius: var(--radius-md);">
        <div style="font-size: 2rem; margin-bottom: 8px;">📭</div>
        <div style="font-weight: 600; font-size: 1rem; margin-bottom: 4px;">No pages in ${escapeHtml(label)}</div>
        <div style="font-size: 0.85rem;">Upload note scans or PDFs above to get started!</div>
      </div>
    `;
    return;
  }

  filteredPages.forEach(p => {
    const card = document.createElement("div");
    card.className = `thumbnail-card ${p.page_number === currentPage ? 'active' : ''} ${selectedPages.has(p.page_number) ? 'selected' : ''}`;
    card.style.cssText = "background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; position: relative; transition: all 0.2s ease; display: flex; flex-direction: column;";

    const isSelected = selectedPages.has(p.page_number);
    const folderName = p.folder || "";
    const isFolderSaved = Boolean(p.folder && p.folder.trim() !== "");
    const isCurrentFilterFolder = Boolean(activeFolderFilter && activeFolderFilter !== "all" && activeFolderFilter !== "__unassigned__");
    const canDeleteHere = !isFolderSaved || (isCurrentFilterFolder && activeFolderFilter === p.folder);

    let folderOptions = `<option value="" ${!folderName ? 'selected' : ''}>Unassigned</option>` +
      availableFolders.map(f => `<option value="${escapeHtml(f)}" ${f === folderName ? 'selected' : ''}>📁 ${escapeHtml(f)}</option>`).join("");

    const deleteBtnHtml = canDeleteHere
      ? `<button onclick="event.stopPropagation(); deletePage(${p.page_number})" style="background: rgba(239, 68, 68, 0.85); color: #fff; border: none; border-radius: 4px; padding: 2px 6px; font-size: 0.75rem; cursor: pointer;" title="${isFolderSaved ? `Delete from folder '${escapeHtml(p.folder)}'` : 'Delete Page'}">🗑️</button>`
      : `<button onclick="event.stopPropagation(); deletePage(${p.page_number})" style="background: rgba(99, 102, 241, 0.2); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.4); border-radius: 4px; padding: 2px 6px; font-size: 0.75rem; cursor: pointer;" title="🔒 Saved in folder '${escapeHtml(p.folder)}' — protected from deletion in Digitized feed">🔒</button>`;

    card.innerHTML = `
      <div style="position: absolute; top: 8px; left: 8px; z-index: 5;">
        <input type="checkbox" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation(); toggleSelectPage(${p.page_number})" style="width: 18px; height: 18px; cursor: pointer;">
      </div>
      <div style="position: absolute; top: 8px; right: 8px; z-index: 5;">
        ${deleteBtnHtml}
      </div>

      <div onclick="jumpToPage(${p.page_number})" style="cursor: pointer; flex: 1; display: flex; flex-direction: column;">
        <div style="height: 160px; background: #000000; display: flex; align-items: center; justify-content: center; overflow: hidden; border-bottom: 1px solid var(--border);">
          ${p.image_url ? `<img src="${p.image_url}" alt="Page ${p.page_number}" style="max-height: 100%; max-width: 100%; object-fit: contain;">` : `<div style="color: var(--text-muted); font-size: 0.85rem;">📷 No Image Preview</div>`}
        </div>
        
        <div style="padding: 10px; display: flex; flex-direction: column; gap: 4px;">
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 6px;">
            <span style="font-weight: 700; font-size: 0.9rem; color: var(--primary);">Page ${p.page_number}</span>
            <div style="display: flex; gap: 4px; align-items: center;">
              ${isFolderSaved ? `<span class="badge" style="background: rgba(99, 102, 241, 0.2); color: #a5b4fc; border: 1px solid rgba(99, 102, 241, 0.35); font-size: 0.7rem;" title="Stored in folder '${escapeHtml(p.folder)}'">📁 ${escapeHtml(p.folder)}</span>` : ''}
              <span class="badge ${p.is_code ? 'badge-code' : 'badge-primary'}" style="font-size: 0.7rem;">${p.is_code ? '💻 Code' : '📄 Text'}</span>
            </div>
          </div>
          
          <div style="font-size: 0.75rem; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escapeHtml(p.source_filename)}">
            ${escapeHtml(p.source_filename)}
          </div>
        </div>
      </div>

      <!-- Folder selector dropdown for moving page -->
      <div style="padding: 0 10px 10px 10px; border-top: 1px solid var(--border); margin-top: auto; padding-top: 6px;">
        <div style="display: flex; align-items: center; gap: 4px;">
          <span style="font-size: 0.72rem; color: var(--text-muted);">📁 Folder:</span>
          <select onchange="event.stopPropagation(); updatePageFolder(${p.page_number}, this.value)" style="font-size: 0.75rem; padding: 2px 4px; border-radius: 4px; background: var(--input-bg); border: 1px solid var(--border); color: var(--text); flex: 1;">
            ${folderOptions}
          </select>
        </div>
      </div>
    `;

    grid.appendChild(card);
  });
}

// ==================== Pages & OCR Workflow ====================
async function loadPages() {
  try {
    await fetchFolders();
    const res = await fetch(`/api/pages?notebook_id=${currentNotebook}`, { headers: getAuthHeaders() });
    let fetched = [];
    if (res.ok) {
      fetched = await res.json();
    }
    
    const cached = getCachedPages();
    if (fetched && fetched.length > 0) {
      pagesData = fetched;
      setCachedPages(pagesData);
    } else if (cached && cached.length > 0) {
      // Server returned 0 pages (e.g. cold start on Vercel), restore from local cache!
      pagesData = cached;
      try {
        fetch("/api/pages/restore", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...getAuthHeaders() },
          body: JSON.stringify({ notebook_id: currentNotebook, pages: cached })
        }).catch(() => {});
      } catch (e) {}
    } else {
      pagesData = [];
    }
    
    // Clean up selections that no longer exist
    const validNums = new Set(pagesData.map(p => p.page_number));
    selectedPages = new Set([...selectedPages].filter(x => validNums.has(x)));
    
    updateHeaderStats();
    updateSelectedControls();
    renderFolderSelectAndPills();
    renderDocumentTags();
    renderThumbnails();
    populateQuizSourceFilters();
    if (pagesData.length > 0) {
      if (currentPage > pagesData.length) currentPage = 1;
      renderViewerPage(currentPage);
    } else {
      clearViewerDisplay();
    }
    renderMath();
  } catch (err) {
    console.error("Failed to load pages", err);
    const cached = getCachedPages();
    if (cached && cached.length > 0) {
      pagesData = cached;
      renderThumbnails();
      renderViewerPage(currentPage);
    }
  }
}

function clearViewerDisplay() {
  const indicator = document.getElementById("pageIndicator");
  if (indicator) indicator.textContent = "No pages uploaded";
  const scanImg = document.getElementById("scanImage");
  if (scanImg) scanImg.src = "";
  const editor = document.getElementById("transcriptEditor");
  if (editor) editor.value = "";
  const renderView = document.getElementById("transcriptRenderView");
  if (renderView) renderView.innerHTML = "";
  const strip = document.getElementById("viewerThumbnailsStrip");
  if (strip) strip.style.display = "none";
}

async function updateDashboardStats() {
  try {
    const notebooksRes = await fetch("/api/notebooks", { headers: getAuthHeaders() });
    if (notebooksRes.ok) {
      const notebooks = await notebooksRes.json();
      const nbCountEl = document.getElementById("statNotebooksCount");
      if (nbCountEl) nbCountEl.textContent = notebooks.length;
    }

    const pagesCountEl = document.getElementById("statPagesCount");
    if (pagesCountEl) pagesCountEl.textContent = pagesData.length;

    const foldersCountEl = document.getElementById("statFoldersCount");
    if (foldersCountEl) foldersCountEl.textContent = availableFolders ? availableFolders.length : 0;

    const codeCount = pagesData.filter(p => p.is_code).length;
    const codeCountEl = document.getElementById("statCodeCount");
    if (codeCountEl) codeCountEl.textContent = codeCount;

    const quizCountEl = document.getElementById("statQuizCount");
    if (quizCountEl) quizCountEl.textContent = quizData ? quizData.length : 0;
  } catch (err) {
    console.warn("Error updating dashboard stats:", err);
  }
}

async function deletePage(pageNumber) {
  const page = pagesData.find(p => p.page_number === pageNumber);
  const isFolderSaved = page && page.folder && page.folder.trim() !== "";
  const isCurrentFilterFolder = Boolean(activeFolderFilter && activeFolderFilter !== "all" && activeFolderFilter !== "__unassigned__");

  // If note is saved in a folder, it can ONLY be deleted from within that folder view!
  if (isFolderSaved && (!isCurrentFilterFolder || activeFolderFilter !== page.folder)) {
    alert(`🔒 Note ${pageNumber} is saved in folder "${page.folder}".\n\nNotes saved in folders are protected from accidental removal in the Digitized feed. To delete this note, please open the "${page.folder}" folder in the Folders tab or filter by "${page.folder}" in the Viewer.`);
    return;
  }

  const promptMsg = isFolderSaved
    ? `Are you sure you want to permanently delete Note ${pageNumber} from folder "${page.folder}"?`
    : `Are you sure you want to delete unassigned Page ${pageNumber}?`;

  if (!confirm(promptMsg)) return;

  try {
    const res = await fetch(`/api/pages/${pageNumber}?notebook_id=${currentNotebook}`, { method: "DELETE", headers: getAuthHeaders() });
    if (res.ok) {
      selectedPages.delete(pageNumber);
      pagesData = pagesData.filter(p => p.page_number !== pageNumber);
      setCachedPages(pagesData);
      await fetchNotebooks();
      await loadPages();
    }
  } catch (err) {
    alert("Failed to delete page.");
  }
}

async function deleteCurrentPage() {
  if (pagesData.length === 0) return;
  await deletePage(currentPage);
}

async function deleteSelectedPages() {
  const nums = Array.from(selectedPages);
  if (nums.length === 0) return;

  const isCurrentFilterFolder = Boolean(activeFolderFilter && activeFolderFilter !== "all" && activeFolderFilter !== "__unassigned__");

  let pagesToDelete = [];
  let protectedFolderCount = 0;

  if (isCurrentFilterFolder) {
    // Inside a specific folder view: delete selected items from this folder
    pagesToDelete = nums;
  } else {
    // In the general Digitized feed ("all" or "unassigned"):
    // Protect all notes saved in folders! Only unassigned notes are removed.
    nums.forEach(num => {
      const p = pagesData.find(page => page.page_number === num);
      if (p && p.folder && p.folder.trim() !== "") {
        protectedFolderCount++;
      } else {
        pagesToDelete.push(num);
      }
    });

    if (pagesToDelete.length === 0 && protectedFolderCount > 0) {
      alert(`🔒 All ${protectedFolderCount} selected note(s) are saved in folders and are protected.\n\nNotes saved in folders can only be deleted from within their specific folder.`);
      return;
    }
  }

  const confirmMsg = protectedFolderCount > 0
    ? `Remove ${pagesToDelete.length} unassigned scan(s) from the Digitized section?\n\n(${protectedFolderCount} note(s) saved in folders will be preserved safely).`
    : `Are you sure you want to delete ${pagesToDelete.length} selected note(s)?`;

  if (!confirm(confirmMsg)) return;

  try {
    const res = await fetch(`/api/pages/delete-bulk`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ page_numbers: pagesToDelete, notebook_id: currentNotebook })
    });
    if (res.ok) {
      const numSet = new Set(pagesToDelete);
      pagesData = pagesData.filter(p => !numSet.has(p.page_number));
      setCachedPages(pagesData);
      selectedPages.clear();
      await fetchNotebooks();
      await loadPages();
      if (protectedFolderCount > 0) {
        alert(`✅ Removed ${pagesToDelete.length} unassigned scan(s). Your ${protectedFolderCount} note(s) saved in folders remain safe.`);
      }
    }
  } catch (err) {
    alert("Failed to delete selected pages.");
  }
}

async function deleteDocument(sourceFilename) {
  const docPages = pagesData.filter(p => p.source_filename === sourceFilename);
  const folderSaved = docPages.filter(p => p.folder && p.folder.trim() !== "");
  const unassigned = docPages.filter(p => !p.folder || p.folder.trim() === "");

  if (folderSaved.length > 0) {
    if (unassigned.length === 0) {
      alert(`🔒 All ${docPages.length} page(s) from "${sourceFilename}" are saved in folders.\n\nNotes saved in folders must be deleted from within their respective folders.`);
      return;
    }
    if (!confirm(`Remove ${unassigned.length} unassigned scan(s) from "${sourceFilename}"?\n\nNote: ${folderSaved.length} page(s) saved in folders will be preserved.`)) {
      return;
    }
    try {
      const res = await fetch(`/api/pages/delete-bulk`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ page_numbers: unassigned.map(p => p.page_number), notebook_id: currentNotebook })
      });
      if (res.ok) {
        const delSet = new Set(unassigned.map(p => p.page_number));
        pagesData = pagesData.filter(p => !delSet.has(p.page_number));
        setCachedPages(pagesData);
        await fetchNotebooks();
        await loadPages();
      }
    } catch (e) {
      alert("Failed to delete document pages.");
    }
    return;
  }

  if (!confirm(`Are you sure you want to delete all pages from "${sourceFilename}"?`)) return;
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(sourceFilename)}?notebook_id=${currentNotebook}`, { method: "DELETE", headers: getAuthHeaders() });
    if (res.ok) {
      pagesData = pagesData.filter(p => p.source_filename !== sourceFilename);
      setCachedPages(pagesData);
      await fetchNotebooks();
      await loadPages();
    }
  } catch (err) {
    alert("Failed to delete document.");
  }
}

let currentViewerMode = "render";
let activeSearchHighlightTerm = "";

// Initialize Mermaid.js on load
document.addEventListener("DOMContentLoaded", () => {
  if (window.mermaid) {
    window.mermaid.initialize({
      startOnLoad: false,
      theme: "dark",
      securityLevel: "loose",
      fontFamily: "Inter, sans-serif"
    });
  }
});

function parseBlocks(ocrText) {
  if (!ocrText) return [{ type: "text", content: "" }];
  try {
    const parsed = JSON.parse(ocrText);
    if (parsed && Array.isArray(parsed.blocks)) {
      return parsed.blocks;
    }
    if (Array.isArray(parsed)) return parsed;
  } catch (e) {
    const match = ocrText.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
    if (match) {
      try {
        const p = JSON.parse(match[1]);
        if (p && Array.isArray(p.blocks)) return p.blocks;
      } catch (err) {}
    }
  }
  return [{ type: "text", content: ocrText }];
}

function blocksToCleanMarkdown(blocks) {
  if (!blocks || !Array.isArray(blocks)) return "";
  let lines = [];
  blocks.forEach(b => {
    if (b.type === "diagram" && b.mermaid) {
      lines.push("```mermaid\n" + b.mermaid.trim() + "\n```");
    } else if (b.content || b.text) {
      lines.push((b.content || b.text).trim());
    }
  });
  return lines.join("\n\n").trim();
}

function setViewerMode(mode) {
  currentViewerMode = mode;
  const renderView = document.getElementById("transcriptRenderView");
  const editor = document.getElementById("transcriptEditor");
  const renderBtn = document.getElementById("toggleRenderViewBtn");
  const editBtn = document.getElementById("toggleEditViewBtn");
  
  if (mode === "render") {
    if (renderView) renderView.style.display = "block";
    if (editor) editor.style.display = "none";
    if (renderBtn) {
      renderBtn.style.background = "var(--primary)";
      renderBtn.style.color = "#ffffff";
    }
    if (editBtn) {
      editBtn.style.background = "transparent";
      editBtn.style.color = "var(--text-muted)";
    }
  } else {
    if (renderView) renderView.style.display = "none";
    if (editor) {
      editor.style.display = "block";
    }
    if (renderBtn) {
      renderBtn.style.background = "transparent";
      renderBtn.style.color = "var(--text-muted)";
    }
    if (editBtn) {
      editBtn.style.background = "var(--primary)";
      editBtn.style.color = "#ffffff";
    }
  }
}

function escapeHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function downloadDiagramAsPng(svgWrapperId, filename = "diagram.png") {
  const wrapper = document.getElementById(svgWrapperId);
  if (!wrapper) return;
  const svgEl = wrapper.querySelector("svg");
  if (!svgEl) return;

  try {
    const svgData = new XMLSerializer().serializeToString(svgEl);
    const svgBlob = new Blob([svgData], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(svgBlob);

    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      const bbox = svgEl.getBoundingClientRect();
      const width = Math.max(bbox.width || 800, 600) + 40;
      const height = Math.max(bbox.height || 400, 300) + 40;
      canvas.width = width;
      canvas.height = height;
      
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#0f172a";
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(img, 20, 20);

      URL.revokeObjectURL(url);
      const pngUrl = canvas.toDataURL("image/png");
      const a = document.createElement("a");
      a.href = pngUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    };
    img.src = url;
  } catch (err) {
    console.error("Failed to export diagram as PNG:", err);
  }
}

async function renderMermaidDiagramsInContainer(container, highlightTerm = "") {
  if (!window.mermaid) return;
  const diagElements = container.querySelectorAll(".mermaid-diagram-pending");
  
  for (const el of diagElements) {
    const code = el.getAttribute("data-mermaid");
    const uniqueId = "mermaid_svg_" + Math.random().toString(36).substring(2, 9);
    const wrapperId = uniqueId + "_wrapper";
    
    try {
      const { svg } = await window.mermaid.render(uniqueId, code);
      el.innerHTML = `
        <div id="${wrapperId}" class="diagram-svg-wrapper" style="text-align: center; padding: 16px; background: var(--input-bg); border: 1px solid var(--border); border-radius: var(--radius-md); margin: 14px 0; overflow-x: auto;">
          ${svg}
          <div style="margin-top: 10px; text-align: right;">
            <button class="btn btn-secondary btn-sm" onclick="downloadDiagramAsPng('${wrapperId}', 'diagram_page_${currentPage}.png')">📥 Download PNG</button>
          </div>
        </div>
      `;
      el.classList.remove("mermaid-diagram-pending");

      if (highlightTerm && highlightTerm.trim().length > 1) {
        const termLower = highlightTerm.trim().toLowerCase();
        const svgElement = el.querySelector("svg");
        if (svgElement) {
          const textNodes = svgElement.querySelectorAll("text, tspan, .nodeLabel");
          textNodes.forEach(node => {
            if (node.textContent && node.textContent.toLowerCase().includes(termLower)) {
              node.setAttribute("fill", "#fde047");
              node.style.fill = "#fde047";
              node.style.fontWeight = "bold";
              node.style.filter = "drop-shadow(0 0 6px rgba(253, 224, 71, 0.8))";
            }
          });
        }
      }
    } catch (err) {
      console.error("Mermaid rendering error:", err);
      const errMsg = err.message || (typeof err === "string" ? err : "Syntax error in Mermaid code.");
      el.innerHTML = `
        <div class="diagram-error" style="color: var(--error); background: var(--error-bg); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: var(--radius-sm); padding: 12px; margin: 12px 0; font-size: 0.85rem;">
          ⚠️ <strong>Invalid Diagram Code:</strong> ${escapeHtml(errMsg)}
        </div>
        <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 4px;">Raw Mermaid Code Fallback:</div>
        <pre style="background: var(--code-bg); padding: 12px; border-radius: var(--radius-sm); color: #f8fafc; font-size: 0.82rem; overflow-x: auto; border: 1px solid var(--border); font-family: 'Fira Code', monospace; line-height: 1.5;">${escapeHtml(code)}</pre>
      `;
      el.classList.remove("mermaid-diagram-pending");
    }
  }
}

function jumpToPage(num, highlightTerm = "") {
  currentPage = num;
  activeSearchHighlightTerm = highlightTerm;
  switchTab("view-viewer");
  renderViewerPage(currentPage);
}

function renderViewerThumbnailsStrip(filtered) {
  const strip = document.getElementById("viewerThumbnailsStrip");
  if (!strip) return;
  strip.innerHTML = "";
  if (!filtered || filtered.length === 0) {
    strip.style.display = "none";
    return;
  }
  strip.style.display = "flex";
  filtered.forEach(p => {
    const thumb = document.createElement("button");
    thumb.type = "button";
    thumb.className = `viewer-strip-item ${p.page_number === currentPage ? 'active' : ''}`;
    thumb.style.cssText = `
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      padding: 4px;
      border: 2px solid ${p.page_number === currentPage ? 'var(--primary)' : 'var(--border)'};
      border-radius: var(--radius-sm);
      background: ${p.page_number === currentPage ? 'rgba(99, 102, 241, 0.14)' : 'var(--card-bg)'};
      cursor: pointer;
      flex-shrink: 0;
      width: 68px;
      transition: all 0.15s ease;
    `;
    thumb.onclick = () => renderViewerPage(p.page_number);
    thumb.innerHTML = `
      <div style="width: 100%; height: 46px; background: #000; border-radius: 2px; overflow: hidden; display: flex; align-items: center; justify-content: center;">
        ${p.image_url ? `<img src="${p.image_url}" alt="p${p.page_number}" style="width: 100%; height: 100%; object-fit: cover;">` : `<span style="font-size: 0.7rem; color: #888;">p${p.page_number}</span>`}
      </div>
      <span style="font-size: 0.72rem; font-weight: 600; color: ${p.page_number === currentPage ? 'var(--primary)' : 'var(--text-muted)'};">#${p.page_number}</span>
    `;
    strip.appendChild(thumb);
  });
}

function renderViewerPage(num) {
  const filtered = getFilteredPages();
  if (filtered.length === 0) {
    clearViewerDisplay();
    const indicator = document.getElementById("pageIndicator");
    if (indicator) {
      const folderName = activeFolderFilter === '__unassigned__' ? 'Unassigned' : activeFolderFilter;
      indicator.textContent = activeFolderFilter === 'all' ? "No pages uploaded" : `No pages in "${folderName}"`;
    }
    return;
  }
  const page = filtered.find(p => p.page_number === num) || filtered[0];
  currentPage = page.page_number;
  const currentIdx = filtered.findIndex(p => p.page_number === currentPage);
  
  const folderLabel = (activeFolderFilter && activeFolderFilter !== 'all') 
    ? ` (${activeFolderFilter === '__unassigned__' ? 'Unassigned' : activeFolderFilter})` 
    : '';
  const indicator = document.getElementById("pageIndicator");
  if (indicator) {
    indicator.textContent = `Note ${currentIdx + 1} of ${filtered.length}${folderLabel} (Page #${page.page_number})`;
  }
  
  renderViewerThumbnailsStrip(filtered);
  
  const viewerSel = document.getElementById("viewerFolderSelect");
  if (viewerSel) {
    viewerSel.value = page.folder || "";
  }
  
  const scanImg = document.getElementById("scanImage");
  if (scanImg && page.image_url) {
    scanImg.src = page.image_url;
  }
  
  const editor = document.getElementById("transcriptEditor");
  const blocks = page.blocks || parseBlocks(page.ocr_text);
  if (editor) {
    editor.value = blocksToCleanMarkdown(blocks);
  }
  
  const renderView = document.getElementById("transcriptRenderView");
  const diagramBadge = document.getElementById("diagramDetectedBadge");
  
  let hasDiagram = false;
  if (renderView) {
    renderView.innerHTML = "";
    blocks.forEach(block => {
      const bType = block.type || "text";
      if (bType === "diagram" && block.mermaid) {
        hasDiagram = true;
        const diagDiv = document.createElement("div");
        diagDiv.className = "mermaid-diagram-pending";
        diagDiv.setAttribute("data-mermaid", block.mermaid);
        renderView.appendChild(diagDiv);
      } else {
        const textContent = block.content || block.text || "";
        const textDiv = document.createElement("div");
        textDiv.className = "text-block-content";
        textDiv.style.marginBottom = "14px";
        textDiv.style.lineHeight = "1.65";
        textDiv.innerHTML = highlightTerms(textContent, activeSearchHighlightTerm).replace(/\n/g, "<br>");
        renderView.appendChild(textDiv);
      }
    });

    renderMermaidDiagramsInContainer(renderView, activeSearchHighlightTerm);
    renderMath();
  }

  if (diagramBadge) {
    diagramBadge.style.display = hasDiagram ? "inline-flex" : "none";
  }

  // Populate OCR Debug Accordion (Model, Image Size, Status, Latency)
  const debugContent = document.getElementById("ocrDebugContent");
  if (debugContent) {
    const meta = page.metadata || {};
    const debug = meta.ocr_debug || {};
    const model = debug.model || "gemini-2.5-flash";
    const imgSize = debug.image_size || (page.image ? `${page.image.width || '600'}x${page.image.height || '800'}` : "N/A");
    const payloadKb = debug.payload_size_kb ? `${debug.payload_size_kb} KB` : "N/A";
    const latency = debug.latency_sec !== undefined ? `${debug.latency_sec}s` : "N/A";
    const status = page.ocr_status || "pending";
    const errReason = page.ocr_error || debug.error || "";
    const errSpan = errReason ? `<div style="color: #ef4444; margin-top: 4px;"><strong>Reason:</strong> ${escapeHtml(errReason)}</div>` : "";

    debugContent.innerHTML = `
      <div><strong>Model Used:</strong> <code>${escapeHtml(model)}</code></div>
      <div><strong>Image Resolution:</strong> ${escapeHtml(imgSize)} (${payloadKb})</div>
      <div><strong>Status:</strong> <span style="font-weight:600; color:${status === 'success' ? '#10b981' : (status === 'error' ? '#ef4444' : '#f59e0b')}">${escapeHtml(status.toUpperCase())}</span></div>
      ${errSpan}
      <div><strong>Response Time:</strong> ${escapeHtml(latency)}</div>
    `;
  }

  // Handle OCR Error state banner inside viewer
  if (page.ocr_status === "error" && renderView) {
    renderView.innerHTML = `
      <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 20px; text-align: center; margin: 20px 0;">
        <div style="font-size: 1.2rem; font-weight: 700; color: #ef4444; margin-bottom: 8px;">⚠️ OCR Processing Failed</div>
        <div style="color: var(--text-muted); font-size: 0.95rem; margin-bottom: 16px;">${escapeHtml(page.ocr_error || "API key missing or request failed.")}</div>
        <button class="btn btn-primary" onclick="reOcrCurrentPage()">🔄 Re-run OCR with Gemini</button>
      </div>
    `;
  }
  
  const prevBtn = document.getElementById("prevPageBtn");
  const nextBtn = document.getElementById("nextPageBtn");
  if (prevBtn) prevBtn.disabled = currentIdx <= 0;
  if (nextBtn) nextBtn.disabled = currentIdx >= filtered.length - 1;
}

function highlightTerms(text, query) {
  if (!text) return "";
  const escaped = escapeHtml(text);
  if (!query || !query.trim()) return escaped;
  
  // Highlight each word in the query individually
  const terms = query.trim().split(/\s+/).filter(t => t.length > 2);
  if (terms.length === 0) return escaped;
  
  const termPattern = terms.map(term => escapeHtml(term).replace(/[-[\]{}()*+?.,\\^$|#\s]/g, '\\$&')).join('|');
  const regex = new RegExp(`(${termPattern})`, "gi");
  return escaped.replace(regex, '<mark class="search-highlight">$1</mark>');
}

function navigatePage(delta) {
  const filtered = getFilteredPages();
  if (filtered.length === 0) return;
  const currentIdx = filtered.findIndex(p => p.page_number === currentPage);
  const targetIdx = (currentIdx >= 0 ? currentIdx : 0) + delta;
  if (targetIdx >= 0 && targetIdx < filtered.length) {
    renderViewerPage(filtered[targetIdx].page_number);
  }
}

async function saveCurrentTranscript() {
  const editor = document.getElementById("transcriptEditor");
  if (!editor) return;
  const newText = editor.value;
  
  const saveBtns = document.querySelectorAll("button[onclick='saveCurrentTranscript()']");
  saveBtns.forEach(btn => { btn.disabled = true; btn.textContent = "⏳ Saving..."; });

  try {
    const res = await fetch(`/api/pages/${currentPage}?notebook_id=${currentNotebook}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ ocr_text: newText })
    });
    if (res.ok) {
      const page = pagesData.find(p => p.page_number === currentPage);
      if (page) {
        page.ocr_text = newText;
        page.blocks = parseBlocks(newText);
        page.ocr_status = "success";
        page.ocr_error = null;
        page.is_code = newText.includes("```") || newText.includes("[CODE_DETECTED]");
      }
      setCachedPages(pagesData);
      updateHeaderStats();
      renderThumbnails();
      renderViewerPage(currentPage);

      saveBtns.forEach(btn => {
        btn.textContent = "✅ Saved!";
        btn.style.background = "var(--success)";
      });
      setTimeout(() => {
        saveBtns.forEach(btn => {
          btn.disabled = false;
          btn.textContent = "💾 Save Corrections";
          btn.style.background = "";
        });
      }, 2000);
    } else {
      alert("Failed to save transcript.");
      saveBtns.forEach(btn => { btn.disabled = false; btn.textContent = "💾 Save Corrections"; });
    }
  } catch (err) {
    alert("Failed to save transcript.");
    saveBtns.forEach(btn => { btn.disabled = false; btn.textContent = "💾 Save Corrections"; });
  }
}

async function reOcrCurrentPage() {
  const btn = document.getElementById("reOcrBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "⏳ Transcribing with Gemini...";
  }
  
  try {
    const res = await fetch(`/api/re-ocr/${currentPage}?notebook_id=${currentNotebook}`, {
      method: "POST",
      headers: getAuthHeaders()
    });
    const data = await res.json();
    const page = pagesData.find(p => p.page_number === currentPage);
    if (data.status === "success") {
      const editor = document.getElementById("transcriptEditor");
      if (editor) editor.value = data.ocr_text;
      if (page) {
        page.ocr_text = data.ocr_text;
        page.is_code = data.is_code;
        page.ocr_status = "success";
        page.ocr_error = null;
        page.metadata = page.metadata || {};
        if (data.ocr_debug) page.metadata.ocr_debug = data.ocr_debug;
      }
      updateHeaderStats();
      renderThumbnails();
      renderViewerPage(currentPage);
      alert("✅ Page re-transcribed with Gemini Vision!");
    } else {
      if (page) {
        page.ocr_status = "error";
        page.ocr_error = data.message || "OCR processing failed.";
        page.metadata = page.metadata || {};
        if (data.ocr_debug) page.metadata.ocr_debug = data.ocr_debug;
      }
      renderViewerPage(currentPage);
      alert("⚠️ OCR Failed: " + (data.message || "Unknown error"));
    }
  } catch (err) {
    alert("Failed to perform OCR: " + err.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "👁️ Re-run OCR";
    }
  }
}


// Helper for fast client-side photo compression before upload
async function compressAndResizeImage(file, maxDim = 1600, quality = 0.85) {
  if (!file.type || !file.type.startsWith("image/")) {
    return file; // Pass non-image files (PDFs) through unchanged
  }
  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      let { width, height } = img;
      if (width <= maxDim && height <= maxDim && file.size < 500 * 1024) {
        return resolve(file);
      }
      if (width > maxDim || height > maxDim) {
        if (width > height) {
          height = Math.round((height * maxDim) / width);
          width = maxDim;
        } else {
          width = Math.round((width * maxDim) / height);
          height = maxDim;
        }
      }
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(img, 0, 0, width, height);
      canvas.toBlob(
        (blob) => {
          if (!blob) return resolve(file);
          const resizedFile = new File([blob], file.name.replace(/\.[^.]+$/, ".jpg"), {
            type: "image/jpeg",
            lastModified: Date.now()
          });
          resolve(resizedFile);
        },
        "image/jpeg",
        quality
      );
    };
    img.onerror = () => resolve(file);
    img.src = url;
  });
}

// ==================== Upload Workflow ====================
async function handleFileUpload(files) {
  if (!files || files.length === 0) return;
  
  const progressText = document.getElementById("uploadStatusText");
  if (progressText) {
    progressText.textContent = "⚡ Optimizing photo(s) & processing...";
    progressText.style.display = "block";
  }

  const uploadFolderSelect = document.getElementById("uploadFolderSelect");
  const targetFolder = (uploadFolderSelect ? uploadFolderSelect.value : "General") || "General";

  let totalUploaded = 0;
  let allErrors = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    if (progressText) {
      progressText.textContent = `⚡ Processing file ${i + 1} of ${files.length} (${file.name})...`;
    }

    const optimizedFile = await compressAndResizeImage(file);
    const formData = new FormData();
    formData.append("files", optimizedFile, optimizedFile.name);
    formData.append("folder", targetFolder);

    try {
      const res = await fetch(`/api/upload?notebook_id=${currentNotebook}`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: formData
      });
      const data = await res.json();
      if (res.ok && (data.status === "success" || data.uploaded_pages > 0)) {
        totalUploaded += (data.uploaded_pages || 1);
      } else {
        const err = data.detail || (data.errors ? data.errors.join(", ") : "Upload error");
        allErrors.push(`${file.name}: ${err}`);
      }
    } catch (err) {
      allErrors.push(`${file.name}: Network error`);
    }
  }

  await fetchNotebooks();
  await loadPages();

  if (progressText) {
    if (allErrors.length === 0) {
      progressText.textContent = `✅ Successfully processed ${totalUploaded} page(s).`;
    } else if (totalUploaded > 0) {
      progressText.textContent = `⚠️ Processed ${totalUploaded} page(s). Warnings: ${allErrors.join("; ")}`;
    } else {
      progressText.textContent = `❌ Upload failed: ${allErrors.join("; ")}`;
    }
  }
}

async function clearCurrentNotebook() {
  const folderSavedPages = pagesData.filter(p => p.folder && p.folder.trim() !== "");
  const unassignedPages = pagesData.filter(p => !p.folder || p.folder.trim() === "");

  if (pagesData.length === 0) {
    alert("Notebook is already empty.");
    return;
  }

  // If notes are organized in folders, protect them!
  if (folderSavedPages.length > 0) {
    if (unassignedPages.length === 0) {
      alert(`🔒 All current notes are saved in folders (${[...new Set(folderSavedPages.map(p => p.folder))].join(", ")}).\n\nNotes saved in folders are protected from being cleared here. To delete notes, open their specific folder.`);
      return;
    }

    if (!confirm(`Clear ${unassignedPages.length} unassigned scan(s) from the Digitized section?\n\nNote: Your ${folderSavedPages.length} note(s) saved in folders will be preserved safely.`)) {
      return;
    }

    try {
      const res = await fetch(`/api/pages/delete-bulk`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          page_numbers: unassignedPages.map(p => p.page_number),
          notebook_id: currentNotebook
        })
      });
      if (res.ok) {
        const delSet = new Set(unassignedPages.map(p => p.page_number));
        pagesData = pagesData.filter(p => !delSet.has(p.page_number));
        setCachedPages(pagesData);
        selectedPages.clear();
        await fetchNotebooks();
        await loadPages();
        alert(`✅ Cleared ${unassignedPages.length} unassigned scan(s). Your ${folderSavedPages.length} note(s) saved in folders remain safe.`);
      }
    } catch (e) {
      alert("Failed to clear scans.");
    }
    return;
  }

  // If NO pages are in folders, standard full clear
  if (!confirm("Are you sure you want to clear all unassigned pages in this notebook?")) return;
  try {
    const res = await fetch(`/api/clear?notebook_id=${currentNotebook}`, {
      method: "POST",
      headers: getAuthHeaders()
    });
    if (res.ok) {
      selectedPages.clear();
      pagesData = [];
      try { localStorage.removeItem(`nis_cached_pages_${currentNotebook}`); } catch (e) {}
      await fetchNotebooks();
      await loadPages();
    }
  } catch (e) {
    alert("Failed to clear notebook.");
  }
}

// ==================== Search View ====================
async function executeSearch() {
  const queryInput = document.getElementById("searchInput");
  const query = queryInput.value.trim();
  const resultsContainer = document.getElementById("searchResults");
  
  if (!query) {
    resultsContainer.innerHTML = `<div style="color: var(--text-muted);">Please enter a keyword or phrase to search.</div>`;
    return;
  }
  
  resultsContainer.innerHTML = `<div style="color: var(--text-muted);">Searching across pages...</div>`;
  
  try {
    let results = [];
    let totalMatches = 0;

    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ query, notebook_id: currentNotebook })
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.results && data.results.length > 0) {
          results = data.results;
          totalMatches = data.total_matches || results.length;
        }
      }
    } catch (netErr) {
      console.warn("Server search encountered error, falling back to local search:", netErr);
    }

    // Client-side search fallback across pagesData if server had no results
    if (results.length === 0 && pagesData.length > 0) {
      const lowerQ = query.toLowerCase();
      pagesData.forEach(p => {
        const text = p.ocr_text || "";
        const fn = (p.source_filename || "").toLowerCase();
        if (text.toLowerCase().includes(lowerQ) || fn.includes(lowerQ)) {
          const lines = text.split("\n");
          const matchingSnippets = [];
          lines.forEach(line => {
            if (line.toLowerCase().includes(lowerQ)) {
              matchingSnippets.push(line.trim());
            }
          });
          if (matchingSnippets.length === 0) {
            matchingSnippets.push(text.slice(0, 150) + "...");
          }
          totalMatches += matchingSnippets.length;
          results.push({
            page_number: p.page_number,
            source_filename: p.source_filename || `Page ${p.page_number}`,
            match_count: matchingSnippets.length,
            snippets: matchingSnippets.slice(0, 4)
          });
        }
      });
    }
    
    if (results.length === 0) {
      resultsContainer.innerHTML = `<div style="color: var(--text-muted); padding: 20px;">No matches found for "<strong>${escapeHtml(query)}</strong>".</div>`;
      return;
    }
    
    resultsContainer.innerHTML = `<div style="font-weight: 600; margin-bottom: 12px; color: #60a5fa;">Found ${totalMatches} match(es) across ${results.length} page(s):</div>`;
    
    results.forEach(r => {
      const card = document.createElement("div");
      card.className = "search-result-card";
      card.onclick = () => jumpToPage(r.page_number, query);
      
      let snippetsHtml = (r.snippets || []).map(s => `<div class="search-snippet">${escapeHtml(s)}</div>`).join("");
      
      card.innerHTML = `
        <div class="search-result-header">
          <span>📄 Page ${r.page_number} (${escapeHtml(r.source_filename)})</span>
          <span class="badge badge-primary">${r.match_count || 1} match(es)</span>
        </div>
        ${snippetsHtml}
      `;
      resultsContainer.appendChild(card);
    });
    
    renderMath();
  } catch (err) {
    resultsContainer.innerHTML = `<div style="color: var(--error);">Search request failed: ${escapeHtml(err.message)}</div>`;
  }
}

// ==================== Grounded Q&A Assistant ====================
async function askQuestion() {
  const input = document.getElementById("chatInput");
  const query = input.value.trim();
  if (!query) return;
  
  const container = document.getElementById("chatMessages");
  
  // Append user message
  const userMsg = document.createElement("div");
  userMsg.className = "message message-user";
  userMsg.textContent = query;
  container.appendChild(userMsg);
  
  input.value = "";
  container.scrollTop = container.scrollHeight;
  
  // Append AI loading placeholder
  const aiMsg = document.createElement("div");
  aiMsg.className = "message message-ai";
  aiMsg.textContent = "Thinking and retrieving page evidence...";
  container.appendChild(aiMsg);
  container.scrollTop = container.scrollHeight;
  
  try {
    const res = await fetch("/api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, notebook_id: currentNotebook })
    });
    const data = await res.json();
    
    let citationsHtml = "";
    const sourcePages = data.citations || data.source_pages || [];
    if (sourcePages && sourcePages.length > 0) {
      citationsHtml = `<div style="margin-top: 10px; border-top: 1px solid #334155; padding-top: 8px;">
        <span style="font-size: 0.75rem; color: var(--text-muted);">Sources:</span> ` +
        sourcePages.map(p => `<span class="citation-badge" onclick="jumpToPage(${p})">Page ${p}</span>`).join("") +
        `</div>`;
    }
    
    const expText = data.explanation || "No explanation returned.";
    aiMsg.innerHTML = `<div style="line-height: 1.6;">${expText.replace(/\n/g, "<br>")}</div>` + citationsHtml;
    renderMath();
  } catch (err) {
    aiMsg.textContent = "Error communicating with AI assistant.";
  }
  container.scrollTop = container.scrollHeight;
}

// ==================== Active Recall Quiz Portal ====================
function populateQuizSourceFilters() {
  const folderSelect = document.getElementById("quizFolderSelect");
  const docSelect = document.getElementById("quizDocSelect");
  if (!docSelect) return;

  const currentFolderVal = folderSelect ? folderSelect.value || "all" : "all";

  if (folderSelect) {
    folderSelect.innerHTML = `<option value="all">📁 All Folders (${pagesData.length} Notes)</option>`;
    availableFolders.forEach(f => {
      const count = pagesData.filter(p => (p.folder || "") === f).length;
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = `📁 ${f} (${count})`;
      if (f === currentFolderVal) opt.selected = true;
      folderSelect.appendChild(opt);
    });

    const unassignedCount = pagesData.filter(p => !p.folder).length;
    if (unassignedCount > 0) {
      const opt = document.createElement("option");
      opt.value = "__unassigned__";
      opt.textContent = `📄 Unassigned (${unassignedCount})`;
      if (currentFolderVal === "__unassigned__") opt.selected = true;
      folderSelect.appendChild(opt);
    }

    if ([...folderSelect.options].some(o => o.value === currentFolderVal)) {
      folderSelect.value = currentFolderVal;
    } else {
      folderSelect.value = "all";
    }
  }

  updateQuizDocOptions(folderSelect ? folderSelect.value : "all");
}

function onQuizFolderChange(folderVal) {
  updateQuizDocOptions(folderVal);
}

function updateQuizDocOptions(folderVal) {
  const docSelect = document.getElementById("quizDocSelect");
  if (!docSelect) return;

  const currentVal = docSelect.value || "all";
  let targetPages = pagesData;
  if (folderVal === "__unassigned__") {
    targetPages = pagesData.filter(p => !p.folder);
  } else if (folderVal && folderVal !== "all") {
    targetPages = pagesData.filter(p => (p.folder || "") === folderVal);
  }

  docSelect.innerHTML = `<option value="all">All Notes in Scope (${targetPages.length})</option>`;

  const files = new Set(targetPages.map(p => p.source_filename).filter(Boolean));
  files.forEach(fname => {
    const pageCount = targetPages.filter(p => p.source_filename === fname).length;
    const opt = document.createElement("option");
    opt.value = fname;
    opt.textContent = `📄 ${fname} (${pageCount} p.)`;
    docSelect.appendChild(opt);
  });

  if ([...docSelect.options].some(o => o.value === currentVal)) {
    docSelect.value = currentVal;
  } else {
    docSelect.value = "all";
  }
}

async function startQuiz() {
  const folderSelect = document.getElementById("quizFolderSelect");
  const docSelect = document.getElementById("quizDocSelect");
  const startInput = document.getElementById("quizStartPage");
  const endInput = document.getElementById("quizEndPage");
  const diffSelect = document.getElementById("quizDifficultySelect");
  const countSelect = document.getElementById("quizCountSelect");

  const folderFilter = folderSelect ? folderSelect.value : "all";
  const docFilter = docSelect ? docSelect.value : "all";
  const startPage = parseInt(startInput?.value);
  const endPage = parseInt(endInput?.value);
  const difficulty = diffSelect?.value || "Intermediate";
  const numQuestions = parseInt(countSelect?.value) || 3;

  let pageRange = null;
  if (!isNaN(startPage) && !isNaN(endPage) && startPage > 0 && endPage >= startPage) {
    pageRange = [startPage, endPage];
  }

  const container = document.getElementById("quizContainer");
  const submitBtn = document.getElementById("submitQuizBtn");
  const scoreDisplay = document.getElementById("quizScoreDisplay");

  if (scoreDisplay) scoreDisplay.style.display = "none";
  const scopeFolderText = folderFilter === 'all' ? 'all folders' : (folderFilter === '__unassigned__' ? 'unassigned notes' : `folder "${folderFilter}"`);
  container.innerHTML = `<div style="color: var(--text-muted); padding: 30px; text-align: center;">
    <div style="font-size: 1.5rem; margin-bottom: 8px;">⏳</div>
    <div style="font-weight: 600;">Generating grounded quiz from notes...</div>
    <div style="font-size: 0.8rem; margin-top: 4px;">Targeting ${escapeHtml(scopeFolderText)} &bull; ${docFilter === 'all' ? 'all notes' : escapeHtml(docFilter)} (${difficulty} difficulty)</div>
  </div>`;
  if (submitBtn) submitBtn.style.display = "none";

  try {
    let generated = [];
    try {
      const res = await fetch("/api/quiz", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({
          notebook_id: currentNotebook,
          num_questions: numQuestions,
          document_filter: docFilter,
          folder: (folderFilter !== "all" ? folderFilter : null),
          page_range: pageRange,
          difficulty: difficulty
        })
      });
      if (res.ok) {
        generated = await res.json();
      }
    } catch (e) {
      console.warn("Server quiz API error, checking local notes fallback:", e);
    }

    // Client fallback if server returned empty or failed
    if ((!generated || generated.length === 0) && pagesData.length > 0) {
      let filteredPages = pagesData;
      if (folderFilter === "__unassigned__") {
        filteredPages = filteredPages.filter(p => !p.folder);
      } else if (folderFilter && folderFilter !== "all") {
        filteredPages = filteredPages.filter(p => (p.folder || "") === folderFilter);
      }
      if (docFilter && docFilter !== "all") {
        filteredPages = filteredPages.filter(p => p.source_filename === docFilter);
      }
      if (pageRange) {
        filteredPages = filteredPages.filter(p => p.page_number >= pageRange[0] && p.page_number <= pageRange[1]);
      }
      if (filteredPages.length === 0) filteredPages = pagesData;

      generated = [];
      filteredPages.slice(0, numQuestions).forEach((p, idx) => {
        const textSample = (p.ocr_text || "").split("\n").filter(l => l.trim().length > 10)[0] || `Notes from Page ${p.page_number}`;
        generated.push({
          question: `Based on Page ${p.page_number} (${escapeHtml(p.source_filename || 'Notes')}), what is a key concept discussed?`,
          options: [
            textSample.slice(0, 70),
            "Unrelated concept from another chapter",
            "Alternative unrelated definition",
            "None of the above"
          ],
          correct_answer: textSample.slice(0, 70),
          explanation: `Directly derived from the notes on Page ${p.page_number}.`,
          page_number: p.page_number
        });
      });
    }

    quizData = generated;

    if (!quizData || quizData.length === 0) {
      container.innerHTML = `<div style="color: var(--text-muted); padding: 30px; text-align: center;">No questions could be generated. Make sure your notebook has uploaded notes.</div>`;
      return;
    }

    container.innerHTML = "";
    quizData.forEach((q, idx) => {
      const qCard = document.createElement("div");
      qCard.className = "quiz-question-card";
      qCard.style.cssText = "background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; margin-bottom: 12px;";

      let optionsHtml = "";
      if (q.options && q.options.length > 0) {
        optionsHtml = `<div class="quiz-options" style="display: flex; flex-direction: column; gap: 8px; margin-top: 10px;">` +
          q.options.map((opt) => `
            <label class="quiz-option-label" style="display: flex; align-items: center; gap: 10px; padding: 10px 14px; background: var(--input-bg); border: 1px solid var(--border); border-radius: 6px; cursor: pointer;">
              <input type="radio" name="q_${idx}" value="${escapeHtml(opt)}">
              <span style="font-size: 0.9rem; color: var(--text-main);">${escapeHtml(opt)}</span>
            </label>
          `).join("") +
          `</div>`;
      }

      const questionText = escapeHtml(q.question || q.prompt || q.question_text || q.q || `Question ${idx + 1}`);
      const pageBadge = q.page_number ? `<span class="badge badge-primary">Page ${q.page_number}</span>` : "";

      qCard.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; margin-bottom: 6px;">
          <div style="font-weight: 700; color: var(--text-main); font-size: 1rem;">Q${idx + 1}: ${questionText}</div>
          ${pageBadge}
        </div>
        ${optionsHtml}
        <div id="rationale_${idx}" style="display: none; margin-top: 12px; padding: 12px; background: #0f172a; border-radius: 6px; font-size: 0.85rem; border-left: 3px solid #60a5fa;"></div>
      `;
      container.appendChild(qCard);
    });

    if (submitBtn) submitBtn.style.display = "inline-flex";
    renderMath();
  } catch (err) {
    container.innerHTML = `<div style="color: var(--error); padding: 20px;">Failed to generate quiz: ${escapeHtml(err.message)}</div>`;
  }
}

function gradeQuiz() {
  if (!quizData || quizData.length === 0) return;
  let score = 0;
  quizData.forEach((q, idx) => {
    const selected = document.querySelector(`input[name="q_${idx}"]:checked`);
    const rationaleDiv = document.getElementById(`rationale_${idx}`);
    if (!rationaleDiv) return;
    rationaleDiv.style.display = "block";

    let expected = q.correct_answer || q.answer || q.correct || "";
    if (typeof expected === "number" && q.options && q.options[expected]) {
      expected = q.options[expected];
    } else if (q.correct_option_idx !== undefined && q.options && q.options[q.correct_option_idx]) {
      expected = q.options[q.correct_option_idx];
    }

    const selVal = selected ? selected.value.trim().toLowerCase() : "";
    const expVal = String(expected).trim().toLowerCase();
    const isCorrect = selected && (selVal === expVal || (expVal.length === 1 && selVal.startsWith(expVal)));
    if (isCorrect) score++;

    rationaleDiv.innerHTML = `
      <div style="font-weight: 700; color: ${isCorrect ? '#34d399' : '#f87171'}; font-size: 0.9rem; margin-bottom: 4px;">
        ${isCorrect ? '✅ Correct Answer!' : '❌ Incorrect'} (Correct: ${escapeHtml(String(expected || 'See notes'))})
      </div>
      <div style="color: #cbd5e1; font-size: 0.85rem; line-height: 1.5;">${escapeHtml(q.explanation || 'Refer to your lecture notes for detailed context.')}</div>
    `;
  });

  const pct = Math.round((score / quizData.length) * 100);
  const scoreDisplay = document.getElementById("quizScoreDisplay");
  if (scoreDisplay) {
    scoreDisplay.style.display = "block";
    scoreDisplay.innerHTML = `<span style="color: ${pct >= 60 ? '#34d399' : '#f87171'};">🎯 Quiz Complete! Score: ${score} / ${quizData.length} (${pct}%)</span>`;
  }

  renderMath();
}

// ==================== Code Lab & Sandbox ====================
let userClearedCode = false;

function clearCodeEditor() {
  const codeEditor = document.getElementById("codeEditor");
  if (codeEditor) {
    codeEditor.value = "";
    codeEditor.focus();
  }
  userClearedCode = true;
  const terminal = document.getElementById("terminalOutput");
  if (terminal) {
    terminal.textContent = "Editor cleared. Enter or paste Python code and click \"▶️ Run Code in Sandbox\".";
  }
}

function switchView(targetId) {
  const tabs = document.querySelectorAll(".tab-btn");
  tabs.forEach(t => {
    t.classList.remove("active");
    if (t.dataset.target === targetId) t.classList.add("active");
  });
  document.querySelectorAll(".view-panel").forEach(p => p.classList.remove("active"));
  const targetPanel = document.getElementById(targetId);
  if (targetPanel) targetPanel.classList.add("active");
  updateViewTitle(targetId);
  if (targetId === "view-code") syncCodeEditor();
  renderMath();
}

function sendCurrentPageToCodeLab() {
  userClearedCode = false;
  const page = pagesData.find(p => p.page_number === currentPage) || pagesData[0];
  if (!page) {
    alert("Please select a page first.");
    return;
  }
  let codeText = "";
  if (page.ocr_text) {
    const match = page.ocr_text.match(/```python([\s\S]*?)```/) || page.ocr_text.match(/```([\s\S]*?)```/);
    if (match) {
      codeText = match[1].trim();
    } else {
      codeText = page.ocr_text;
    }
  }
  const codeEditor = document.getElementById("codeEditor");
  if (codeEditor) {
    codeEditor.value = codeText;
  }
  switchView("view-code");
}

function syncCodeEditor() {
  const codeEditor = document.getElementById("codeEditor");
  if (!codeEditor) return;
  if (userClearedCode) return;
  
  if (!codeEditor.value || codeEditor.value.trim() === "") {
    const codePage = pagesData.find(p => p.is_code);
    if (codePage) {
      const match = codePage.ocr_text.match(/```python([\s\S]*?)```/);
      if (match) {
        codeEditor.value = match[1].trim();
      } else {
        codeEditor.value = codePage.ocr_text;
      }
    }
  }
}

async function runCodeInSandbox() {
  const codeEditor = document.getElementById("codeEditor");
  const terminal = document.getElementById("terminalOutput");
  const code = codeEditor.value;
  
  terminal.textContent = "Running in secure isolated sandbox...";
  
  try {
    const res = await fetch("/run-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, timeout_seconds: 5 })
    });
    const data = await res.json();
    
    let output = "";
    if (data.stdout) output += data.stdout;
    if (data.stderr) output += `\n[STDERR]\n${data.stderr}`;
    if (data.error_message) output += `\n[SANDBOX ERROR] ${data.error_message}`;
    if (data.timed_out) output += `\n[SECURITY] Execution aborted: exceeded 5-second timeout limit.`;
    
    output += `\n\n--- Process finished with exit code ${data.exit_code} (${data.duration_ms.toFixed(1)} ms) ---`;
    terminal.textContent = output;
  } catch (err) {
    terminal.textContent = `Execution failed: ${err.message}`;
  }
}

// ==================== Export Handlers ====================
async function exportNotebookMarkdown() {
  try {
    const res = await fetch(`/api/notebooks/${currentNotebook}/export`);
    const data = await res.json();
    
    const blob = new Blob([data.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${currentNotebook}_notes.md`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("Export failed.");
  }
}

async function exportQuizAnki() {
  try {
    const res = await fetch(`/api/notebooks/${currentNotebook}/export-quiz`);
    const data = await res.json();
    
    const blob = new Blob([data.csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${currentNotebook}_flashcards.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("Quiz export failed.");
  }
}

