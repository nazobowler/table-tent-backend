# Markup/CSS/JS for the /admin dashboard, kept as one plain string so
# main.py can serve it with zero extra dependencies (no Jinja, no static
# file mount, no build step). It's a thin client over the existing admin
# API - same X-Admin-Key you'd pass to curl, just typed in once and kept in
# the browser's localStorage instead of retyped into every command.

ADMIN_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Table Tent Fleet</title>
<style>
  :root {
    --bg: #0f1115;
    --panel: #171a21;
    --panel-border: #262b36;
    --text: #e7e9ee;
    --text-dim: #9aa1b2;
    --accent: #4f8cff;
    --green: #2fb170;
    --amber: #d9a441;
    --red: #e5484d;
    --gray: #6b7280;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
  }
  h1 { font-size: 20px; margin: 0; }
  h2 { font-size: 15px; margin: 0 0 12px 0; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.04em; }
  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
    flex-wrap: wrap;
    gap: 8px;
  }
  .topbar .right { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--text-dim); }
  button, .btn {
    font-family: inherit;
    font-size: 13px;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    color: var(--text);
    padding: 6px 12px;
    border-radius: 6px;
    cursor: pointer;
  }
  button:hover { border-color: var(--accent); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.danger:hover { border-color: var(--red); }
  button:disabled { opacity: 0.5; cursor: default; }
  input, select {
    font-family: inherit;
    font-size: 13px;
    background: #0f1115;
    border: 1px solid var(--panel-border);
    color: var(--text);
    padding: 6px 10px;
    border-radius: 6px;
  }
  .panel {
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 10px;
    padding: 18px;
    margin-bottom: 20px;
  }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--text-dim); font-weight: 500; padding: 6px 10px; border-bottom: 1px solid var(--panel-border); white-space: nowrap; }
  td { padding: 8px 10px; border-bottom: 1px solid var(--panel-border); vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; }
  .badge.active { background: rgba(47,177,112,0.15); color: var(--green); }
  .badge.grace { background: rgba(217,164,65,0.15); color: var(--amber); }
  .badge.failed { background: rgba(229,72,77,0.15); color: var(--red); }
  .badge.suspended { background: rgba(107,114,128,0.2); color: var(--gray); }
  .badge.canceled { background: rgba(107,114,128,0.2); color: var(--gray); }
  .dim { color: var(--text-dim); }
  .row-actions { display: flex; gap: 6px; }
  .empty { color: var(--text-dim); font-size: 13px; padding: 6px 10px; }
  .banner { padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px; }
  .banner.error { background: rgba(229,72,77,0.12); color: #ff8b8e; border: 1px solid rgba(229,72,77,0.35); }
  .banner.success { background: rgba(47,177,112,0.12); color: #6fe3a4; border: 1px solid rgba(47,177,112,0.35); }
  .secret-box { font-family: monospace; font-size: 13px; background: #0f1115; border: 1px solid var(--panel-border); border-radius: 6px; padding: 8px 10px; margin-top: 6px; word-break: break-all; }
  .login-wrap { display: flex; align-items: center; justify-content: center; min-height: 60vh; }
  .login-card { background: var(--panel); border: 1px solid var(--panel-border); border-radius: 10px; padding: 28px; width: 320px; text-align: center; }
  .login-card p { color: var(--text-dim); font-size: 13px; margin: 6px 0 16px; }
  .login-card input { width: 100%; margin-bottom: 10px; }
  .login-card button { width: 100%; }
  .add-customer-row { display: flex; gap: 8px; margin-top: 12px; }
  .add-customer-row input { flex: 1; }
  .muted-note { font-size: 12px; color: var(--text-dim); margin-top: 8px; }
  #lastUpdated { font-size: 12px; }
</style>
</head>
<body>

<div id="loginScreen" class="login-wrap" style="display:none;">
  <div class="login-card">
    <h1>Table Tent Fleet</h1>
    <p>Enter the admin key to continue.</p>
    <input id="keyInput" type="password" placeholder="Admin key" autocomplete="off">
    <button class="primary" id="keySubmit">Connect</button>
    <div id="loginError" class="banner error" style="display:none; margin-top:12px;"></div>
  </div>
</div>

<div id="mainScreen" style="display:none;">
  <div class="topbar">
    <h1>Table Tent Fleet</h1>
    <div class="right">
      <span id="lastUpdated"></span>
      <button id="refreshBtn">Refresh</button>
      <button id="forgetKeyBtn">Change key</button>
    </div>
  </div>

  <div id="bannerArea"></div>

  <div class="panel">
    <h2>Devices</h2>
    <div id="devicesTableWrap"><div class="empty">Loading…</div></div>
  </div>

  <div class="panel">
    <h2>Pending pairing codes</h2>
    <div id="pendingTableWrap"><div class="empty">Loading…</div></div>
  </div>

  <div class="panel">
    <h2>Customers</h2>
    <div id="customersTableWrap"><div class="empty">Loading…</div></div>
    <div class="add-customer-row">
      <input id="newCustomerName" type="text" placeholder="New customer / business name">
      <button class="primary" id="addCustomerBtn">Add customer</button>
    </div>
  </div>
</div>

<script>
(function () {
  var STORAGE_KEY = "tt_admin_key";
  var state = { devices: [], customers: [], pending: [], expandedLogs: {}, logCache: {}, confirmDelete: {} };
  var refreshTimer = null;

  function getKey() {
    try { return localStorage.getItem(STORAGE_KEY) || ""; }
    catch (e) { return ""; }
  }
  function setKey(k) {
    try { localStorage.setItem(STORAGE_KEY, k); } catch (e) {}
  }
  function clearKey() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
  }

  function fmtRelative(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "—";
    var diffMs = Date.now() - d.getTime();
    var mins = Math.round(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    var hours = Math.round(mins / 60);
    if (hours < 24) return hours + "h ago";
    var days = Math.round(hours / 24);
    return days + "d ago";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showBanner(kind, msg, opts) {
    opts = opts || {};
    var area = document.getElementById("bannerArea");
    var div = document.createElement("div");
    div.className = "banner " + kind;
    div.innerHTML = msg;
    area.innerHTML = "";
    area.appendChild(div);
    if (!opts.sticky) {
      setTimeout(function () {
        if (div.parentNode) div.parentNode.removeChild(div);
      }, 6000);
    }
    return div;
  }

  async function apiFetch(path, opts) {
    opts = opts || {};
    var headers = Object.assign({ "X-Admin-Key": getKey() }, opts.headers || {});
    if (opts.body) headers["Content-Type"] = "application/json";
    var res = await fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 401) {
      clearKey();
      showLogin("That key was rejected. Enter the current admin key.");
      throw new Error("unauthorized");
    }
    if (!res.ok) {
      var detail = "";
      try { detail = (await res.json()).detail || ""; } catch (e) {}
      throw new Error(detail || ("HTTP " + res.status));
    }
    if (res.status === 204) return null;
    return res.json();
  }

  function showLogin(err) {
    document.getElementById("mainScreen").style.display = "none";
    document.getElementById("loginScreen").style.display = "flex";
    var errBox = document.getElementById("loginError");
    if (err) { errBox.style.display = "block"; errBox.textContent = err; }
    else { errBox.style.display = "none"; }
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  }

  function showMain() {
    document.getElementById("loginScreen").style.display = "none";
    document.getElementById("mainScreen").style.display = "block";
  }

  function statusBadge(device) {
    if (device.manually_suspended || device.device_locally_suspended) {
      var label = "Suspended";
      if (device.manually_suspended && device.device_locally_suspended) label = "Suspended (admin + device)";
      else if (device.device_locally_suspended) label = "Suspended (device)";
      else label = "Suspended (admin)";
      return '<span class="badge suspended">' + label + '</span>';
    }
    if (device.effective_status === "active") return '<span class="badge active">Active</span>';
    if (device.effective_status === "grace") return '<span class="badge grace">Grace</span>';
    if (device.effective_status === "failed") {
      if (device.failure_code === 504) return '<span class="badge failed">Offline</span>';
      if (device.failure_code === 402) return '<span class="badge failed">Payment failed</span>';
      return '<span class="badge failed">Failed</span>';
    }
    return '<span class="badge">' + escapeHtml(device.effective_status) + '</span>';
  }

  function fetchDeviceLog(deviceId) {
    return apiFetch("/api/v1/admin/devices/" + encodeURIComponent(deviceId) + "/log").then(function (result) {
      state.logCache[deviceId] = result;
      return result;
    });
  }

  function logPanelHtml(d) {
    var cached = state.logCache[d.device_id];
    var body;
    if (!cached) {
      body = '<div class="empty">Loading…</div>';
    } else if (!cached.log) {
      body = '<div class="empty">No log received yet - this device hasn\\'t checked in since the log feature was added.</div>';
    } else {
      body = '<pre style="background:#0f1115; border:1px solid var(--panel-border); border-radius:6px; padding:10px; max-height:280px; overflow:auto; font-size:12px; white-space:pre-wrap; word-break:break-all; margin:0;">' +
        escapeHtml(cached.log) + '</pre>' +
        '<div class="muted-note">As of last check-in (' + fmtRelative(cached.as_of) + ') - not live, updates on the device\\'s next hourly check-in.</div>';
    }
    return '<tr class="log-row" data-log-row="' + escapeHtml(d.device_id) + '">' +
      '<td colspan="8" style="background:#0d0f13;">' +
      '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">' +
      '<strong style="font-size:12px; color:var(--text-dim);">Log — ' + escapeHtml(d.name) + '</strong>' +
      '<div class="row-actions">' +
      '<button data-action="refresh-log" data-device="' + escapeHtml(d.device_id) + '">Refresh</button>' +
      '<button data-action="close-log" data-device="' + escapeHtml(d.device_id) + '">Close</button>' +
      '</div></div>' +
      body +
      '</td></tr>';
  }

  function renderDevices() {
    var wrap = document.getElementById("devicesTableWrap");
    if (!state.devices.length) {
      wrap.innerHTML = '<div class="empty">No devices yet. Pair one from the table tent\\'s Settings menu.</div>';
      return;
    }
    var rows = state.devices.slice().sort(function (a, b) {
      if (a.customer_name !== b.customer_name) return a.customer_name.localeCompare(b.customer_name);
      return a.name.localeCompare(b.name);
    });
    var html = '<table><thead><tr>' +
      '<th>Device</th><th>Customer</th><th>Status</th><th>Last check-in</th>' +
      '<th>Firmware</th><th>WiFi</th><th>Grace expires</th><th></th>' +
      '</tr></thead><tbody>';
    rows.forEach(function (d) {
      html += '<tr>' +
        '<td>' + escapeHtml(d.name) + '<div class="dim">' + escapeHtml(d.device_id) + '</div></td>' +
        '<td>' + escapeHtml(d.customer_name) + '</td>' +
        '<td>' + statusBadge(d) +
          (d.device_locally_suspended && !d.manually_suspended
            ? '<div class="muted-note">Reactivate here won\\'t clear this - it self-clears when the device reports its toggle is off.</div>'
            : '') +
        '</td>' +
        '<td>' + fmtRelative(d.last_checkin_at) + '</td>' +
        '<td class="dim">' + escapeHtml(d.firmware_version || "—") + '</td>' +
        '<td class="dim">' + (d.wifi_rssi_dbm != null ? d.wifi_rssi_dbm + " dBm" : "—") + '</td>' +
        '<td class="dim">' + (d.grace_expires_at ? fmtRelative(d.grace_expires_at) : "—") + '</td>' +
        '<td><div class="row-actions">' +
          (state.confirmDelete[d.device_id]
            ? '<span class="dim" style="font-size:12px;">Delete forever?</span>' +
              '<button class="danger" data-action="confirm-delete" data-device="' + escapeHtml(d.device_id) + '">Yes, delete</button>' +
              '<button data-action="cancel-delete" data-device="' + escapeHtml(d.device_id) + '">Cancel</button>'
            : '<button data-action="' + (d.manually_suspended ? "reactivate" : "suspend") + '" data-device="' + escapeHtml(d.device_id) + '">' +
                (d.manually_suspended ? "Reactivate" : "Suspend") +
              '</button>' +
              '<button data-action="reset-grace" data-device="' + escapeHtml(d.device_id) + '">Reset grace</button>' +
              '<button data-action="toggle-log" data-device="' + escapeHtml(d.device_id) + '">' +
                (state.expandedLogs[d.device_id] ? "Hide log" : "Log") +
              '</button>' +
              '<button class="danger" data-action="delete" data-device="' + escapeHtml(d.device_id) + '">Delete</button>'
          ) +
        '</div></td>' +
      '</tr>';
      if (state.expandedLogs[d.device_id]) {
        html += logPanelHtml(d);
      }
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;

    wrap.querySelectorAll('button[data-action="suspend"], button[data-action="reactivate"], button[data-action="reset-grace"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var action = btn.getAttribute("data-action");
        var deviceId = btn.getAttribute("data-device");
        var path = "/api/v1/admin/devices/" + encodeURIComponent(deviceId) + "/" +
          (action === "suspend" ? "force-suspend" : action === "reactivate" ? "force-reactivate" : "reset-grace");
        btn.disabled = true;
        apiFetch(path, { method: "POST" })
          .then(function () { return loadAll(); })
          .catch(function (e) { showBanner("error", "Action failed: " + escapeHtml(e.message)); })
          .then(function () { btn.disabled = false; });
      });
    });

    wrap.querySelectorAll('button[data-action="toggle-log"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var deviceId = btn.getAttribute("data-device");
        if (state.expandedLogs[deviceId]) {
          delete state.expandedLogs[deviceId];
          renderDevices();
          return;
        }
        state.expandedLogs[deviceId] = true;
        renderDevices();
        fetchDeviceLog(deviceId).then(renderDevices).catch(function (e) {
          showBanner("error", "Couldn't load log: " + escapeHtml(e.message));
        });
      });
    });

    wrap.querySelectorAll('button[data-action="refresh-log"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var deviceId = btn.getAttribute("data-device");
        btn.disabled = true;
        fetchDeviceLog(deviceId).then(renderDevices).catch(function (e) {
          showBanner("error", "Couldn't refresh log: " + escapeHtml(e.message));
        }).then(function () { btn.disabled = false; });
      });
    });

    wrap.querySelectorAll('button[data-action="close-log"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var deviceId = btn.getAttribute("data-device");
        delete state.expandedLogs[deviceId];
        renderDevices();
      });
    });

    wrap.querySelectorAll('button[data-action="delete"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var deviceId = btn.getAttribute("data-device");
        state.confirmDelete[deviceId] = true;
        renderDevices();
      });
    });

    wrap.querySelectorAll('button[data-action="cancel-delete"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var deviceId = btn.getAttribute("data-device");
        delete state.confirmDelete[deviceId];
        renderDevices();
      });
    });

    wrap.querySelectorAll('button[data-action="confirm-delete"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var deviceId = btn.getAttribute("data-device");
        btn.disabled = true;
        apiFetch("/api/v1/admin/devices/" + encodeURIComponent(deviceId), { method: "DELETE" })
          .then(function (result) {
            delete state.confirmDelete[deviceId];
            delete state.expandedLogs[deviceId];
            delete state.logCache[deviceId];
            showBanner("success", "Deleted device <strong>" + escapeHtml(result.name) + "</strong> (" + escapeHtml(result.device_id) + ").");
            return loadAll();
          })
          .catch(function (e) { showBanner("error", "Delete failed: " + escapeHtml(e.message)); })
          .then(function () { btn.disabled = false; });
      });
    });
  }

  function renderPending() {
    var wrap = document.getElementById("pendingTableWrap");
    if (!state.pending.length) {
      wrap.innerHTML = '<div class="empty">No devices are currently waiting to be paired.</div>';
      return;
    }
    if (!state.customers.length) {
      wrap.innerHTML = '<div class="empty">Add a customer below before claiming a pairing code.</div>';
      return;
    }
    var custOptions = state.customers.map(function (c) {
      return '<option value="' + c.id + '">' + escapeHtml(c.name) + '</option>';
    }).join("");
    var html = '<table><thead><tr><th>Code</th><th>Customer</th><th>Device name</th><th></th></tr></thead><tbody>';
    state.pending.forEach(function (code) {
      html += '<tr>' +
        '<td style="font-family:monospace; font-size:15px; letter-spacing:0.05em;">' + escapeHtml(code) + '</td>' +
        '<td><select data-code="' + escapeHtml(code) + '" class="pending-customer">' + custOptions + '</select></td>' +
        '<td><input type="text" class="pending-name" data-code="' + escapeHtml(code) + '" value="Table Tent" style="width:140px;"></td>' +
        '<td><button class="primary" data-action="claim" data-code="' + escapeHtml(code) + '">Claim</button></td>' +
      '</tr>';
    });
    html += '</tbody></table><div class="muted-note">Codes expire 15 minutes after the device requested one.</div>';
    wrap.innerHTML = html;

    wrap.querySelectorAll('button[data-action="claim"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        var code = btn.getAttribute("data-code");
        var select = wrap.querySelector('.pending-customer[data-code="' + code + '"]');
        var nameInput = wrap.querySelector('.pending-name[data-code="' + code + '"]');
        var customerId = parseInt(select.value, 10);
        var name = (nameInput.value || "Table Tent").trim();
        btn.disabled = true;
        apiFetch("/api/v1/admin/pairing/" + encodeURIComponent(code) + "/claim", {
          method: "POST",
          body: { customer_id: customerId, name: name },
        }).then(function (result) {
          showBanner("success",
            "Claimed <strong>" + escapeHtml(code) + "</strong> as device <strong>" + escapeHtml(result.device_id) +
            "</strong>. The device will pick up its credentials automatically within a few seconds." +
            "<div class='secret-box'>device_id: " + escapeHtml(result.device_id) + "<br>secret: " + escapeHtml(result.secret) + "</div>" +
            "<div class='muted-note'>That secret is shown here once, only as a fallback for manual entry - the device doesn't need you to copy it.</div>",
            { sticky: true }
          );
          return loadAll();
        }).catch(function (e) {
          showBanner("error", "Claim failed: " + escapeHtml(e.message));
        }).then(function () { btn.disabled = false; });
      });
    });
  }

  function customerBadge(c) {
    if (c.subscription_status === "active") return '<span class="badge active">Active</span>';
    if (c.subscription_status === "failed") return '<span class="badge failed">Payment failed</span>';
    if (c.subscription_status === "canceled") return '<span class="badge canceled">Canceled</span>';
    return '<span class="badge">' + escapeHtml(c.subscription_status) + '</span>';
  }

  function renderCustomers() {
    var wrap = document.getElementById("customersTableWrap");
    if (!state.customers.length) {
      wrap.innerHTML = '<div class="empty">No customers yet.</div>';
      return;
    }
    var deviceCounts = {};
    state.devices.forEach(function (d) {
      deviceCounts[d.customer_id] = (deviceCounts[d.customer_id] || 0) + 1;
    });
    var html = '<table><thead><tr><th>Customer</th><th>Subscription</th><th>Devices</th></tr></thead><tbody>';
    state.customers.forEach(function (c) {
      html += '<tr>' +
        '<td>' + escapeHtml(c.name) + '</td>' +
        '<td>' + customerBadge(c) + '</td>' +
        '<td class="dim">' + (deviceCounts[c.id] || 0) + '</td>' +
      '</tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
  }

  function renderAll() {
    renderDevices();
    renderPending();
    renderCustomers();
    document.getElementById("lastUpdated").textContent = "Updated " + new Date().toLocaleTimeString();
  }

  async function loadAll() {
    var results = await Promise.all([
      apiFetch("/api/v1/admin/devices"),
      apiFetch("/api/v1/admin/customers"),
      apiFetch("/api/v1/admin/pairing/pending"),
    ]);
    state.devices = results[0];
    state.customers = results[1];
    state.pending = results[2];
    renderAll();
  }

  function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(function () {
      loadAll().catch(function () { /* apiFetch already surfaced the error / login prompt */ });
    }, 20000);
  }

  function init() {
    var key = getKey();
    if (!key) { showLogin(); return; }
    showMain();
    loadAll().then(startAutoRefresh).catch(function (e) {
      if (e.message !== "unauthorized") showBanner("error", "Couldn't load the fleet: " + escapeHtml(e.message), { sticky: true });
    });
  }

  document.getElementById("keySubmit").addEventListener("click", function () {
    var val = document.getElementById("keyInput").value.trim();
    if (!val) return;
    setKey(val);
    showMain();
    loadAll().then(startAutoRefresh).catch(function (e) {
      showLogin("Couldn't connect with that key: " + e.message);
    });
  });
  document.getElementById("keyInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter") document.getElementById("keySubmit").click();
  });
  document.getElementById("forgetKeyBtn").addEventListener("click", function () {
    clearKey();
    showLogin();
  });
  document.getElementById("refreshBtn").addEventListener("click", function () {
    loadAll().catch(function (e) {
      if (e.message !== "unauthorized") showBanner("error", "Refresh failed: " + escapeHtml(e.message));
    });
  });
  document.getElementById("addCustomerBtn").addEventListener("click", function () {
    var input = document.getElementById("newCustomerName");
    var name = input.value.trim();
    if (!name) return;
    var btn = document.getElementById("addCustomerBtn");
    btn.disabled = true;
    apiFetch("/api/v1/admin/customers", { method: "POST", body: { name: name } })
      .then(function () {
        input.value = "";
        return loadAll();
      })
      .catch(function (e) { showBanner("error", "Couldn't add customer: " + escapeHtml(e.message)); })
      .then(function () { btn.disabled = false; });
  });

  init();
})();
</script>
</body>
</html>
"""
