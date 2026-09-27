/* Shared helpers for every booking page: API calls, toasts, bottom sheets,
   formatting and icons. Everything hangs off window.MOB. */
(function () {
  "use strict";
  var M = window.MOB;

  M.$ = function (id) { return document.getElementById(id); };

  M.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  M.icons = function () { if (window.lucide) window.lucide.createIcons(); };
  document.addEventListener("DOMContentLoaded", M.icons);

  /* ---- loaders: a slim bar for every request, a veil for page changes ---- */
  var bar, veil, inflight = 0;
  function ensureLoaders() {
    if (bar) return;
    bar = document.createElement("div"); bar.className = "topbar-load"; document.body.appendChild(bar);
    veil = document.createElement("div"); veil.className = "page-veil";
    veil.innerHTML = '<div class="loader-logo"><img class="logo" src="' + M.static + 'img/logo-dark.svg" alt="" style="height:26px"><div class="dots"><i></i><i></i><i></i></div></div>';
    document.body.appendChild(veil);
  }
  M.loading = function (on) {
    ensureLoaders();
    inflight = Math.max(0, inflight + (on ? 1 : -1));
    if (inflight) { bar.classList.remove("done"); void bar.offsetWidth; bar.classList.add("on"); }
    else { bar.classList.remove("on"); bar.classList.add("done"); }
  };
  M.leaving = function () { ensureLoaders(); M.loading(true); veil.classList.add("on"); };
  document.addEventListener("DOMContentLoaded", ensureLoaders);
  // Leaving for another page of the app: show it's on its way.
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank") return;
    var url = new URL(a.href, location.href);
    if (url.origin !== location.origin || /^\/(admin|api|media|static)\//.test(url.pathname)) return;
    if (url.pathname === location.pathname && url.search === location.search) return;
    M.leaving();
  });
  document.addEventListener("submit", function (e) { if (!e.defaultPrevented && e.target.method === "post" && !e.target.dataset.js) M.leaving(); });
  window.addEventListener("pageshow", function () {  // back/forward cache: clear any leftover veil
    if (veil) { veil.classList.remove("on"); inflight = 0; bar.classList.remove("on"); }
  });

  /* fetch JSON; rejects with an Error carrying the server's own message */
  M.api = function (url, body, method) {
    method = method || (body ? "POST" : "GET");
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (method !== "GET") opts.headers["X-CSRFToken"] = M.csrf;
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    M.loading(true);
    return fetch(url, opts).then(function (r) {
      M.loading(false);
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.status === 401) { window.location = M.urls.login + "?next=" + encodeURIComponent(location.pathname + location.search); }
        if (!r.ok) { var e = new Error(d.error || "Something went wrong. Please try again."); e.code = d.code; throw e; }
        return d;
      });
    }, function () { M.loading(false); throw new Error("You're offline. Check your connection and try again."); });
  };

  var toastTimer;
  M.toast = function (text, icon) {
    var t = document.querySelector(".toast");
    if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
    t.innerHTML = (icon ? '<i data-lucide="' + icon + '"></i>' : "") + "<span>" + M.esc(text) + "</span>";
    M.icons();
    requestAnimationFrame(function () { t.classList.add("show"); });
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3200);
  };

  /* A bottom sheet: M.sheet(html) → {el, close}. Closes on scrim tap / Escape. */
  M.sheet = function (html, onClose) {
    var scrim = document.createElement("div"); scrim.className = "scrim";
    var el = document.createElement("div"); el.className = "bsheet"; el.setAttribute("role", "dialog");
    el.innerHTML = '<div class="grab"></div>' + html;
    document.body.appendChild(scrim); document.body.appendChild(el); M.icons();
    requestAnimationFrame(function () { scrim.classList.add("show"); el.classList.add("show"); });
    function close() {
      scrim.classList.remove("show"); el.classList.remove("show");
      document.removeEventListener("keydown", key);
      setTimeout(function () { scrim.remove(); el.remove(); }, 380);
      if (onClose) onClose();
    }
    function key(e) { if (e.key === "Escape") close(); }
    scrim.addEventListener("click", close); document.addEventListener("keydown", key);
    return { el: el, close: close };
  };

  M.busy = function (btn, on) {
    if (on) { btn.dataset.html = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spin"></span>'; }
    else { btn.disabled = false; if (btn.dataset.html) btn.innerHTML = btn.dataset.html; M.icons(); }
  };

  /* What a booking's status means to the customer. */
  M.statusLabel = function (status, fallback) {
    return { requested: "Finding a driver", no_driver_available: "Looking for a driver", assigned: "Driver on the way",
      arrived_at_pickup: "Driver at pickup", in_progress: "On the way to drop", completed: "Delivered", cancelled: "Cancelled" }[status] || fallback || status;
  };

  M.money = function (n) { return "₹" + Math.round(n || 0).toLocaleString("en-IN"); };
  M.km = function (m) { return m >= 1000 ? (m / 1000).toFixed(m >= 10000 ? 0 : 1) + " km" : Math.round(m / 10) * 10 + " m"; };
  M.mins = function (s) { var m = Math.max(1, Math.round(s / 60)); return m >= 60 ? Math.floor(m / 60) + " h " + (m % 60) + " min" : m + " min"; };
  M.initials = function (name) {
    return (name || "?").trim().split(/\s+/).map(function (w) { return w[0]; }).slice(0, 2).join("").toUpperCase();
  };
  M.haversine = function (a, b) {
    var R = 6371, r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLng = (b[1] - a[1]) * r;
    var x = Math.pow(Math.sin(dLat / 2), 2) + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.pow(Math.sin(dLng / 2), 2);
    return 2 * R * Math.asin(Math.sqrt(x));
  };

  /* A soft background per kind of vehicle, from its picture's file name. */
  M.tint = function (image) {
    var m = /(scooter|auto|pickup|truck|lorry)\.png/.exec(image || "");
    return { scooter: "#ffe8ef", auto: "#fff4cc", pickup: "#ffe7e1", truck: "#fff0d6", lorry: "#e3f5e6" }[m && m[1]] || "#eef2ff";
  };

  /* Where the browser thinks we are (cached for a minute). */
  var here = null;
  M.locate = function (quiet) {
    return new Promise(function (resolve, reject) {
      if (here && Date.now() - here.at < 60000) return resolve(here.ll);
      if (!navigator.geolocation) return reject(new Error("Location isn't available in this browser."));
      navigator.geolocation.getCurrentPosition(function (p) {
        here = { ll: [p.coords.latitude, p.coords.longitude], at: Date.now() };
        resolve(here.ll);
      }, function () {
        var e = new Error("Allow location access to use where you are — or search a place.");
        if (!quiet) M.toast(e.message, "map-pin-off");
        reject(e);
      }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    });
  };
  M.lastKnown = function () { return here && here.ll; };

  /* Screens opened on top of a page join the browser history, so the phone's
     back button closes them: nav.push(closeFn) on open, nav.back() to close
     the top one, nav.drop(n) to leave n screens at once without their closers. */
  M.nav = { stack: [], ignore: 0 };
  M.nav.push = function (close) { M.nav.stack.push(close); history.pushState({ mob: M.nav.stack.length }, ""); };
  M.nav.back = function () { if (M.nav.stack.length) history.back(); };
  M.nav.drop = function (n) {
    n = Math.min(n, M.nav.stack.length); if (!n) return;
    M.nav.stack.splice(-n); M.nav.ignore++; history.go(-n);
  };
  window.addEventListener("popstate", function () {
    if (M.nav.ignore) { M.nav.ignore--; return; }
    var close = M.nav.stack.pop(); if (close) close();
  });

  /* Numbers that count up to their value (data-count, optional data-prefix). */
  M.countUp = function (els) {
    Array.prototype.forEach.call(els, function (el) {
      var to = parseFloat(el.dataset.count) || 0, pre = el.dataset.prefix || "", start = performance.now(), ms = 900;
      function tick(now) {
        var t = Math.min(1, (now - start) / ms), v = Math.round(to * (1 - Math.pow(1 - t, 3)));
        el.textContent = pre + v.toLocaleString("en-IN");
        if (t < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  };

  M.store = {
    get: function (k, d) { try { var v = localStorage.getItem("mob." + k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem("mob." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  };
})();
