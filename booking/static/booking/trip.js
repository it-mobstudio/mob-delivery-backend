/* Live tracking: one poll every few seconds redraws the status, the driver's
   card and their position (glided, not jumped, between polls). */
(function () {
  "use strict";
  var M = window.MOB, $ = M.$;
  var s = JSON.parse($("state").textContent), map, driverMark, route, lastStatus = s.status, timer;
  var PROGRESS = { requested: 0, no_driver_available: 0, assigned: 1, arrived_at_pickup: 2, in_progress: 3, completed: 4 };
  var REASONS = ["Booked by mistake", "Driver is taking too long", "Need a different vehicle", "Changed my plans", "Found another way to send it", "Other"];

  function eta(from, to) {
    var km = M.haversine(from, to);
    return { km: km, min: Math.max(2, Math.round(km / 22 * 60) + 1) };
  }

  function hero(d) {
    var p = PROGRESS[d.status], bar = "";
    if (p != null && d.status !== "completed") {
      bar = '<div class="progress">' + [1, 2, 3, 4].map(function (i) { return "<i class=\"" + (i <= p ? "done" : i === p + 1 ? "now" : "") + '"></i>'; }).join("") + "</div>" +
        '<div class="row tiny muted b" style="justify-content:space-between"><span>Assigned</span><span>At pickup</span><span>Picked up</span><span>Delivered</span></div>';
    }
    var dl = d.driver && d.driver.lat != null ? [d.driver.lat, d.driver.lng] : null;
    switch (d.status) {
      case "requested":
      case "no_driver_available":
        return '<div class="row" style="gap:16px"><div class="radar" style="width:84px;height:84px;margin:0;flex:none"><span></span><span></span><div class="core" style="width:56px;height:56px;box-shadow:0 0 0 5px var(--brand-soft)"><img src="' + d.image + '" alt="" style="width:40px"></div></div>' +
          '<div class="grow"><h1>' + (d.status === "requested" ? "Finding your driver" : "Still looking for a driver") + "</h1>" +
          '<p class="small muted" style="margin-top:4px">' + (d.status === "requested" ? "Matching you with the nearest " + M.esc(d.vehicle_type) + ". This usually takes under a minute." : "Drivers nearby are busy right now. We'll keep trying — or cancel and try another vehicle.") + "</p></div></div>" + bar;
      case "assigned":
        var e = dl ? eta(dl, [d.pickup.lat, d.pickup.lng]) : null;
        return '<span class="pill brand"><span class="live-dot"></span>Driver on the way</span><h1 style="margin-top:10px">' +
          (e ? "Arriving in " + e.min + " min" : "Driver is on the way") + "</h1>" +
          '<p class="small muted" style="margin-top:4px">' + (e ? e.km.toFixed(1) + " km from your pickup" : "Heading to your pickup") + "</p>" + bar;
      case "arrived_at_pickup":
        return '<span class="pill amber"><span class="live-dot"></span>At pickup</span><h1 style="margin-top:10px">Your driver has arrived</h1>' +
          '<p class="small muted" style="margin-top:4px">Hand over your goods. The driver photographs them before leaving.</p>' + bar;
      case "in_progress":
        var e2 = dl ? eta(dl, [d.drop.lat, d.drop.lng]) : null;
        return '<span class="pill blue"><span class="live-dot"></span>On the way to drop</span><h1 style="margin-top:10px">' +
          (e2 ? "Delivering in " + e2.min + " min" : "Your goods are on the move") + "</h1>" +
          '<p class="small muted" style="margin-top:4px">' + (e2 ? e2.km.toFixed(1) + " km to go" : "Heading to the drop") + "</p>" + bar;
      case "completed":
        var at = d.steps[4].at ? new Date(d.steps[4].at).toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "";
        return '<div class="done-hero"><svg viewBox="0 0 64 64" fill="none"><circle cx="32" cy="32" r="30" stroke="#0c831f" stroke-width="4"/><path d="M20 33l8 8 16-17" stroke="#0c831f" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
          '<h1 style="margin-top:10px">Delivered!</h1><p class="small muted" style="margin-top:4px">' + (at ? "Your goods reached the drop on " + at : "Your goods reached the drop") + "</p></div>";
      case "cancelled":
        var c = d.cancelled || {};
        return '<div class="row"><span class="icon-btn flat" style="background:var(--red-soft);color:var(--red)"><i data-lucide="circle-x"></i></span><div class="grow"><h1>Booking cancelled</h1>' +
          '<p class="small muted" style="margin-top:2px">' + (c.by === "customer" ? "You cancelled" : "Cancelled") + (c.reason ? " · " + M.esc(c.reason) : "") + "</p></div></div>";
      default:
        return "<h1>" + M.esc(d.status_label) + "</h1>" + bar;
    }
  }

  function render(d) {
    s = d;
    $("hero").innerHTML = hero(d);

    // OTP to read out at the drop
    $("otp").hidden = !d.otp;
    if (d.otp) {
      $("otp").innerHTML = '<div class="otp-card fade-up"><div><b>Delivery OTP</b><div class="small" style="opacity:.75">Share it with the driver only when your goods arrive</div></div>' +
        '<div class="digits">' + String(d.otp).split("").map(function (x) { return "<b>" + x + "</b>"; }).join("") + "</div></div>";
    }

    // driver
    $("driver").hidden = !d.driver;
    if (d.driver) {
      var dr = d.driver;
      $("driver").innerHTML = '<span class="avatar">' + (dr.photo ? '<img src="' + M.esc(dr.photo) + '" alt="">' : M.initials(dr.name)) + "</span>" +
        '<div class="grow"><b style="font-size:16px">' + M.esc(dr.name) + '</b><div style="margin-top:6px" class="row"><span class="plate">' + M.esc(dr.vehicle || "—") + "</span>" +
        '<span class="small muted">' + M.esc(d.vehicle_type) + "</span></div></div>" +
        '<img src="' + d.image + '" alt="" style="width:58px">' +
        (dr.phone ? '<a class="icon-btn brand" href="tel:' + M.esc(dr.phone) + '" aria-label="Call driver"><i data-lucide="phone"></i></a>' : "");
      if (dr.lat != null) placeDriver([dr.lat, dr.lng], d.image);
      else if (driverMark) { driverMark.remove(); driverMark = null; }
    } else if (driverMark) { driverMark.remove(); driverMark = null; }

    $("a-pickup").textContent = d.pickup.address;
    $("a-drop").textContent = d.drop.address;
    $("a-rcv").textContent = d.drop.name ? "Receiver: " + d.drop.name + (d.drop.phone ? " · " + d.drop.phone : "") : "";
    $("a-notes").hidden = !d.notes; $("a-notes").textContent = d.notes || "";

    var current = d.steps.findIndex(function (x) { return !x.at; });
    $("steps").innerHTML = d.steps.map(function (x, i) {
      var cls = x.at ? "done" : (i === current && d.status !== "cancelled" ? "now" : "");
      var when = x.at ? new Date(x.at).toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : cls === "now" ? "In progress" : "";
      return '<li class="' + cls + '"><i></i>' + M.esc(x.label) + (when ? "<small>" + when + "</small>" : "") + "</li>";
    }).join("");

    var b = d.breakdown;
    $("pay-pill").innerHTML = d.paid ? '<span class="pill green"><i data-lucide="check" style="width:13px;height:13px"></i>Paid</span>'
      : d.status === "cancelled" ? '<span class="pill">No charge</span>' : '<span class="pill amber">Pay on delivery</span>';
    $("fare").innerHTML =
      line("Base fare", b.base) + line("Distance" + (d.distance_m ? " · " + M.km(d.distance_m) : ""), b.distance) + line("Time" + (d.duration_s ? " · " + M.mins(d.duration_s) : ""), b.time) +
      '<div class="fare-line total"><span>' + (d.paid ? "Total paid" : "Total") + '</span><span>' + M.money(d.fare) + "</span></div>" +
      (d.paid || d.status === "cancelled" ? "" : '<p class="small muted" style="margin-top:6px">Pay the driver by cash or UPI when your goods are delivered.</p>');

    $("photos-card").hidden = !d.photos.length;
    $("photos").innerHTML = d.photos.map(function (u) { return '<a href="' + M.esc(u) + '" target="_blank" rel="noopener"><img src="' + M.esc(u) + '" alt="Proof photo" loading="lazy"></a>'; }).join("");
    $("cancel").hidden = !d.can_cancel;
    $("again").hidden = !(d.status === "completed" || d.status === "cancelled");
    M.icons();

    if (d.status !== lastStatus) {
      var said = { assigned: "A driver has accepted your booking", arrived_at_pickup: "Your driver has arrived at the pickup", in_progress: "Your goods have been picked up", completed: "Delivered! Thanks for booking with MOB" }[d.status];
      if (said) { M.toast(said, "bell-ring"); if (navigator.vibrate) navigator.vibrate(80); }
      lastStatus = d.status;
    }
  }

  function line(label, amount) {
    return '<div class="fare-line"><span class="muted">' + M.esc(label) + "</span><span>₹" + (amount || 0).toFixed(2) + "</span></div>";
  }

  function placeDriver(ll, image) {
    if (!map) return;
    if (!driverMark) { driverMark = map.marker(ll, '<div class="mk-veh mk-driver"><img src="' + image + '" alt=""></div>', { z: 10 }); fitAll(); }
    else driverMark.move(ll, 2500);
  }

  function pad() { return window.innerWidth >= 900 ? { top: 90, right: 90, bottom: 90, left: 90 } : { top: 80, right: 50, bottom: 40, left: 50 }; }
  function fitAll() {
    var pts = [[s.pickup.lat, s.pickup.lng], [s.drop.lat, s.drop.lng]];
    if (s.driver && s.driver.lat != null && s.status !== "completed") pts.push([s.driver.lat, s.driver.lng]);
    map.fit(pts, pad());
  }

  M.map($("map"), { center: [s.pickup.lat, s.pickup.lng], zoom: 14 }).then(function (m) {
    map = m;
    if (s.route.length > 1) route = map.route(s.route);
    map.marker([s.pickup.lat, s.pickup.lng], M.mk.stop("p", "Pickup", s.pickup.address.split(",")[0]), { z: 5 });
    map.marker([s.drop.lat, s.drop.lng], M.mk.stop("d", "Drop", s.drop.address.split(",")[0]), { z: 5 });
    if (s.driver && s.driver.lat != null) placeDriver([s.driver.lat, s.driver.lng], s.image);
    fitAll();
  });

  render(s);
  function poll() {
    if (s.status === "completed" || s.status === "cancelled") return;
    M.api(TRIP.url).then(render).catch(function () {}).finally(function () { timer = setTimeout(poll, document.hidden ? 15000 : 4000); });
  }
  timer = setTimeout(poll, 4000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { clearTimeout(timer); poll(); } });

  // cancel with a reason
  $("cancel").onclick = function () {
    var chosen = REASONS[0];
    var sh = M.sheet('<h2>Cancel this booking?</h2><p class="small muted" style="margin:6px 0 10px">Tell us why — it helps us get better.</p>' +
      '<div id="reasons">' + REASONS.map(function (r, i) { return '<button class="reason' + (i ? "" : " on") + '" data-r="' + i + '"><span class="radio"></span>' + r + "</button>"; }).join("") + "</div>" +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:18px"><button class="btn" id="keep">Keep booking</button><button class="btn btn-dark" id="do-cancel" style="background:var(--red);border-color:var(--red)">Cancel</button></div>');
    sh.el.querySelectorAll("[data-r]").forEach(function (b) {
      b.onclick = function () { sh.el.querySelectorAll(".reason").forEach(function (x) { x.classList.remove("on"); }); b.classList.add("on"); chosen = REASONS[+b.dataset.r]; };
    });
    sh.el.querySelector("#keep").onclick = sh.close;
    sh.el.querySelector("#do-cancel").onclick = function () {
      var btn = this; M.busy(btn, true);
      M.api(TRIP.cancel, { reason: chosen }).then(function (d) { sh.close(); render(d); M.toast("Booking cancelled", "circle-check"); })
        .catch(function (e) { M.busy(btn, false); M.toast(e.message); });
    };
  };

  function copy(text, msg) {
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () { M.toast(msg, "copy"); }, function () { M.toast(text); });
  }
  $("copy-no").onclick = function () { copy(s.number, "Order number copied"); };
  $("share").onclick = function () {
    var text = "MOB booking " + s.number + " — " + s.status_label + "\nFrom: " + s.pickup.address + "\nTo: " + s.drop.address +
      (s.driver ? "\nDriver: " + s.driver.name + (s.driver.vehicle ? " (" + s.driver.vehicle + ")" : "") : "");
    if (navigator.share) navigator.share({ title: "MOB booking " + s.number, text: text }).catch(function () {});
    else copy(text, "Trip details copied");
  };
})();
