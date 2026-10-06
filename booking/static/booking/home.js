/* Home → pick places → choose a vehicle → book. */
(function () {
  "use strict";
  var M = window.MOB, $ = M.$;
  var boot = JSON.parse($("boot").textContent);
  var state = { pickup: null, drop: null, options: [], chosen: null, want: null, goods: "", route: null };
  var map, marks = [], routeLine = null, nearbyMarks = [], nearbyTimer, optionsSeq = 0;

  function art(category) {
    return M.static + "vehicles/" + ({ two_wheeler: "scooter", three_wheeler: "auto" }[category] || "pickup") + ".png";
  }
  function placeFrom(p) {
    return { title: p.title, subtitle: p.subtitle || "", address: p.address, details: p.details || "", lat: +p.lat, lng: +p.lng };
  }

  /* ------------------------------------------------------------- home */
  function renderPickup() {
    var p = state.pickup;
    $("pickup-t").textContent = p ? p.title : "Set pickup location";
    $("pickup-s").textContent = p ? (p.details ? p.details + ", " : "") + (p.subtitle || "") : "Tap to choose where to pick up from";
  }

  function renderQuick() {
    var html = boot.saved.map(function (p, i) {
      var img = p.kind === "home" ? "home" : p.kind === "work" ? "work" : "pin";
      return '<button data-q="' + i + '"><img src="' + M.static + "img/" + img + '.png" alt=""><span class="ellipsis">' + M.esc(p.name) + "</span></button>";
    }).join("");
    var kinds = boot.saved.map(function (p) { return p.kind; });
    if (kinds.indexOf("home") < 0) html += '<button data-add="home"><span class="add"><i data-lucide="plus" style="width:16px;height:16px"></i></span>Add Home</button>';
    if (kinds.indexOf("work") < 0) html += '<button data-add="work"><span class="add"><i data-lucide="plus" style="width:16px;height:16px"></i></span>Add Work</button>';
    $("quick").innerHTML = html;
    $("quick").querySelectorAll("[data-q]").forEach(function (b) {
      b.onclick = function () {
        var p = boot.saved[+b.dataset.q];
        state.drop = placeFrom({ title: p.name, subtitle: p.address, address: p.address, details: p.details, lat: p.lat, lng: p.lng });
        if (p.contact_phone) prefillReceiver(p.contact_name, p.contact_phone);
        openReview();
      };
    });
    $("quick").querySelectorAll("[data-add]").forEach(function (b) {
      b.onclick = function () { addSaved(b.dataset.add); };
    });
    M.icons();
  }

  function addSaved(kind) {
    M.pickPlace({ mode: "save", kind: kind, recent: boot.recent }).then(function (p) {
      if (!p) return;
      return M.api(M.urls.saved, p).then(function (saved) {
        boot.saved = boot.saved.filter(function (x) { return x.id !== saved.id; }).concat([saved]);
        renderQuick(); M.toast(saved.name + " saved", "circle-check");
      });
    }).catch(function (e) { M.toast(e.message); });
  }

  function renderLive() {
    $("live").innerHTML = boot.active.map(function (t) {
      var looking = t.status === "requested" || t.status === "no_driver_available";
      var label = M.statusLabel(t.status, t.status_label);
      return '<a class="live-card fade-up" href="' + t.url + '"><span class="veh"><img src="' + t.image + '" alt="" class="drive"></span>' +
        '<span class="grow"><span class="pill ' + (looking ? "amber" : "brand") + '"><span class="live-dot"></span>' + M.esc(label) + "</span>" +
        '<b class="ellipsis" style="display:block;margin-top:6px">' + M.esc(t.vehicle) + " to " + M.esc(t.drop.split(",")[0]) + "</b>" +
        '<span class="small muted">' + M.esc(t.number) + " · Track live</span></span><i data-lucide=\"chevron-right\"></i></a>";
    }).join("");
    if (boot.active.length) $("live").style.marginBottom = "4px";
  }

  function renderVehicles() {
    var list = boot.catalogue;
    $("veh-count").textContent = list.length ? list.length + (list.length === 1 ? " type" : " types") : "";
    if (!list.length) {
      $("vgrid").innerHTML = '<div class="card empty" style="grid-column:1/-1"><img src="' + M.static + 'vehicles/pickup.png" alt=""><h3>No vehicles yet</h3><p class="small muted">Bookings open as soon as vehicles are added.</p></div>';
      return;
    }
    $("vgrid").innerHTML = list.map(function (v, i) {
      return '<button class="vcard" data-v="' + i + '" style="--tint:' + M.tint(v.image) + ";--i:" + i + '">' +
        '<span class="stage"><span class="speed"><i></i><i></i><i></i></span><span class="veh-wrap"><img class="veh-img" src="' + v.image + '" alt=""></span></span>' +
        '<span class="info"><b>' + M.esc(v.name) + '</b><span class="cap">Up to ' + Math.round(v.capacity_kg).toLocaleString("en-IN") + " kg</span>" +
        '<span class="foot"><span class="from">Starts at<span>' + M.money(v.from_fare) + '</span></span><span class="go"><i data-lucide="arrow-right"></i></span></span></span></button>';
    }).join("");
    M.icons();
    $("vgrid").querySelectorAll("[data-v]").forEach(function (b) {
      b.onclick = function () { state.want = list[+b.dataset.v].vehicle_type_id; chooseDrop(); };
    });
  }

  function renderRecent() {
    var drops = boot.recent.slice(0, 4);
    $("recent-sec").hidden = !drops.length;
    $("recent").innerHTML = drops.map(function (p, i) {
      return '<button class="li" data-r="' + i + '"><span class="ic"><i data-lucide="history"></i></span><span class="grow"><span class="t">' +
        M.esc(p.title) + '</span><span class="s">' + M.esc(p.subtitle) + '</span></span><i data-lucide="chevron-right" class="end"></i></button>';
    }).join("");
    $("recent").querySelectorAll("[data-r]").forEach(function (b) {
      b.onclick = function () { state.drop = placeFrom(drops[+b.dataset.r]); openReview(); };
    });
  }

  function choosePickup() {
    return M.pickPlace({ mode: "pickup", initial: state.pickup, saved: boot.saved, recent: boot.recent }).then(function (p) {
      if (!p) return false;
      state.pickup = p; M.store.set("pickup", { p: p, at: Date.now() }); renderPickup();
      return true;
    });
  }

  function chooseDrop() {
    if (!state.pickup) {
      return choosePickup().then(function (ok) { if (ok) chooseDrop(); });
    }
    var fromChanged = false;
    return M.pickPlace({
      mode: "drop", from: state.pickup, initial: state.drop, saved: boot.saved, recent: boot.recent,
      onFrom: function (p) { state.pickup = p; fromChanged = true; M.store.set("pickup", { p: p, at: Date.now() }); renderPickup(); }
    }).then(function (p) {
      if (!p) { if (fromChanged && reviewOpen) openReview(); return; }
      state.drop = p;
      if (p.saved && p.saved.contact_phone) prefillReceiver(p.saved.contact_name, p.saved.contact_phone);
      openReview();
    });
  }

  function startPickup() {
    var remembered = M.store.get("pickup");
    if (remembered && Date.now() - remembered.at < 6 * 3600 * 1000) { state.pickup = remembered.p; renderPickup(); return; }
    M.locate(true).then(function (ll) {
      return M.api(M.urls.reverse + "?lat=" + ll[0] + "&lng=" + ll[1]).then(function (p) { if (!state.pickup) { state.pickup = p; renderPickup(); } });
    }).catch(function () { if (!state.pickup) renderPickup(); });
  }

  $("pickup-btn").onclick = function () { choosePickup(); };
  $("drop-btn").onclick = function () { chooseDrop(); };
  $("promo-go").onclick = function () {
    var big = boot.catalogue.filter(function (v) { return /truck|lorry/.test(v.image); }).pop() || boot.catalogue[boot.catalogue.length - 1];
    state.want = big && big.vehicle_type_id; chooseDrop();
  };

  /* ------------------------------------------------------------ review */
  var reviewOpen = false;
  function openReview() {
    if (!state.pickup || !state.drop) return;
    fillRoute();
    if (!reviewOpen) {
      reviewOpen = true;
      $("review").classList.add("show"); $("tabbar").hidden = true;
      M.nav.push(closeReview);
    }
    $("rv-scroll").scrollTop = 0;
    ensureMap().then(drawStops);
    loadOptions();
  }
  function closeReview() {
    reviewOpen = false; clearInterval(nearbyTimer);
    $("review").classList.remove("show"); $("tabbar").hidden = false;
  }
  $("rv-back").onclick = function () { M.nav.back(); };
  $("rv-pickup").onclick = function () { choosePickup().then(function (ok) { if (ok) openReview(); }); };
  $("rv-drop").onclick = function () { chooseDrop(); };

  function fillRoute() {
    $("rv-pt").textContent = state.pickup.title;
    $("rv-ps").textContent = (state.pickup.details ? state.pickup.details + ", " : "") + (state.pickup.subtitle || "");
    $("rv-dt").textContent = state.drop.title;
    $("rv-ds").textContent = (state.drop.details ? state.drop.details + ", " : "") + (state.drop.subtitle || "");
    $("rv-meta").innerHTML = '<span class="skel" style="width:90px;height:26px;border-radius:99px"></span><span class="skel" style="width:110px;height:26px;border-radius:99px"></span>';
  }

  function ensureMap() {
    if (map) return Promise.resolve(map);
    return M.map($("rv-map"), { center: [state.pickup.lat, state.pickup.lng], zoom: 13 }).then(function (m) { map = m; return m; });
  }

  function pad() {
    return window.innerWidth >= 900 ? { top: 90, right: 90, bottom: 90, left: 90 } : { top: 80, right: 50, bottom: 50, left: 50 };
  }

  function drawStops() {
    marks.forEach(function (m) { m.remove(); }); marks = [];
    if (routeLine) { routeLine.remove(); routeLine = null; }
    marks.push(map.marker([state.pickup.lat, state.pickup.lng], M.mk.stop("p", "Pickup", state.pickup.title), { z: 5 }));
    marks.push(map.marker([state.drop.lat, state.drop.lng], M.mk.stop("d", "Drop", state.drop.title), { z: 5 }));
    map.fit([[state.pickup.lat, state.pickup.lng], [state.drop.lat, state.drop.lng]], pad());
    loadNearby(); clearInterval(nearbyTimer); nearbyTimer = setInterval(loadNearby, 15000);
  }

  function drawRoute(points) {
    if (!map || !points || points.length < 2) return;
    if (routeLine) routeLine.remove();
    routeLine = map.route(points);
    map.fit(points, pad());
  }

  function loadNearby() {
    if (!map || !state.pickup) return;
    M.api(M.urls.nearby + "?lat=" + state.pickup.lat + "&lng=" + state.pickup.lng).then(function (d) {
      nearbyMarks.forEach(function (m) { m.remove(); }); nearbyMarks = [];
      d.drivers.slice(0, 25).forEach(function (x) { nearbyMarks.push(map.marker([x.lat, x.lng], M.mk.vehicle(art(x.category), "small idle"), { z: 2 })); });
      $("rv-nearby").hidden = !d.drivers.length;
      $("rv-nearby-n").textContent = d.drivers.length + (d.drivers.length === 1 ? " driver nearby" : " drivers nearby");
    }).catch(function () {});
  }

  function loadOptions() {
    var mine = ++optionsSeq;
    state.options = []; select(null);
    $("opts").innerHTML = [0, 1, 2].map(function () {
      return '<div class="opt"><span class="pic skel"></span><span class="grow"><span class="skel" style="display:block;height:15px;width:45%;margin-bottom:8px"></span><span class="skel" style="display:block;height:11px;width:70%"></span></span><span class="skel" style="width:52px;height:20px"></span></div>';
    }).join("");
    M.api(M.urls.options, { pickup: state.pickup, drop: state.drop }).then(function (d) {
      if (mine !== optionsSeq) return;
      state.options = d.options;
      if (d.route && d.route.length) ensureMap().then(function () { drawRoute(d.route); });
      if (!d.options.length) {
        $("opts").innerHTML = '<div class="card empty"><img src="' + M.static + 'vehicles/pickup.png" alt=""><h3>No vehicles here yet</h3><p class="small muted">Try another pickup.</p></div>';
        $("rv-meta").innerHTML = ""; return;
      }
      var o0 = d.options[0];
      $("rv-meta").innerHTML = '<span class="pill"><i data-lucide="route" style="width:14px;height:14px"></i>' + M.km(o0.distance_m) + "</span>" +
        '<span class="pill"><i data-lucide="clock-3" style="width:14px;height:14px"></i>' + M.mins(o0.duration_s) + " drive</span>";
      $("opts").innerHTML = "";
      $("opts").classList.add("stagger");
      d.options.forEach(function (o, i) {
        var b = document.createElement("button"); b.type = "button"; b.className = "opt" + (o.eta_min ? "" : " off");
        b.innerHTML = '<span class="pic" style="--tint:' + M.tint(o.image) + '"><img src="' + o.image + '" alt=""></span>' +
          '<span class="grow"><b>' + M.esc(o.name) + '</b><div class="meta">' +
          Math.round(o.capacity_kg).toLocaleString("en-IN") + " kg · " + (o.eta_min ? '<span class="eta">' + o.eta_min + " min away</span>" : "No driver nearby") + "</div></span>" +
          '<span class="price">' + M.money(o.fare) + "<small>" + (o.eta_min ? "Pay on delivery" : "May take longer") + "</small></span>" +
          '<span class="check"><i data-lucide="check"></i></span>';
        b.onclick = function () { select(o, b); };
        $("opts").appendChild(b);
      });
      M.icons();
      var pick = d.options.filter(function (o) { return o.vehicle_type_id === state.want; })[0] ||
        d.options.filter(function (o) { return o.eta_min; })[0] || d.options[0];
      select(pick, $("opts").children[d.options.indexOf(pick)]);
    }).catch(function (e) {
      if (mine !== optionsSeq) return;
      $("opts").innerHTML = '<div class="card card-pad"><p class="muted">' + M.esc(e.message) + '</p><button class="btn btn-sm" id="retry" style="margin-top:10px">Try again</button></div>';
      $("retry").onclick = loadOptions; $("rv-meta").innerHTML = "";
    });
  }

  function select(o, el) {
    state.chosen = o;
    Array.prototype.forEach.call($("opts").children, function (x) { x.classList.toggle("on", x === el); });
    $("book").disabled = !o;
    $("book-t").textContent = o ? "Book " + o.name : "Choose a vehicle";
    $("book-p").textContent = o ? M.money(o.fare) : "";
    if (o) { state.want = o.vehicle_type_id; }
  }

  // goods
  $("goods").innerHTML = boot.goods.map(function (g) { return '<button class="chip" type="button">' + M.esc(g) + "</button>"; }).join("");
  $("goods").querySelectorAll(".chip").forEach(function (c) {
    c.onclick = function () {
      var on = !c.classList.contains("on");
      $("goods").querySelectorAll(".chip").forEach(function (x) { x.classList.remove("on"); });
      c.classList.toggle("on", on); state.goods = on ? c.textContent : "";
    };
  });

  // receiver
  function prefillReceiver(name, phone) {
    var digits = (phone || "").replace(/\D/g, "").slice(-10);
    if (!digits || "+91" + digits === boot.me.phone) return;
    $("rcv-self").checked = false; $("rcv-fields").hidden = false;
    $("rcv-name").value = name || ""; $("rcv-phone").value = digits;
  }
  $("rcv-self").onchange = function () {
    $("rcv-fields").hidden = this.checked;
    if (!this.checked) setTimeout(function () { $("rcv-name").focus(); }, 50);
  };
  $("rcv-phone").addEventListener("input", function () { this.value = this.value.replace(/\D/g, "").slice(0, 10); });

  // book
  $("book").onclick = function () {
    var o = state.chosen; if (!o) return;
    var self = $("rcv-self").checked, phone = $("rcv-phone").value;
    if (!self && phone.length !== 10) { M.toast("Enter the receiver's 10-digit mobile number."); $("rcv-phone").focus(); return; }
    if (!self && !$("rcv-name").value.trim()) { M.toast("Enter the receiver's name."); $("rcv-name").focus(); return; }
    $("finding-img").src = o.image; $("finding-name").textContent = o.name; $("finding").hidden = false;
    var started = Date.now();
    M.api(M.urls.book, {
      vehicle_type_id: o.vehicle_type_id, pickup: state.pickup, drop: state.drop, goods: state.goods,
      receiver_name: self ? "" : $("rcv-name").value.trim(), receiver_phone: self ? "" : phone, notes: $("note").value.trim()
    }).then(function (d) {
      M.store.set("pickup", { p: state.pickup, at: Date.now() });
      // Let the animation breathe for a moment before the tracking screen.
      setTimeout(function () { location.href = d.url; }, Math.max(0, 1400 - (Date.now() - started)));
    }).catch(function (e) { $("finding").hidden = true; M.toast(e.message, "circle-alert"); });
  };

  /* -------------------------------------------------------------- start */
  renderQuick(); renderLive(); renderVehicles(); renderRecent();
  if (boot.again) {
    state.pickup = placeFrom(boot.again.pickup); state.drop = placeFrom(boot.again.drop); state.want = boot.again.vehicle_type_id;
    if (boot.again.receiver_phone) prefillReceiver(boot.again.receiver_name, boot.again.receiver_phone);
    renderPickup(); history.replaceState(null, "", location.pathname); openReview();
  } else {
    startPickup();
  }
  M.icons();
  // Load the map library in the background so the next screens open instantly.
  (window.requestIdleCallback || setTimeout)(function () { M.loadMaps().catch(function () {}); });
})();
