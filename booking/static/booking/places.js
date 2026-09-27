/* The place picker: search (Google Places through our server) → fine-tune on
   the map → confirm. Used for pickup, drop and saving an address.

     MOB.pickPlace({mode: "pickup" | "drop" | "save", from, initial, saved, recent, kind, label})
       → Promise<{title, subtitle, address, details, lat, lng, kind?, label?}> (null if closed)
*/
(function () {
  "use strict";
  var M = window.MOB, $ = M.$;
  var root, map, pinTimer, searchTimer, seq = 0, session = "", st = {};

  var COPY = {
    pickup: { title: "Pickup location", ph: "Search pickup area, street, landmark…", btn: "Confirm pickup", tip: "Your goods will be picked up here", dot: "p", pin: "green" },
    drop: { title: "Drop location", ph: "Where should it be delivered?", btn: "Confirm drop", tip: "Your goods will be delivered here", dot: "d", pin: "red" },
    save: { title: "Add an address", ph: "Search an area, street or building…", btn: "Save address", tip: "Move the map to the exact spot", dot: "p", pin: "" }
  };

  function uuid() {
    return (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^a-z0-9-]/gi, "");
  }

  function build() {
    root = document.createElement("div");
    root.className = "screen up"; root.id = "pk";
    root.innerHTML =
      '<div id="pk-s1" style="display:flex;flex-direction:column;height:100%">' +
      '  <div class="pk-head">' +
      '    <div class="row"><button class="icon-btn flat" id="pk-close" aria-label="Back"><i data-lucide="arrow-left"></i></button><h2 id="pk-title"></h2></div>' +
      '    <div class="pk-from" id="pk-from"><span class="dot p"></span><span class="grow ellipsis" id="pk-from-t"></span></div>' +
      '    <label class="pk-search"><span class="dot" id="pk-dot"></span><input id="pk-q" type="search" autocomplete="off" enterkeyhint="search" spellcheck="false">' +
      '      <button type="button" class="icon-btn flat" id="pk-clear" style="width:30px;height:30px" aria-label="Clear" hidden><i data-lucide="x" style="width:16px;height:16px"></i></button></label>' +
      "  </div>" +
      '  <div class="pk-body">' +
      '    <div class="pk-actions">' +
      '      <button id="pk-here"><span class="ic" style="background:var(--blue-soft);color:var(--blue)"><i data-lucide="locate-fixed"></i></span>Use current location</button>' +
      '      <button id="pk-onmap"><span class="ic" style="background:var(--brand-soft);color:var(--brand)"><i data-lucide="map-pinned"></i></span>Locate on map</button>' +
      "    </div>" +
      '    <div id="pk-list"></div>' +
      "  </div>" +
      "</div>" +
      '<div id="pk-s2" style="display:none;flex-direction:column;height:100%">' +
      '  <div class="pinmap"><div class="map" id="pk-map"></div>' +
      '    <div class="center-pin" id="pk-pin"><div class="head"></div></div><div class="pin-shadow"></div><div class="pin-tip" id="pk-tip"></div>' +
      '    <button class="icon-btn pin-back" id="pk-pin-back" aria-label="Back to search"><i data-lucide="arrow-left"></i></button>' +
      '    <button class="icon-btn pin-locate" id="pk-locate" aria-label="Go to my location"><i data-lucide="locate-fixed"></i></button>' +
      "  </div>" +
      '  <div class="pin-card">' +
      '    <div class="row" style="align-items:flex-start"><span class="dot" id="pk-dot2" style="margin-top:7px"></span>' +
      '      <div class="grow"><div class="addr-t ellipsis" id="pk-at"></div><div class="small muted clamp2" id="pk-as"></div></div>' +
      '      <button class="btn btn-sm" id="pk-change" style="flex:none">Change</button></div>' +
      '    <input class="input" id="pk-details" maxlength="120" placeholder="House / flat no., floor, building (optional)" style="margin-top:14px">' +
      '    <div id="pk-save" hidden style="margin-top:14px"><div class="tiny b muted" style="margin-bottom:8px">SAVE AS</div>' +
      '      <div class="chips" id="pk-kinds"><button class="chip" data-k="home">🏠 Home</button><button class="chip" data-k="work">🏢 Work</button><button class="chip" data-k="other">📍 Other</button></div>' +
      '      <input class="input" id="pk-label" maxlength="40" placeholder="Name it, e.g. Shop, Warehouse, Mom\'s" style="margin-top:10px" hidden></div>' +
      '    <button class="btn btn-primary" id="pk-ok" style="margin-top:14px"></button>' +
      "  </div>" +
      "</div>";
    document.body.appendChild(root);
    M.icons();

    $("pk-close").onclick = function () { M.nav.back(); };
    $("pk-pin-back").onclick = function () { M.nav.back(); };
    $("pk-change").onclick = function () { M.nav.back(); setTimeout(function () { $("pk-q").focus(); }, 60); };
    $("pk-q").addEventListener("input", onType);
    $("pk-clear").onclick = function () { $("pk-q").value = ""; onType(); $("pk-q").focus(); };
    $("pk-here").onclick = useHere;
    $("pk-onmap").onclick = function () {
      var start = st.initial || st.from || (M.lastKnown() && { lat: M.lastKnown()[0], lng: M.lastKnown()[1] });
      openPin(start ? { lat: start.lat, lng: start.lng } : null, true);
    };
    $("pk-locate").onclick = function () { M.locate().then(function (ll) { map && map.view(ll, 17); }).catch(function () {}); };
    $("pk-ok").onclick = confirm;
    Array.prototype.forEach.call($("pk-kinds").children, function (c) {
      c.onclick = function () { setKind(c.dataset.k); };
    });
  }

  function setKind(k) {
    st.kind = k;
    Array.prototype.forEach.call($("pk-kinds").children, function (c) { c.classList.toggle("on", c.dataset.k === k); });
    $("pk-label").hidden = k !== "other";
    if (k === "other") setTimeout(function () { $("pk-label").focus(); }, 50);
  }

  /* ------------------------------------------------------------ search */
  function onType() {
    var q = $("pk-q").value.trim();
    $("pk-clear").hidden = !q;
    clearTimeout(searchTimer);
    if (q.length < 2) { renderIdle(); return; }
    $("pk-list").innerHTML = '<div class="pk-label">Searching</div><div class="list">' +
      [0, 1, 2, 3].map(function () { return '<div class="li"><span class="ic skel"></span><span class="grow"><span class="skel" style="display:block;height:13px;width:60%;margin-bottom:7px"></span><span class="skel" style="display:block;height:11px;width:85%"></span></span></div>'; }).join("") + "</div>";
    searchTimer = setTimeout(function () { search(q); }, 220);
  }

  function search(q) {
    var mine = ++seq, near = st.from || M.lastKnown() && { lat: M.lastKnown()[0], lng: M.lastKnown()[1] };
    var url = M.urls.places + "?q=" + encodeURIComponent(q) + "&session=" + session + (near ? "&lat=" + near.lat + "&lng=" + near.lng : "");
    M.api(url).then(function (d) {
      if (mine !== seq) return;
      if (!d.places.length) {
        $("pk-list").innerHTML = '<div class="empty fade-up"><img src="' + M.static + 'img/pin.png" alt="" style="width:72px"><h3>No places found</h3>' +
          '<p class="small muted" style="margin-top:4px">Try a nearby landmark or area name — or locate it on the map.</p></div>';
        return;
      }
      var google = d.places.some(function (p) { return p.place_id; });
      $("pk-list").innerHTML = '<div class="pk-label">Results</div><div class="list stagger" id="pk-res"></div>' +
        (google ? '<div class="powered">powered by <b>Google</b></div>' : "");
      d.places.forEach(function (p) {
        var b = document.createElement("button"); b.className = "li"; b.type = "button";
        b.innerHTML = '<span class="ic"><i data-lucide="' + iconFor(p.types) + '"></i></span><span class="grow"><span class="t">' + highlight(p.title, p.match) +
          '</span><span class="s">' + M.esc(p.subtitle) + "</span></span>" + (p.distance_m != null ? '<span class="end">' + M.km(p.distance_m) + "</span>" : "");
        b.onclick = function () { choose(p, b); };
        $("pk-res").appendChild(b);
      });
      M.icons();
    }).catch(function (e) {
      if (mine === seq) $("pk-list").innerHTML = '<p class="muted" style="padding:20px 4px">' + M.esc(e.message) + "</p>";
    });
  }

  function iconFor(types) {
    types = types || [];
    if (types.indexOf("establishment") >= 0) return "building-2";
    if (types.indexOf("route") >= 0) return "route";
    if (types.indexOf("sublocality") >= 0 || types.indexOf("locality") >= 0 || types.indexOf("political") >= 0) return "map";
    return "map-pin";
  }

  function highlight(text, match) {
    if (!match || !match.length) return M.esc(text);
    var out = "", at = 0;
    match.forEach(function (m) {
      out += M.esc(text.slice(at, m[0])) + '<span class="hl">' + M.esc(text.substr(m[0], m[1])) + "</span>";
      at = m[0] + m[1];
    });
    return out + M.esc(text.slice(at));
  }

  function choose(p, btn) {
    if (p.lat != null) return openPin(p, false);
    btn.style.opacity = ".5";
    M.api(M.urls.place + "?id=" + encodeURIComponent(p.place_id) + "&session=" + session).then(function (full) {
      session = uuid();  // a details call ends Google's billing session
      openPin(full, false);
    }).catch(function (e) { M.toast(e.message); }).finally(function () { btn.style.opacity = ""; });
  }

  function renderIdle() {
    var html = "";
    var saved = (st.saved || []).filter(function () { return st.mode !== "save"; });
    if (saved.length) {
      html += '<div class="pk-label">Saved addresses</div><div class="list stagger">' + saved.map(function (p, i) {
        return '<button class="li" data-saved="' + i + '"><span class="ic"><img src="' + M.static + "img/" + (p.kind === "work" ? "work" : p.kind === "home" ? "home" : "pin") + '.png" alt=""></span>' +
          '<span class="grow"><span class="t">' + M.esc(p.name) + '</span><span class="s">' + M.esc((p.details ? p.details + ", " : "") + p.address) + "</span></span></button>";
      }).join("") + "</div>";
    }
    var recent = st.recent || [];
    if (recent.length) {
      html += '<div class="pk-label">Recent</div><div class="list stagger">' + recent.map(function (p, i) {
        return '<button class="li" data-recent="' + i + '"><span class="ic"><i data-lucide="history"></i></span>' +
          '<span class="grow"><span class="t">' + M.esc(p.title) + '</span><span class="s">' + M.esc(p.subtitle) + "</span></span></button>";
      }).join("") + "</div>";
    }
    if (!html) {
      html = '<div class="empty fade-up" style="padding-top:40px"><img src="' + M.static + 'img/pin.png" alt="" style="width:80px">' +
        '<h3>Search any address in India</h3><p class="small muted" style="margin-top:4px">Shops, societies, streets, landmarks — or drop a pin on the map.</p></div>';
    }
    $("pk-list").innerHTML = html;
    $("pk-list").querySelectorAll("[data-saved]").forEach(function (b) {
      b.onclick = function () { var p = saved[+b.dataset.saved]; finish({ title: p.name, subtitle: p.address, address: p.address, details: p.details, lat: p.lat, lng: p.lng, saved: p }, 1); };
    });
    $("pk-list").querySelectorAll("[data-recent]").forEach(function (b) {
      b.onclick = function () { openPin(recent[+b.dataset.recent], false); };
    });
    M.icons();
  }

  function useHere() {
    var btn = $("pk-here"); btn.style.opacity = ".6";
    M.locate().then(function (ll) {
      return M.api(M.urls.reverse + "?lat=" + ll[0] + "&lng=" + ll[1]).then(function (p) { openPin(p, false); });
    }).catch(function () {}).finally(function () { btn.style.opacity = ""; });
  }

  /* --------------------------------------------------------------- pin */
  function openPin(place, lookup) {
    $("pk-s1").style.display = "none"; $("pk-s2").style.display = "flex";
    M.nav.push(closePin);
    st.pinned = place && place.title ? place : null;
    showAddress(st.pinned);
    $("pk-details").value = (place && place.details) || (st.initial && st.initial.details) || "";
    var ll = place ? [place.lat, place.lng] : (M.lastKnown() || [26.8467, 80.9462]);
    // The place already has a good name: don't replace it with the reverse
    // geocode of the map settling on it (only a move the customer makes).
    st.skipUntil = !lookup && st.pinned ? Date.now() + 1500 : 0;
    if (!map) {
      M.map($("pk-map"), { center: ll, zoom: 17 }).then(function (m) {
        map = m;
        map.on("movestart", function () { $("pk-pin").classList.add("lift"); });
        map.on("idle", function () {
          $("pk-pin").classList.remove("lift");
          if (Date.now() < st.skipUntil) { st.skipUntil = 0; return; }
          clearTimeout(pinTimer); showAddress(null);
          pinTimer = setTimeout(lookupCenter, 250);
        });
        if (lookup || !st.pinned) lookupCenter();
      });
    } else {
      map.view(ll, 17);
      if (lookup || !st.pinned) setTimeout(lookupCenter, 400);
    }
  }

  function closePin() { $("pk-s2").style.display = "none"; $("pk-s1").style.display = "flex"; }

  function lookupCenter() {
    if (!map) return;
    var c = map.center(), mine = ++seq;
    showAddress(null);
    M.api(M.urls.reverse + "?lat=" + c[0] + "&lng=" + c[1]).then(function (p) {
      if (mine !== seq) return;
      st.pinned = p; showAddress(p);
    }).catch(function () {});
  }

  function showAddress(p) {
    $("pk-ok").disabled = !p;
    if (!p) {
      $("pk-at").innerHTML = '<span class="skel" style="display:block;height:18px;width:55%"></span>';
      $("pk-as").innerHTML = '<span class="skel" style="display:block;height:12px;width:85%;margin-top:8px"></span>';
      return;
    }
    $("pk-at").textContent = p.title; $("pk-as").textContent = p.subtitle || "";
  }

  function confirm() {
    if (!st.pinned) return;
    if (map) { var c = map.center(); st.pinned.lat = c[0]; st.pinned.lng = c[1]; }
    var out = {
      title: st.pinned.title, subtitle: st.pinned.subtitle || "", address: st.pinned.address || st.pinned.title,
      lat: st.pinned.lat, lng: st.pinned.lng, details: $("pk-details").value.trim()
    };
    if (st.mode === "save") {
      if (st.kind === "other" && !$("pk-label").value.trim()) { $("pk-label").focus(); M.toast("Give this address a name."); return; }
      out.kind = st.kind; out.label = $("pk-label").value.trim();
    }
    finish(out, 2);
  }

  /* ------------------------------------------------------------- open/close */
  function finish(place, depth) {
    var done = st.done; st.done = null;
    root.classList.remove("show");
    M.nav.drop(depth);
    if (done) done(place);
  }

  M.pickPlace = function (opts) {
    if (!root) build();
    st = { mode: opts.mode || "drop", from: opts.from, initial: opts.initial, saved: opts.saved, recent: opts.recent, kind: opts.kind || "home" };
    session = uuid();
    var c = COPY[st.mode];
    $("pk-title").textContent = opts.title || c.title;
    $("pk-q").placeholder = c.ph; $("pk-q").value = ""; $("pk-clear").hidden = true;
    $("pk-dot").className = "dot " + c.dot; $("pk-dot2").className = "dot " + c.dot;
    $("pk-ok").textContent = c.btn; $("pk-tip").textContent = c.tip;
    $("pk-pin").className = "center-pin " + c.pin;
    $("pk-here").style.display = st.mode === "drop" ? "none" : "";
    $("pk-onmap").parentNode.style.gridTemplateColumns = st.mode === "drop" ? "1fr" : "";
    $("pk-from").hidden = !(st.mode === "drop" && st.from);
    if (st.from) $("pk-from-t").textContent = "From  " + st.from.title;
    $("pk-save").hidden = st.mode !== "save";
    if (st.mode === "save") { setKind(st.kind); $("pk-label").value = opts.label || ""; }
    $("pk-s2").style.display = "none"; $("pk-s1").style.display = "flex";
    renderIdle();
    requestAnimationFrame(function () { root.classList.add("show"); });
    setTimeout(function () { $("pk-q").focus({ preventScroll: true }); }, 380);
    // Warm the map up while they type.
    M.loadMaps().catch(function () {});
    return new Promise(function (resolve) {
      st.done = resolve;
      M.nav.push(function () { root.classList.remove("show"); var d = st.done; st.done = null; if (d) d(null); });
      if (opts.initial && opts.edit) openPin(opts.initial, false);
    });
  };
})();
