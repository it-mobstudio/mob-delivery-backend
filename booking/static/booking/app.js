/* The booking screen: pickup → drop → vehicle → book. One map, three sheets. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var state = { pickup: null, drop: null, editing: null, options: [], chosen: null, me: null };
  var DEFAULT = [26.8467, 80.9462]; // Lucknow, until we know where the customer is

  var map = L.map("map", { zoomControl: false, attributionControl: true }).setView(DEFAULT, 13);
  map.attributionControl.setPosition("topright");
  L.tileLayer(MOB_MAP.url, { maxZoom: 19, attribution: MOB_MAP.attribution }).addTo(map);
  var layers = { route: L.layerGroup().addTo(map), drivers: L.layerGroup().addTo(map), me: L.layerGroup().addTo(map) };

  function pin(color) {
    return L.divIcon({ className: "", iconSize: [22, 22], iconAnchor: [11, 11],
      html: '<span style="display:block;width:22px;height:22px;border-radius:50%;background:' + color + ';border:4px solid #fff;box-shadow:0 3px 10px rgba(0,0,0,.3)"></span>' });
  }
  var carIcon = L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
    html: '<span style="display:grid;place-items:center;width:26px;height:26px;border-radius:50%;background:#0f1d2e;box-shadow:0 2px 8px rgba(0,0,0,.3)"><svg width="14" height="14" viewBox="0 0 24 24" fill="#fff"><path d="M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11v6a1 1 0 0 1-1 1h-1a1 1 0 0 1-1-1v-1H8v1a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1z"/></svg></span>' });

  // -- sheets -----------------------------------------------------------------------
  var sheets = ["sheet-where", "sheet-pin", "sheet-options"];
  function show(id) {
    sheets.forEach(function (s) { $(s).classList.toggle("away", s !== id); });
    $("center-pin").hidden = id !== "sheet-pin";
    setTimeout(function () { var h = $(id).offsetHeight; if (window.innerWidth < 900) $("locate").style.bottom = (h + 14) + "px"; }, 360);
  }

  // -- where am I -------------------------------------------------------------------
  function locate(setPickup) {
    if (!navigator.geolocation) { if (setPickup) $("val-pickup").textContent = "Set your pickup"; return; }
    navigator.geolocation.getCurrentPosition(function (pos) {
      var ll = [pos.coords.latitude, pos.coords.longitude];
      state.me = ll;
      layers.me.clearLayers();
      L.circleMarker(ll, { radius: 8, color: "#fff", weight: 3, fillColor: "#1463ff", fillOpacity: 1 }).addTo(layers.me);
      map.setView(ll, 15);
      if (setPickup && !state.pickup) {
        mobApi(BOOK.reverse + "?lat=" + ll[0] + "&lng=" + ll[1]).then(function (p) { choose("pickup", p); });
      }
      loadNearby(ll);
    }, function () {
      if (setPickup) { $("val-pickup").textContent = "Set your pickup"; $("val-pickup").classList.add("empty"); }
      mobToast("Turn on location to pick up from where you are — or search a place.");
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
  }
  $("locate").addEventListener("click", function () { locate(false); });

  function loadNearby(ll) {
    mobApi(BOOK.nearby + "?lat=" + ll[0] + "&lng=" + ll[1]).then(function (d) {
      layers.drivers.clearLayers();
      d.drivers.forEach(function (x) { L.marker([x.lat, x.lng], { icon: carIcon, interactive: false }).addTo(layers.drivers); });
      $("nearby-pill").hidden = !d.drivers.length; $("nearby-n").textContent = d.drivers.length;
    }).catch(function () {});
  }
  setInterval(function () { var ll = state.pickup ? [state.pickup.lat, state.pickup.lng] : state.me; if (ll) loadNearby(ll); }, 20000);

  // -- choosing places ----------------------------------------------------------------
  function choose(which, place) {
    state[which] = place;
    var el = $("val-" + which);
    el.textContent = place.title + (place.subtitle ? ", " + place.subtitle : "");
    el.classList.remove("empty");
    if (which === "pickup") loadNearby([place.lat, place.lng]);
    drawRoute();
    if (state.pickup && state.drop) loadOptions();
  }

  function drawRoute() {
    layers.route.clearLayers();
    var pts = [];
    if (state.pickup) { L.marker([state.pickup.lat, state.pickup.lng], { icon: pin("#0e9f6e") }).addTo(layers.route); pts.push([state.pickup.lat, state.pickup.lng]); }
    if (state.drop) { L.marker([state.drop.lat, state.drop.lng], { icon: pin("#d64534") }).addTo(layers.route); pts.push([state.drop.lat, state.drop.lng]); }
    if (pts.length === 2) {
      L.polyline(pts, { color: "#0f1d2e", weight: 4, opacity: .8, dashArray: "2 10", lineCap: "round" }).addTo(layers.route);
      var pad = window.innerWidth >= 900 ? { paddingTopLeft: [480, 60], paddingBottomRight: [60, 60] } : { paddingTopLeft: [40, 90], paddingBottomRight: [40, window.innerHeight * .55] };
      map.fitBounds(pts, pad);
    } else if (pts.length) map.setView(pts[0], 15);
  }

  // search overlay
  var searchTimer;
  function openSearch(which) {
    state.editing = which;
    $("search").hidden = false;
    $("use-current").hidden = which !== "pickup";
    $("search-input").value = "";
    $("search-input").placeholder = which === "pickup" ? "Where should the driver pick up?" : "Where should it be delivered?";
    $("search-results").innerHTML = "";
    setTimeout(function () { $("search-input").focus(); }, 60);
    mobIcons();
  }
  $("btn-pickup").addEventListener("click", function () { openSearch("pickup"); });
  $("btn-drop").addEventListener("click", function () { openSearch("drop"); });
  $("search-close").addEventListener("click", function () { $("search").hidden = true; });
  $("search-input").addEventListener("input", function () {
    clearTimeout(searchTimer);
    var q = this.value.trim();
    if (q.length < 2) { $("search-results").innerHTML = ""; return; }
    $("search-results").innerHTML = '<div style="padding:10px 18px"><div class="skel" style="height:52px;margin-bottom:10px"></div><div class="skel" style="height:52px"></div></div>';
    searchTimer = setTimeout(function () {
      var near = state.pickup ? "&lat=" + state.pickup.lat + "&lng=" + state.pickup.lng : state.me ? "&lat=" + state.me[0] + "&lng=" + state.me[1] : "";
      mobApi(BOOK.places + "?q=" + encodeURIComponent(q) + near).then(function (d) {
        if (!d.places.length) { $("search-results").innerHTML = '<p class="muted" style="padding:14px 18px">No places found. Try a nearby landmark, or choose on map.</p>'; return; }
        $("search-results").innerHTML = "";
        d.places.forEach(function (p) {
          var b = document.createElement("button"); b.className = "result";
          b.innerHTML = '<span class="ic"><i data-lucide="map-pin"></i></span><span class="grow"><b></b><span></span></span>';
          b.querySelector("b").textContent = p.title; b.querySelector("span span").textContent = p.subtitle;
          b.addEventListener("click", function () { $("search").hidden = true; choose(state.editing, p); });
          $("search-results").appendChild(b);
        });
        mobIcons();
      }).catch(function (e) { $("search-results").innerHTML = '<p class="muted" style="padding:14px 18px">' + e.message + "</p>"; });
    }, 300);
  });
  $("use-current").addEventListener("click", function () { $("search").hidden = true; state.pickup = null; locate(true); });

  // pin on the map
  var pinTimer;
  $("use-map").addEventListener("click", function () {
    $("search").hidden = true;
    $("pin-title").textContent = state.editing === "pickup" ? "Move the map to set the pickup" : "Move the map to set the drop";
    var start = state[state.editing] || state.pickup;
    if (start) map.setView([start.lat, start.lng], 17); else if (state.me) map.setView(state.me, 17);
    show("sheet-pin"); pinLookup();
  });
  function pinLookup() {
    clearTimeout(pinTimer);
    $("pin-address").textContent = "…";
    pinTimer = setTimeout(function () {
      var c = map.getCenter();
      mobApi(BOOK.reverse + "?lat=" + c.lat + "&lng=" + c.lng).then(function (p) {
        state.pinned = p; $("pin-address").textContent = p.title + (p.subtitle ? ", " + p.subtitle : "");
      });
    }, 350);
  }
  map.on("moveend", function () { if (!$("sheet-pin").classList.contains("away")) pinLookup(); });
  $("pin-confirm").addEventListener("click", function () {
    if (!state.pinned) return;
    var c = map.getCenter(); state.pinned.lat = c.lat; state.pinned.lng = c.lng;
    show("sheet-where"); choose(state.editing, state.pinned);
  });
  $("pin-cancel").addEventListener("click", function () { show("sheet-where"); });

  // -- vehicle options ---------------------------------------------------------------------
  function loadOptions() {
    show("sheet-options");
    state.chosen = null; updateBook();
    $("opts").innerHTML = [1, 2, 3].map(function () { return '<div class="skel" style="height:78px"></div>'; }).join("");
    $("trip-summary").textContent = "Working out fares…";
    mobApi(BOOK.options, { pickup: state.pickup, drop: state.drop }).then(function (d) {
      state.options = d.options;
      if (!d.options.length) { $("opts").innerHTML = '<p class="muted">No vehicles are available here yet.</p>'; return; }
      var km = (d.options[0].distance_m / 1000).toFixed(1), min = Math.round(d.options[0].duration_s / 60);
      $("trip-summary").textContent = km + " km · about " + min + " min drive";
      $("opts").innerHTML = "";
      d.options.forEach(function (o, i) {
        var b = document.createElement("button"); b.className = "opt"; b.type = "button";
        b.innerHTML = '<img alt=""><span><b></b><div class="meta"></div></span><span class="price"></span>';
        b.querySelector("img").src = o.icon_url || mobArt(o.category, o.name);
        b.querySelector("b").textContent = o.name;
        b.querySelector(".meta").innerHTML = "Up to " + Math.round(o.capacity_kg) + " kg · " +
          (o.eta_min ? '<span class="eta">' + o.eta_min + " min away</span>" : "No driver close by");
        b.querySelector(".price").textContent = "₹" + Math.round(o.fare).toLocaleString("en-IN");
        b.addEventListener("click", function () {
          document.querySelectorAll(".opt").forEach(function (x) { x.classList.remove("on"); });
          b.classList.add("on"); state.chosen = o; updateBook();
        });
        $("opts").appendChild(b);
        if (i === 0 && o.eta_min) b.click();
      });
    }).catch(function (e) {
      $("opts").innerHTML = '<p class="muted">' + e.message + '</p><button class="btn" id="retry">Try again</button>';
      $("retry").addEventListener("click", loadOptions);
    });
  }
  function updateBook() {
    var b = $("book");
    b.disabled = !state.chosen;
    b.textContent = state.chosen ? "Book " + state.chosen.name + " · ₹" + Math.round(state.chosen.fare).toLocaleString("en-IN") : "Choose a vehicle";
  }
  $("opts-back").addEventListener("click", function () { show("sheet-where"); });

  $("book").addEventListener("click", function () {
    var phone = $("rcv-phone").value.replace(/\D/g, "");
    if (phone && phone.length !== 10) { mobToast("Enter the receiver's 10-digit mobile number."); $("more").open = true; return; }
    $("finding-name").textContent = state.chosen.name; $("finding").hidden = false;
    mobApi(BOOK.book, {
      vehicle_type_id: state.chosen.vehicle_type_id, pickup: state.pickup, drop: state.drop,
      receiver_name: $("rcv-name").value, receiver_phone: phone, notes: $("note").value
    }).then(function (d) { window.location = d.url; })
      .catch(function (e) { $("finding").hidden = true; mobToast(e.message); });
  });

  show("sheet-where");
  locate(true);
})();
