/* MOB operations console — small, dependency-free behaviours. */
(function () {
  "use strict";

  function icons() { if (window.lucide) window.lucide.createIcons(); }

  // Rows that open a page when clicked (but not when a link/button inside is).
  document.addEventListener("click", function (e) {
    var row = e.target.closest("[data-href]");
    if (!row || e.target.closest("a, button, input, select, label, form")) return;
    if (e.metaKey || e.ctrlKey) window.open(row.dataset.href, "_blank");
    else window.location = row.dataset.href;
  });

  // Filters apply as soon as they change.
  document.addEventListener("change", function (e) {
    if (e.target.matches("[data-autosubmit]")) e.target.form.submit();
  });

  // "/" jumps to the global search, like most consoles.
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) {
      var box = document.getElementById("global-search");
      if (box) { e.preventDefault(); box.focus(); }
    }
    if (e.key === "Escape") closeAll();
  });

  // -- modals: <button data-open="id">, <div class="modal" id="id">, [data-close]
  function closeAll() {
    document.querySelectorAll(".modal.open, .lightbox.open").forEach(function (m) { m.classList.remove("open"); });
    document.body.style.overflow = "";
  }
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-open]");
    if (opener) {
      var modal = document.getElementById(opener.dataset.open);
      if (modal) {
        // A single modal can serve many rows: copy data-set-* onto its inputs.
        Object.keys(opener.dataset).forEach(function (k) {
          if (k.indexOf("set") !== 0 || k === "set") return;
          var name = k.slice(3).replace(/^./, function (c) { return c.toLowerCase(); });
          modal.querySelectorAll('[data-fill="' + name + '"]').forEach(function (el) {
            if ("value" in el && el.tagName !== "SPAN" && el.tagName !== "B") el.value = opener.dataset[k];
            else el.textContent = opener.dataset[k];
          });
        });
        modal.classList.add("open");
        document.body.style.overflow = "hidden";
        var first = modal.querySelector("input:not([type=hidden]), select, textarea");
        if (first) setTimeout(function () { first.focus(); }, 50);
      }
    }
    if (e.target.closest("[data-close]") || e.target.classList.contains("modal")) closeAll();
  });

  // Destructive forms ask first.
  document.addEventListener("submit", function (e) {
    var msg = e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // -- lightbox: any [data-lightbox] link inside a [data-gallery] group
  var lb, lbImg, lbMeta, group = [], index = 0;
  function buildLightbox() {
    lb = document.createElement("div");
    lb.className = "lightbox";
    lb.innerHTML =
      '<button class="x" aria-label="Close" data-close><i data-lucide="x"></i></button>' +
      '<button class="nav-btn prev" aria-label="Previous"><i data-lucide="chevron-left"></i></button>' +
      '<img alt="">' +
      '<button class="nav-btn next" aria-label="Next"><i data-lucide="chevron-right"></i></button>' +
      '<div class="meta"><span class="cap"></span><a class="open" target="_blank" rel="noopener">Open original ↗</a></div>';
    document.body.appendChild(lb);
    lbImg = lb.querySelector("img");
    lbMeta = lb.querySelector(".meta");
    lb.querySelector(".prev").onclick = function (e) { e.stopPropagation(); show(index - 1); };
    lb.querySelector(".next").onclick = function (e) { e.stopPropagation(); show(index + 1); };
    lb.addEventListener("click", function (e) { if (e.target === lb) closeAll(); });
    icons();
  }
  function show(i) {
    if (!group.length) return;
    index = (i + group.length) % group.length;
    var a = group[index];
    lbImg.src = a.getAttribute("href");
    lbMeta.querySelector(".cap").innerHTML = a.dataset.caption || "";
    lbMeta.querySelector(".open").href = a.getAttribute("href");
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest("[data-lightbox]");
    if (!a) return;
    e.preventDefault();
    if (!lb) buildLightbox();
    var scope = a.closest("[data-gallery]") || document;
    group = Array.prototype.slice.call(scope.querySelectorAll("[data-lightbox]"));
    lb.classList.add("open");
    document.body.style.overflow = "hidden";
    show(group.indexOf(a));
  });
  document.addEventListener("keydown", function (e) {
    if (!lb || !lb.classList.contains("open")) return;
    if (e.key === "ArrowRight") show(index + 1);
    if (e.key === "ArrowLeft") show(index - 1);
  });

  // Toasts from Django messages fade out on their own.
  function toasts() {
    document.querySelectorAll(".toast").forEach(function (t, i) {
      setTimeout(function () { t.style.transition = "opacity .4s"; t.style.opacity = "0"; }, 4200 + i * 400);
      setTimeout(function () { t.remove(); }, 4700 + i * 400);
    });
  }

  // Collapsible sidebar, remembered per browser; labels become hover tips.
  function navToggle() {
    var btn = document.getElementById("nav-toggle");
    document.querySelectorAll(".nav a").forEach(function (a) {
      var lbl = a.querySelector(".lbl"); if (lbl) a.dataset.tip = lbl.textContent.trim();
    });
    if (!btn) return;
    btn.addEventListener("click", function () {
      var collapsed = document.body.classList.toggle("nav-collapsed");
      try { localStorage.setItem("console-nav", collapsed ? "collapsed" : "open"); } catch (e) {}
      window.dispatchEvent(new Event("resize"));  // let maps/charts refit
    });
  }

  document.addEventListener("DOMContentLoaded", function () { icons(); toasts(); navToggle(); });
  window.consoleIcons = icons;
})();
