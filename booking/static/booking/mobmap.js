/* One small map API over Google Maps (when the server has a key) or Leaflet
   (otherwise, or if Google refuses the key). Markers are plain HTML so they
   can be styled and animated with CSS.

     MOB.map(el, {center: [lat, lng], zoom}) → Promise<map>
     map.view(ll, zoom) · map.fit(points, {top, right, bottom, left}) · map.center()
     map.on("movestart" | "idle", fn)
     map.marker(ll, html, {anchor: "center" | "bottom", z}) → {move(ll, ms), remove(), el}
     map.route(points) → {remove()}   (cased line with a travelling dot)
*/
(function () {
  "use strict";
  var M = window.MOB;
  var NO_GOOGLE = "mob.nogoogle";
  var STYLE = [
    { elementType: "geometry", stylers: [{ color: "#f5f5f3" }] },
    { elementType: "labels.icon", stylers: [{ saturation: -100 }, { lightness: 25 }] },
    { elementType: "labels.text.fill", stylers: [{ color: "#5f6368" }] },
    { elementType: "labels.text.stroke", stylers: [{ color: "#f5f5f3" }] },
    { featureType: "poi.business", stylers: [{ visibility: "off" }] },
    { featureType: "poi", elementType: "geometry", stylers: [{ color: "#eceeea" }] },
    { featureType: "poi.park", elementType: "geometry", stylers: [{ color: "#dcefd9" }] },
    { featureType: "poi.park", elementType: "labels.text", stylers: [{ visibility: "off" }] },
    { featureType: "road", elementType: "geometry", stylers: [{ color: "#ffffff" }] },
    { featureType: "road", elementType: "geometry.stroke", stylers: [{ color: "#e6e6e3" }] },
    { featureType: "road.arterial", elementType: "labels.text.fill", stylers: [{ color: "#7b7f86" }] },
    { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#dde8f5" }] },
    { featureType: "road.highway", elementType: "geometry.stroke", stylers: [{ color: "#c3d6ec" }] },
    { featureType: "road.local", elementType: "labels.text.fill", stylers: [{ color: "#9aa0a6" }] },
    { featureType: "transit", stylers: [{ visibility: "off" }] },
    { featureType: "water", elementType: "geometry", stylers: [{ color: "#cfe6f5" }] },
    { featureType: "water", elementType: "labels.text.fill", stylers: [{ color: "#8ab4cf" }] },
    { featureType: "administrative.land_parcel", stylers: [{ visibility: "off" }] }
  ];

  var refused = false;  // Google refused the key on this page
  var live = [];        // every map on this page, so a refusal can swap them to Leaflet in place

  function useGoogle() {
    var off = refused;
    try { off = off || sessionStorage.getItem(NO_GOOGLE) === "1"; } catch (e) { /* ignore */ }
    return !!M.mapKey && !off;
  }

  var loading = {};
  function script(src) {
    if (!loading[src]) {
      loading[src] = new Promise(function (resolve, reject) {
        var s = document.createElement("script"), timer;
        function fail() {
          clearTimeout(timer); s.remove(); delete loading[src];
          reject(new Error("The map couldn't load. Check your connection and try again."));
        }
        s.src = src; s.async = true;
        s.onload = function () { clearTimeout(timer); resolve(); }; s.onerror = fail;
        timer = setTimeout(fail, 12000);
        document.head.appendChild(s);
      });
    }
    return loading[src];
  }

  M.loadMaps = function () {
    if (useGoogle()) {
      if (window.google && window.google.maps && window.google.maps.Map) return Promise.resolve("google");
      if (!loading.google) {
        loading.google = new Promise(function (resolve, reject) {
          var timer = setTimeout(function () { reject(new Error("Map loading timed out.")); }, 12000);
          window.__mobMapsReady = function () { clearTimeout(timer); resolve("google"); };
          // Google calls this if it refuses the key (billing, referrer…): fall back for this tab.
          // Never reload for it: an app's WebView often has no sessionStorage, so the
          // flag would not survive and the page would reload forever.
          window.gm_authFailure = function () {
            clearTimeout(timer);
            refused = true;
            try { sessionStorage.setItem(NO_GOOGLE, "1"); } catch (e) { /* ignore */ }
            resolve(loadLeaflet());
            live.forEach(function (m) { m.toLeaflet(); });
          };
          script("https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(M.mapKey) +
            "&v=weekly&loading=async&callback=__mobMapsReady&region=IN&language=en").catch(function (e) { clearTimeout(timer); reject(e); });
        }).catch(function () {
          refused = true;
          try { sessionStorage.setItem(NO_GOOGLE, "1"); } catch (e) { /* ignore */ }
          return loadLeaflet();
        });
      }
      return loading.google;
    }
    return loadLeaflet();
  };

  function loadLeaflet() {
    if (!document.querySelector("link[data-leaflet]")) {
      var l = document.createElement("link"); l.rel = "stylesheet"; l.dataset.leaflet = "1";
      l.href = "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"; document.head.appendChild(l);
    }
    return script("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js").then(function () { return "leaflet"; });
  }

  function lerp(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]; }
  function ease(t) { return t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function animate(from, to, ms, step) {
    var start = performance.now(), id;
    function tick(now) {
      var t = Math.min(1, (now - start) / ms);
      step(lerp(from, to, ease(t)));
      if (t < 1) id = requestAnimationFrame(tick);
    }
    id = requestAnimationFrame(tick);
    return function () { cancelAnimationFrame(id); };
  }

  M.map = function (el, opts) {
    opts = opts || {};
    var center = opts.center || [26.8467, 80.9462], zoom = opts.zoom || 13;
    el.classList.add("map-loading");
    return M.loadMaps().then(function (kind) {
      var map = swappable(el, kind, center, zoom);
      setTimeout(function () { el.classList.remove("map-loading"); }, kind === "google" ? 500 : 250);
      return map;
    }).catch(function (e) {
      el.classList.remove("map-loading");
      throw e;
    });
  };

  /* The map the pages hold. It remembers its listeners, markers and routes so
     that, if Google refuses the key after the map is up, it can move them all
     onto a Leaflet map in the same spot without the page noticing. */
  function swappable(el, kind, center, zoom) {
    var impl = kind === "google" ? googleMap(el, center, zoom) : leafletMap(el, center, zoom);
    var listeners = [], markers = [], routes = [];
    var api = {
      view: function (ll, z) { impl.view(ll, z); },
      fit: function (points, pad) { impl.fit(points, pad); },
      center: function () { return impl.center(); },
      on: function (ev, fn) { listeners.push([ev, fn]); impl.on(ev, fn); },
      marker: function (ll, html, o) {
        var rec = { ll: ll, html: html, o: o, m: impl.marker(ll, html, o) };
        markers.push(rec);
        return {
          get el() { return rec.m.el; },
          move: function (to, ms) { rec.ll = to; rec.m.move(to, ms); },
          remove: function () { rec.m.remove(); markers.splice(markers.indexOf(rec), 1); }
        };
      },
      route: function (points) {
        var rec = { points: points, r: impl.route(points) };
        routes.push(rec);
        return { remove: function () { rec.r.remove(); routes.splice(routes.indexOf(rec), 1); } };
      },
      toLeaflet: function () {
        if (impl.kind !== "google") return;
        var c = impl.center(), z = impl.zoom();
        routes.forEach(function (r) { r.r.remove(); });
        markers.forEach(function (m) { m.m.remove(); });
        impl.destroy();
        // A clean element: Google leaves its own styles and error box on the old one.
        var fresh = el.cloneNode(false); fresh.removeAttribute("style"); fresh.classList.add("map-loading");
        el.replaceWith(fresh); el = fresh;
        // Stand-in while Leaflet loads: remembers where to look, the rest is replayed.
        var stub = { el: null, move: function () {}, remove: function () {} }, fitTo = null;
        impl = {
          kind: "switching",
          view: function (ll, zz) { c = ll; if (zz) z = zz; fitTo = null; },
          fit: function (points, pad) { fitTo = [points, pad]; },
          center: function () { return c; }, zoom: function () { return z; },
          on: function () {}, marker: function () { return stub; }, route: function () { return stub; }
        };
        loadLeaflet().then(function () {
          impl = leafletMap(el, c, z);
          listeners.forEach(function (l) { impl.on(l[0], l[1]); });
          markers.forEach(function (m) { m.m = impl.marker(m.ll, m.html, m.o); });
          routes.forEach(function (r) { r.r = impl.route(r.points); });
          if (fitTo) impl.fit(fitTo[0], fitTo[1]);
          setTimeout(function () { el.classList.remove("map-loading"); }, 250);
        }).catch(function () {
          el.classList.remove("map-loading");
          el.textContent = "The map couldn't load. Check your connection and reload to retry.";
        });
      }
    };
    if (impl.kind === "google") live.push(api);
    return api;
  }

  /* ---------------------------------------------------------------- Google */
  function googleMap(el, center, zoom) {
    var g = window.google.maps;
    var map = new g.Map(el, {
      center: { lat: center[0], lng: center[1] }, zoom: zoom, disableDefaultUI: true, gestureHandling: "greedy",
      clickableIcons: false, keyboardShortcuts: false, styles: STYLE, backgroundColor: "#f5f5f3"
    });

    function Html(ll, html, o) {
      this.ll = ll; this.o = o || {};
      this.div = document.createElement("div");
      this.div.style.cssText = "position:absolute;will-change:transform;z-index:" + (this.o.z || 1);
      this.div.innerHTML = html;
      this.setMap(map);
    }
    Html.prototype = new g.OverlayView();
    Html.prototype.onAdd = function () { this.getPanes().overlayMouseTarget.appendChild(this.div); };
    Html.prototype.draw = function () {
      var proj = this.getProjection(); if (!proj) return;
      var p = proj.fromLatLngToDivPixel(new g.LatLng(this.ll[0], this.ll[1]));
      this.div.style.left = p.x + "px"; this.div.style.top = p.y + "px";
      this.div.style.transform = this.o.anchor === "bottom" ? "translate(-50%,-100%)" : "translate(-50%,-50%)";
    };
    Html.prototype.onRemove = function () { this.div.remove(); };

    var api = {
      kind: "google", raw: map,
      view: function (ll, z) { map.panTo({ lat: ll[0], lng: ll[1] }); if (z) map.setZoom(z); },
      fit: function (points, pad) {
        if (!points.length) return;
        if (points.length === 1) return api.view(points[0], 15);
        var b = new g.LatLngBounds(); points.forEach(function (p) { b.extend({ lat: p[0], lng: p[1] }); });
        map.fitBounds(b, pad || 60);
      },
      center: function () { var c = map.getCenter(); return [c.lat(), c.lng()]; },
      zoom: function () { return map.getZoom(); },
      destroy: function () { g.event.clearInstanceListeners(map); },
      on: function (ev, fn) {
        if (ev === "idle") map.addListener("idle", fn);
        else if (ev === "movestart") { map.addListener("dragstart", fn); map.addListener("zoom_changed", fn); }
      },
      marker: function (ll, html, o) {
        var m = new Html(ll, html, o), stop;
        return {
          el: m.div,
          move: function (to, ms) {
            if (stop) stop();
            if (!ms) { m.ll = to; m.draw(); return; }
            stop = animate(m.ll, to, ms, function (p) { m.ll = p; m.draw(); });
          },
          remove: function () { if (stop) stop(); m.setMap(null); }
        };
      },
      route: function (points) {
        var path = points.map(function (p) { return { lat: p[0], lng: p[1] }; });
        var casing = new g.Polyline({ path: path, map: map, strokeColor: "#ffffff", strokeWeight: 9, strokeOpacity: 1, zIndex: 1 });
        var line = new g.Polyline({
          path: path, map: map, strokeColor: "#0454a3", strokeWeight: 5, strokeOpacity: 1, zIndex: 2,
          icons: [{ icon: { path: g.SymbolPath.CIRCLE, scale: 4.5, fillColor: "#ffffff", fillOpacity: 1, strokeColor: "#001533", strokeWeight: 2.5 }, offset: "0%" }]
        });
        var off = 0, timer = setInterval(function () {
          off = (off + 0.6) % 100; var icons = line.get("icons"); icons[0].offset = off + "%"; line.set("icons", icons);
        }, 40);
        return { remove: function () { clearInterval(timer); casing.setMap(null); line.setMap(null); } };
      }
    };
    return api;
  }

  /* --------------------------------------------------------------- Leaflet */
  function leafletMap(el, center, zoom) {
    var L = window.L;
    var map = L.map(el, { zoomControl: false, attributionControl: true }).setView(center, zoom);
    map.attributionControl.setPrefix(false).setPosition("topright");
    L.tileLayer(M.tiles.url, { maxZoom: 19, attribution: M.tiles.attribution }).addTo(map);

    return {
      kind: "leaflet", raw: map,
      view: function (ll, z) { map.setView(ll, z || map.getZoom(), { animate: true }); },
      fit: function (points, pad) {
        if (!points.length) return;
        if (points.length === 1) return map.setView(points[0], 15);
        pad = pad || { top: 60, right: 60, bottom: 60, left: 60 };
        map.fitBounds(points, { paddingTopLeft: [pad.left, pad.top], paddingBottomRight: [pad.right, pad.bottom], animate: true });
      },
      center: function () { var c = map.getCenter(); return [c.lat, c.lng]; },
      zoom: function () { return map.getZoom(); },
      on: function (ev, fn) { map.on(ev === "idle" ? "moveend" : "movestart", fn); },
      marker: function (ll, html, o) {
        o = o || {};
        var icon = L.divIcon({ className: "", html: '<div style="transform:translate(-50%,' + (o.anchor === "bottom" ? "-100%" : "-50%") + ')">' + html + "</div>", iconSize: [0, 0] });
        var m = L.marker(ll, { icon: icon, interactive: false, zIndexOffset: (o.z || 1) * 100 }).addTo(map), stop, cur = ll;
        return {
          el: m.getElement(),
          move: function (to, ms) {
            if (stop) stop();
            if (!ms) { cur = to; m.setLatLng(to); return; }
            stop = animate(cur, to, ms, function (p) { cur = p; m.setLatLng(p); });
          },
          remove: function () { if (stop) stop(); map.removeLayer(m); }
        };
      },
      route: function (points) {
        var group = L.layerGroup([
          L.polyline(points, { color: "#fff", weight: 9, opacity: 1 }),
          L.polyline(points, { color: "#0454a3", weight: 5, opacity: 1 }),
          L.polyline(points, { color: "#9cc4f0", weight: 3, opacity: 1, className: "route-anim" })
        ]).addTo(map);
        return { remove: function () { map.removeLayer(group); } };
      }
    };
  }

  /* Marker bodies shared by every map screen. */
  M.mk = {
    stop: function (kind, title, sub) {
      var icon = kind === "d"
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 5v14M5 12l7 7 7-7"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
      return '<div class="mk">' + (title ? '<div class="mk-label">' + M.esc(title) + (sub ? "<small>" + M.esc(sub) + "</small>" : "") + "</div>" : "") +
        '<div class="mk-stop ' + (kind === "d" ? "d" : "") + '">' + icon + "</div></div>";
    },
    vehicle: function (image, cls) { return '<div class="mk-veh ' + (cls || "") + '"><img src="' + image + '" alt=""></div>'; },
    me: function () { return '<div class="mk-me"></div>'; }
  };
})();
