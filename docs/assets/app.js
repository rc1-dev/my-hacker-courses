/* HN Course Picks static front end, ported from my-hackertube. No framework; every
 * page calls one of the page functions at the bottom based on <body data-page="...">.
 * Data text is only ever inserted with textContent (see el()). Likes and history live
 * in this browser's localStorage only. */
(function () {
  "use strict";

  var PAGE_SIZE = 60;
  var params = new URLSearchParams(location.search);
  var ACCESS = { free: "Free", audit: "Free to audit", paid: "Paid" };
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // ---------- helpers ----------
  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      var v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "text") n.textContent = v;
      else if (k === "class") n.className = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    (children || []).forEach(function (c) {
      if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }
  function $(sel) { return document.querySelector(sel); }
  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + ": HTTP " + r.status);
      return r.json();
    });
  }
  function safeURL(u) { return /^https?:\/\//i.test(u || "") ? u : null; }
  function plural(n, one, many) { return n.toLocaleString() + " " + (n === 1 ? one : many); }
  function years(c) { return c.first_year === c.last_year ? String(c.first_year) : c.first_year + "–" + c.last_year; }
  function monthName(m) { return MONTHS[+m.slice(5, 7) - 1] + " " + m.slice(0, 4); }
  function courseHref(c) { return "course.html?c=" + encodeURIComponent(c.id); }
  function isReading(c) { return c.format === "Reading list"; }

  var dataPromise = null;
  function loadCourses() {
    if (!dataPromise) {
      dataPromise = Promise.all([getJSON("data/courses.json"), getJSON("data/taxonomy.json")])
        .then(function (res) {
          var courses = res[0].courses;
          var byId = {};
          var cats = {}, areaOfCat = {};
          res[1].areas.forEach(function (a) {
            a.categories.forEach(function (c) { cats[c.id] = c; areaOfCat[c.id] = a; });
          });
          courses.forEach(function (c, i) {
            c.i = i;
            byId[c.id] = c;
            c.hay = [c.title, c.provider, c.key, c.summary, c.quote, c.page_title,
              c.cats.map(function (k) { return cats[k] ? cats[k].name : ""; }).join(" ")]
              .join(" ").toLowerCase();
          });
          return { courses: courses, byId: byId, meta: res[0].meta, taxonomy: res[1], cats: cats, areaOfCat: areaOfCat };
        });
    }
    return dataPromise;
  }

  // ---------- likes + history (localStorage, per browser) ----------
  var HISTORY_MAX = 100;
  function readStore(key) {
    try { var v = JSON.parse(localStorage.getItem(key) || "[]"); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }
  function writeStore(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  function likes() { return readStore("hc.likes"); }                     // [id], newest first
  function history() { return readStore("hc.history"); }                 // [{id, t}], newest first
  function isLiked(id) { return likes().indexOf(id) !== -1; }
  function toggleLike(id) {
    var l = likes(), i = l.indexOf(id);
    if (i === -1) l.unshift(id); else l.splice(i, 1);
    writeStore("hc.likes", l);
    syncLikeButtons(id);
    return i === -1;
  }
  function recordVisit(id) {
    var h = history().filter(function (x) { return x.id !== id; });
    h.unshift({ id: id, t: Date.now() });
    writeStore("hc.history", h.slice(0, HISTORY_MAX));
  }
  function syncLikeButtons(id) {
    var on = isLiked(id);
    Array.prototype.forEach.call(document.querySelectorAll('[data-like="' + CSS.escape(id) + '"]'), function (b) {
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.setAttribute("aria-label", on ? "Unlike" : "Like");
      if (b.classList.contains("like")) b.textContent = on ? "♥" : "♡";
      else b.textContent = on ? "♥ Liked" : "♡ Like";
    });
  }
  function likeButton(c, big) {
    var b = el("button", { type: "button", class: big ? "btn" : "like", "data-like": c.id, title: "Like (l)" });
    b.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); toggleLike(c.id); });
    var on = isLiked(c.id);
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.setAttribute("aria-label", on ? "Unlike" : "Like");
    b.textContent = big ? (on ? "♥ Liked" : "♡ Like") : (on ? "♥" : "♡");
    return b;
  }

  // For You: courses near the ones you liked (weight 1) and recently opened (0.5, decaying),
  // using the precomputed embedding neighbours in courses.json (`nb`).
  function forYou(d, n) {
    var seeds = [];
    likes().forEach(function (id) { if (d.byId[id]) seeds.push([d.byId[id], 1]); });
    history().slice(0, 30).forEach(function (h, k) { if (d.byId[h.id]) seeds.push([d.byId[h.id], 0.5 * Math.pow(0.9, k)]); });
    if (!seeds.length) return [];
    var skip = {}, score = {}, because = {};
    seeds.forEach(function (s) { skip[s[0].i] = 1; });
    seeds.forEach(function (s) {
      s[0].nb.forEach(function (p) {
        var j = p[0], w = s[1] * p[1];
        if (skip[j]) return;
        score[j] = (score[j] || 0) + w;
        if (!because[j] || because[j][1] < w) because[j] = [s[0], w];
      });
    });
    var perProvider = {};   // at most 3 per provider, so one like doesn't fill the shelf with Khan Academy
    return Object.keys(score).map(Number)
      .filter(function (j) { return !isReading(d.courses[j]); })
      .sort(function (a, b) { return score[b] - score[a] || a - b; })
      .filter(function (j) {
        var p = d.courses[j].provider || "?";
        perProvider[p] = (perProvider[p] || 0) + 1;
        return perProvider[p] <= 3;
      })
      .slice(0, n).map(function (j) { return { c: d.courses[j], because: because[j][0] }; });
  }

  // ---------- cards (text only) ----------
  function tags(c) {
    var t = [];
    if (c.free) t.push(el("span", {
      class: "tag" + (c.free === "free" ? " free" : ""), text: ACCESS[c.free] || c.free,
      title: c.free_src === "provider" ? "Inferred from the provider; the page didn't say" : "From the course page",
    }));
    if (c.video) t.push(el("span", { class: "tag", text: "Video lectures" }));
    if (c.has_assignments) t.push(el("span", { class: "tag", text: "Assignments" }));
    if (c.status === "dead") t.push(el("span", { class: "tag gone", text: c.wayback_url ? "Archived" : "Page gone" }));
    return t.length ? el("div", { class: "tags" }, t) : null;
  }
  function kicker(c) {
    var p = c.provider || "Unknown provider";
    return p.indexOf(c.format) !== -1 ? p : p + " · " + c.format;   // not "MIT OpenCourseWare · OpenCourseWare"
  }
  // extra: optional meta strings instead of recommenders + years; note: line under the quote
  function card(c, extra, note) {
    var meta = [];
    if (extra) extra.forEach(function (t, k) { meta.push(el("span", { class: k ? "dot" : null, text: t })); });
    else {
      meta.push(el("span", { text: plural(c.n_authors, "HN recommender", "HN recommenders") }));
      meta.push(el("span", { class: "dot", text: years(c) }));
    }
    return el("article", { class: "card", "data-id": c.id }, [
      el("div", { class: "kicker", text: kicker(c) }),
      likeButton(c),
      el("h3", null, [el("a", { href: courseHref(c), text: c.title })]),
      c.quote ? el("p", { class: "q", text: "“" + c.quote + "”" }) : null,
      note ? el("div", { class: "why-pick", text: note }) : null,
      tags(c),
      el("div", { class: "meta" }, meta),
    ]);
  }

  // Renders a filtered list into a grid in pages of PAGE_SIZE as the user scrolls.
  function pagedGrid(container) {
    var grid = el("div", { class: "grid" });
    var sentinel = el("div", { class: "sentinel" });
    container.appendChild(grid);
    container.appendChild(sentinel);
    var list = [], shown = 0;
    function more() {
      var frag = document.createDocumentFragment();
      list.slice(shown, shown + PAGE_SIZE).forEach(function (c) { frag.appendChild(card(c)); });
      shown = Math.min(list.length, shown + PAGE_SIZE);
      grid.appendChild(frag);
    }
    var io = "IntersectionObserver" in window ? new IntersectionObserver(function (es) {
      if (es[0].isIntersecting && shown < list.length) more();
    }, { rootMargin: "1200px" }) : null;
    if (io) io.observe(sentinel);
    var set = function (newList) {
      list = newList; shown = 0;
      grid.textContent = "";
      if (!list.length) grid.appendChild(el("p", { class: "empty", text: "No courses match." }));
      more();
      if (!io) while (shown < list.length) more();
    };
    set.more = function () { if (shown < list.length) more(); };
    return set;
  }

  function toggle(label, key, onChange, initial) {
    var box = el("input", { type: "checkbox" });
    box.checked = params.has(key) ? params.get(key) === "1" : !!initial;
    box.addEventListener("change", onChange);
    return { node: el("label", { class: "toggle" }, [box, label]), get: function () { return box.checked; }, key: key, def: !!initial };
  }

  function wireSearch(onInput) {
    var input = $("#q");
    if (!input) return;
    if (params.get("q")) input.value = params.get("q");
    if (onInput) {
      var t;
      input.addEventListener("input", function () { clearTimeout(t); t = setTimeout(onInput, 120); });
      input.form.addEventListener("submit", function (e) { e.preventDefault(); onInput(); });
    }
  }
  function query() { var i = $("#q"); return i ? i.value.trim().toLowerCase() : ""; }
  function matches(c, q) {
    if (!q) return true;
    return q.split(/\s+/).every(function (w) { return c.hay.indexOf(w) !== -1; });
  }

  function fail(err) {
    console.warn(err);
    var m = $("main");
    m.textContent = "";
    m.appendChild(el("p", { class: "empty", text: "Couldn't load data. " + err.message }));
  }

  // Filter bar shared by home and category pages. Returns {nodes, test(c), sync()}.
  function filterBar(d, onChange, opts) {
    var free = toggle("Free only", "free", onChange);
    var video = toggle("Video lectures", "video", onChange);
    var assign = toggle("Has assignments", "assign", onChange);
    var readings = toggle("Show reading lists", "readings", onChange);
    var provs = {};
    d.courses.forEach(function (c) { if (c.provider) provs[c.provider] = (provs[c.provider] || 0) + 1; });
    var prov = el("select", { "aria-label": "Provider" }, [el("option", { value: "", text: "All providers" })].concat(
      Object.keys(provs).sort(function (a, b) { return provs[b] - provs[a] || a.localeCompare(b); }).map(function (p) {
        return el("option", { value: p, text: p + " (" + provs[p] + ")" });
      })));
    prov.value = params.get("provider") || "";
    prov.addEventListener("change", onChange);
    var cat = null;
    if (!opts || !opts.noCategory) {
      cat = el("select", { "aria-label": "Category" }, [el("option", { value: "", text: "All categories" })].concat(
        d.taxonomy.areas.map(function (a) {
          return el("optgroup", { label: a.name }, a.categories.map(function (c) {
            return el("option", { value: c.id, text: c.name + " (" + c.count + ")" });
          }));
        })));
      cat.value = d.cats[params.get("cat")] ? params.get("cat") : "";
      cat.addEventListener("change", onChange);
    }
    var sort = el("select", { "aria-label": "Sort" }, [
      el("option", { value: "rank", text: "Most recommended" }),
      el("option", { value: "recent", text: "Recently recommended" }),
      el("option", { value: "months", text: "Recommended longest" }),
      el("option", { value: "title", text: "Title A–Z" }),
    ]);
    sort.value = params.get("sort") || "rank";
    if (!sort.value) sort.value = "rank";
    sort.addEventListener("change", onChange);
    var toggles = [free, video, assign, readings];
    return {
      nodes: [free.node, video.node, assign.node, prov, cat, readings.node, sort],
      category: function () { return cat ? cat.value : ""; },
      test: function (c) {
        if (free.get() && c.free !== "free" && c.free !== "audit") return false;
        if (video.get() && !c.video) return false;
        if (assign.get() && !c.has_assignments) return false;
        if (!readings.get() && isReading(c)) return false;
        if (prov.value && c.provider !== prov.value) return false;
        if (cat && cat.value && c.cats.indexOf(cat.value) === -1) return false;
        return true;
      },
      sort: function (list) {
        var k = sort.value;
        if (k === "rank") return list;
        return list.slice().sort(function (a, b) {
          if (k === "recent") return b.last_year - a.last_year || a.rank - b.rank;
          if (k === "months") return b.n_months - a.n_months || a.rank - b.rank;
          return a.title.localeCompare(b.title);
        });
      },
      // keep filters in the URL so a filtered view can be shared or reloaded
      sync: function (extra) {
        var p = new URLSearchParams();
        Object.keys(extra || {}).forEach(function (k) { if (extra[k]) p.set(k, extra[k]); });
        if (query()) p.set("q", query());
        toggles.forEach(function (t) { if (t.get() !== t.def) p.set(t.key, t.get() ? "1" : "0"); });
        if (prov.value) p.set("provider", prov.value);
        if (cat && cat.value) p.set("cat", cat.value);
        if (sort.value !== "rank") p.set("sort", sort.value);
        var s = p.toString();
        history_replace(location.pathname + (s ? "?" + s : ""));
      },
    };
  }
  function history_replace(url) { try { window.history.replaceState(null, "", url); } catch (e) { /* file:// */ } }

  // ---------- home ----------
  function home(d) {
    var main = $("main");
    var area = params.get("area") || "";
    var chips = el("div", { class: "chips", role: "tablist" });
    var count = el("span", { class: "count" });
    var picks = el("section", { class: "for-you" });
    var set, fb;

    function addChip(id, name) {
      var b = el("button", { class: "chip" + (id === area ? " active" : ""), type: "button", text: name });
      b.addEventListener("click", function () {
        area = id;
        Array.prototype.forEach.call(chips.children, function (c) { c.classList.remove("active"); });
        b.classList.add("active");
        update();
      });
      chips.appendChild(b);
    }
    addChip("", "All");
    d.taxonomy.areas.forEach(function (a) { addChip(a.id, a.name); });

    function update() {
      var q = query();
      var list = d.courses.filter(function (c) {
        if (area && !c.cats.some(function (k) { return d.areaOfCat[k] && d.areaOfCat[k].id === area; })) return false;
        return fb.test(c) && matches(c, q);
      });
      list = fb.sort(list);
      count.textContent = plural(list.length, "course", "courses");
      set(list);
      fb.sync({ area: area });
      // For You only on the unfiltered home view
      picks.hidden = !!(q || area || location.search.replace(/^\?/, ""));
    }

    function renderPicks() {
      picks.textContent = "";
      var p = forYou(d, 12);
      if (!p.length) return;
      picks.appendChild(el("div", { class: "shelf-head" }, [
        el("h2", { text: "For you" }),
        el("span", { class: "muted", text: "From your likes and history, by embedding similarity" }),
        el("a", { href: "library.html", text: "Library" }),
      ]));
      picks.appendChild(el("div", { class: "shelf" }, p.map(function (x) {
        return card(x.c, null, "Because you " + (isLiked(x.because.id) ? "liked " : "opened ") + x.because.title);
      })));
    }

    fb = filterBar(d, update);
    main.textContent = "";
    main.appendChild(el("div", { class: "page-head" }, [
      el("h1", { text: "Courses Hacker News keeps recommending" }),
      el("p", { text: d.meta.n_courses + " courses, each recommended by at least " + d.meta.min_authors +
        " different people in " + d.meta.min_months + "+ different months, from " +
        d.meta.n_comments.toLocaleString() + " HN comments (" + monthName(d.meta.first_month) + "–" +
        monthName(d.meta.last_month) + "). Press ? for keyboard shortcuts." }),
    ]));
    main.appendChild(picks);
    main.appendChild(chips);
    main.appendChild(el("div", { class: "filters" }, fb.nodes.concat([count])));
    set = pagedGrid(main);
    wireSearch(update);
    renderPicks();
    update();
    window.addEventListener("pageshow", function (e) { if (e.persisted) renderPicks(); });
  }

  // ---------- categories ----------
  function categoryIndex(d) {
    var main = $("main");
    main.textContent = "";
    document.title = "Categories · HN Course Picks";
    main.appendChild(el("div", { class: "page-head" }, [
      el("h1", { text: "Categories" }),
      el("p", { text: d.taxonomy.areas.length + " areas, " + Object.keys(d.cats).length +
        " categories from HackerTube's taxonomy. Courses carry no labels of their own: each is placed by " +
        "the categories of the HackerTube videos nearest to it by embedding, so expect some misfits." }),
    ]));
    var wrap = el("div", { class: "areas" });
    d.taxonomy.areas.forEach(function (a) {
      wrap.appendChild(el("section", { class: "area" }, [
        el("h2", { text: a.name }),
        el("ul", null, a.categories.map(function (c) {
          return el("li", null, [el("a", { href: "category.html?c=" + encodeURIComponent(c.id), title: c.description }, [
            c.name, el("span", { text: String(c.count) }),
          ])]);
        })),
      ]));
    });
    main.appendChild(wrap);
  }

  function categoryPage(d, id) {
    var c = d.cats[id];
    if (!c) return categoryIndex(d);
    var a = d.areaOfCat[id];
    var main = $("main");
    document.title = c.name + " · HN Course Picks";
    var count = el("span", { class: "count" });
    var set, fb;
    function update() {
      var q = query();
      var list = fb.sort(d.courses.filter(function (x) {
        return x.cats.indexOf(id) !== -1 && fb.test(x) && matches(x, q);
      }));
      count.textContent = plural(list.length, "course", "courses");
      set(list);
      fb.sync({ c: id });
    }
    fb = filterBar(d, update, { noCategory: true });
    main.textContent = "";
    main.appendChild(el("div", { class: "page-head" }, [
      el("div", { class: "crumb" }, [el("a", { href: "category.html", text: "Categories" }), " / " + a.name]),
      el("h1", { text: c.name }),
      el("p", { text: c.description }),
    ]));
    var siblings = el("div", { class: "chips" }, a.categories.map(function (s) {
      return el("a", { class: "chip" + (s.id === id ? " active" : ""), href: "category.html?c=" + encodeURIComponent(s.id), text: s.name });
    }));
    main.appendChild(siblings);
    main.appendChild(el("div", { class: "filters" }, fb.nodes.concat([count])));
    set = pagedGrid(main);
    wireSearch(update);
    update();
    var active = siblings.querySelector(".active");
    if (active) siblings.scrollLeft = active.offsetLeft - 16;
  }

  // ---------- course ----------
  var MATCH_LABEL = { code: "same course number", title: "same course title", similar: "close match" };

  function lectureCard(v) {
    var url = "https://www.youtube.com/watch?v=" + encodeURIComponent(v.vid);
    var img = el("img", { src: "https://i.ytimg.com/vi/" + encodeURIComponent(v.vid) + "/hq2.jpg", alt: "", loading: "lazy", decoding: "async" });
    function fb() { if (!img.dataset.fb) { img.dataset.fb = "1"; img.src = "https://i.ytimg.com/vi/" + encodeURIComponent(v.vid) + "/hqdefault.jpg"; } }
    img.addEventListener("error", fb);
    img.addEventListener("load", function () { if (img.naturalWidth <= 120) fb(); });
    return el("a", { class: "lecture", href: url, target: "_blank", rel: "noopener", title: v.title }, [
      el("span", { class: "thumb" }, [img]),
      el("h3", { text: v.title }),
      el("div", { class: "meta" }, [
        el("span", { text: v.channel }),
        v.recs ? el("span", { class: "dot", text: plural(v.recs, "HN recommender", "HN recommenders") }) : null,
        el("span", { class: "dot", text: MATCH_LABEL[v.match] || v.match }),
      ]),
      v.why ? el("p", { text: v.why }) : null,
    ]);
  }

  function coursePage(d, id) {
    var c = d.byId[id];
    var main = $("main");
    main.textContent = "";
    if (!c) {
      main.appendChild(el("p", { class: "empty" }, ["Course not found. ", el("a", { href: "./", text: "Back home" })]));
      return;
    }
    recordVisit(c.id);
    document.title = c.title + " · HN Course Picks";
    var live = safeURL(c.final_url) || safeURL(c.url);
    var wb = safeURL(c.wayback_url);
    var dead = c.status === "dead";
    var goURL = dead && wb ? wb : live;

    var cats = c.cats.filter(function (k) { return d.cats[k]; });
    var facts = [];
    if (c.instructors && c.instructors.length) facts.push("Instructors: " + c.instructors.join(", "));
    if (c.page_title && c.page_title !== c.summary) facts.push("Page title: " + c.page_title);
    var primary = el("div", { class: "primary" }, [
      el("div", { class: "crumb" }, cats.length ? [
        el("a", { href: "category.html?c=" + encodeURIComponent(cats[0]), text: d.cats[cats[0]].name }),
      ] : [el("a", { href: "./", text: "All courses" })]),
      el("h1", { text: c.title }),
      el("div", { class: "course-meta" }, [
        el("span", { text: c.provider || "Unknown provider" }),
        el("span", { class: "dot", text: c.format }),
        el("span", { class: "dot", text: plural(c.n_authors, "HN recommender", "HN recommenders") }),
        el("span", { class: "dot", text: years(c) }),
      ]),
      tags(c),
      el("div", { class: "actions" }, [
        goURL ? el("a", { class: "btn primary", href: goURL, target: "_blank", rel: "noopener", title: "Open (o)",
          text: dead && wb ? "Open archived copy" : "Go to course" }) : null,
        likeButton(c, true),
        dead && wb && live ? el("a", { class: "btn", href: live, target: "_blank", rel: "noopener", text: "Original link" }) : null,
      ]),
      dead ? el("p", { class: "note" }, [
        el("b", { text: "This course page is gone. " }),
        (c.dead_reason ? "When we checked it: " + c.dead_reason + ". " : "") +
        (wb ? "“Open archived copy” goes to the latest Wayback Machine snapshot, which may be older than the course's last run."
          : "The Wayback Machine has no snapshot of it."),
      ]) : null,
      c.status === "unknown" ? el("p", { class: "note" }, [
        el("b", { text: "Couldn't check this page. " }),
        "It sent a bot check or login page, or wasn't reachable from our crawler" + (c.dead_reason ? " (" + c.dead_reason + ")" : "") +
        ". It probably still works." + (wb ? " " : ""),
        wb ? el("a", { href: wb, target: "_blank", rel: "noopener", text: "Wayback snapshot" }) : null,
      ]) : null,
      el("div", { class: "panel" }, [
        el("h2", { text: "Summary" }),
        el("p", { class: c.summary ? null : "muted", text: c.summary ||
          "The course page gives no description. The HN comments below say what it covers." }),
        facts.length ? el("p", { class: "muted", text: facts.join(" · ") }) : null,
        cats.length ? el("div", { class: "chips" }, cats.map(function (k) {
          return el("a", { class: "chip", href: "category.html?c=" + encodeURIComponent(k), text: d.cats[k].name });
        })) : null,
      ]),
      el("div", { class: "panel" }, [
        el("h2", { text: "Why HN recommends it" }),
        el("p", null, [el("a", { href: c.quote_url, target: "_blank", rel: "noopener", text: "“" + c.quote + "”" })]),
        el("p", { class: "muted", text: "— " + c.quote_author + ", " + monthName(c.quote_month) + ". Linked in " +
          plural(c.n_mentions, "comment", "comments") + " by " + plural(c.n_authors, "person", "people") + " across " +
          plural(c.n_months, "month", "months") + ", " + years(c) + "." }),
      ]),
    ]);
    var lectures = el("section", { class: "section" });
    var comments = el("section", { class: "section" });
    primary.appendChild(lectures);
    primary.appendChild(comments);
    var side = el("aside", { class: "side" });
    main.appendChild(el("div", { class: "course" }, [primary, side]));

    side.appendChild(el("h2", { text: "Similar courses" }));
    side.appendChild(el("div", { class: "side-list" }, c.nb.map(function (p) { return d.courses[p[0]]; })
      .filter(function (o) { return !isReading(o) || isReading(c); }).slice(0, 10).map(function (o) { return card(o); })));

    getJSON("data/c/" + encodeURIComponent(c.id) + ".json").then(function (x) {
      if (x.lectures.length) {
        lectures.appendChild(el("h2", { text: "Lectures on HackerTube" }));
        lectures.appendChild(el("p", { class: "muted", text: "Lecture and course videos that HN recommends, matched to this course by course number, title or embedding similarity." }));
        lectures.appendChild(el("div", { class: "lectures" }, x.lectures.map(lectureCard)));
      }
      comments.appendChild(el("h2", { text: "What HN said (" + x.mentions.length + " of " + c.n_mentions + " comments)" }));
      x.mentions.forEach(function (m) {
        var hn = "https://news.ycombinator.com/item?id=" + encodeURIComponent(m.id);
        var linked = safeURL(m.url);
        comments.appendChild(el("div", { class: "comment" }, [
          el("blockquote", { text: m.text }),
          el("div", { class: "meta" }, [
            el("b", { text: m.author }),
            el("span", { class: "dot", text: monthName(m.month) }),
            m.story ? el("span", { class: "dot", text: "on “" + m.story + "”" }) : null,
            el("span", { class: "dot" }, [el("a", { href: hn, target: "_blank", rel: "noopener", text: "View on HN" })]),
            linked ? el("span", { class: "linked" }, ["Linked: ", el("a", { href: linked, target: "_blank", rel: "noopener nofollow", text: m.url })]) : null,
          ]),
        ]));
      });
    }).catch(function (e) { console.warn(e); });
  }

  // ---------- library: likes, history, For You ----------
  function library(d) {
    var main = $("main");
    document.title = "Library · HN Course Picks";
    function render() {
      main.textContent = "";
      main.appendChild(el("div", { class: "page-head" }, [
        el("h1", { text: "Library" }),
        el("p", { text: "Your likes and history are kept in this browser only. Nothing is sent anywhere." }),
      ]));
      var p = forYou(d, 24);
      var liked = likes().map(function (id) { return d.byId[id]; }).filter(Boolean);
      var seen = history().filter(function (h) { return d.byId[h.id]; });
      section("For you", p.length ? p.map(function (x) {
        return card(x.c, null, "Because you " + (isLiked(x.because.id) ? "liked " : "opened ") + x.because.title);
      }) : null, "Like or open a few courses and recommendations show up here.");
      section("Liked", liked.map(function (c) { return card(c); }), "Nothing liked yet. Press ♡ on a course, or l on its page.",
        liked.length ? function () { writeStore("hc.likes", []); render(); } : null);
      section("History", seen.map(function (h) {
        var c = d.byId[h.id];
        return card(c, [c.provider || "", "opened " + new Date(h.t).toLocaleDateString()]);
      }), "No history yet.", seen.length ? function () { writeStore("hc.history", []); render(); } : null);
    }
    function section(title, cards, empty, clear) {
      main.appendChild(el("div", { class: "lib-head" }, [
        el("h2", { text: title }),
        clear ? el("button", { type: "button", class: "linkbtn", text: "Clear", onclick: function () {
          if (confirm("Clear " + title.toLowerCase() + "?")) clear();
        } }) : null,
      ]));
      main.appendChild(cards && cards.length ? el("div", { class: "grid" }, cards) : el("p", { class: "muted", text: empty }));
    }
    render();
    document.addEventListener("click", function (e) {
      // unliking from the Liked list should drop the card
      if (e.target.closest && e.target.closest("[data-like]")) setTimeout(render, 0);
    });
  }

  // ---------- timeline ----------
  var TL_TOP = 12, TL_DEBUTS = 8;

  function timeline(d) {
    var main = $("main");
    main.textContent = "";
    document.title = "Timeline · HN Course Picks";
    getJSON("data/timeline.json").then(function (tl) {
      var yrs = tl.years;
      var lastYear = yrs[yrs.length - 1].y;
      var throughMonth = +tl.through.slice(5, 7);
      var partial = throughMonth < 12 ? "Jan–" + MONTHS[throughMonth - 1] : "";
      var readings = toggle("Show reading lists", "readings", render);
      var body = el("div", { class: "years" });

      main.appendChild(el("div", { class: "page-head" }, [
        el("h1", { text: "Timeline" }),
        el("p", { text: "Which courses Hacker News recommended each year, " + yrs[0].y + "–" + lastYear +
          ". Bars count comments linking one of these courses" + (partial ? "; " + lastYear + " runs " + partial + "." : ".") }),
      ]));
      main.appendChild(yearChart(yrs, lastYear, partial));
      main.appendChild(el("div", { class: "toolbar" }, [readings.node]));
      main.appendChild(body);

      function render() {
        var show = readings.get();
        function keep(c) { return c && (show || !isReading(c)); }
        body.textContent = "";
        yrs.slice().reverse().forEach(function (yr) {
          var top = yr.top.map(function (t) { return [d.courses[t[0]], t[1]]; })
            .filter(function (t) { return keep(t[0]); }).slice(0, TL_TOP);
          var debuts = yr.debuts.map(function (i) { return d.courses[i]; }).filter(keep).slice(0, TL_DEBUTS);
          body.appendChild(el("section", { class: "year", id: "y" + yr.y }, [
            el("div", { class: "year-head" }, [
              el("h2", null, [String(yr.y), yr.y === lastYear && partial ? el("span", { class: "partial", text: partial }) : null]),
              el("p", { class: "meta", text: plural(yr.mentions, "comment", "comments") + " from " +
                plural(yr.people, "person", "people") + " · " + plural(yr.courses, "course", "courses") + " · " +
                yr.new.toLocaleString() + " linked for the first time" }),
            ]),
            top.length ? el("h3", { text: "Most recommended" }) : null,
            top.length ? el("div", { class: "shelf" }, top.map(function (t) {
              return card(t[0], [plural(t[1], "recommender", "recommenders") + " in " + yr.y]);
            })) : null,
            debuts.length ? el("h3", { text: "First recommended in " + yr.y }) : null,
            debuts.length ? el("div", { class: "shelf" }, debuts.map(function (c) { return card(c); })) : null,
          ]));
        });
      }
      render();
      var target = location.hash && document.getElementById(location.hash.slice(1));
      if (target) target.scrollIntoView();
    }).catch(fail);
  }

  // Column chart of comments per year. Each column links to its year.
  function yearChart(yrs, lastYear, partial) {
    var max = Math.max.apply(null, yrs.map(function (y) { return y.mentions; }));
    var peak = yrs.filter(function (y) { return y.mentions === max; })[0].y;
    var readout = el("p", { class: "tl-readout", "aria-live": "polite" });
    function describe(yr) {
      return yr.y + (yr.y === lastYear && partial ? " (" + partial + ")" : "") + ": " +
        plural(yr.mentions, "comment", "comments") + " from " + plural(yr.people, "person", "people");
    }
    var resting = describe(yrs.filter(function (y) { return y.y === peak; })[0]) + " (peak)";
    readout.textContent = resting;
    var cols = el("div", { class: "tl-cols" }, yrs.map(function (yr) {
      var label = yr.y === peak || yr.y === lastYear ? yr.mentions.toLocaleString() : null;
      var a = el("a", { class: "tl-col", href: "#y" + yr.y, "aria-label": describe(yr) }, [
        el("span", { class: "tl-plot" }, [
          el("span", { class: "tl-bar" + (yr.y === lastYear && partial ? " partial" : ""), style: "height:" + (88 * yr.mentions / max).toFixed(1) + "%" },
            [label ? el("span", { class: "tl-val", text: label }) : null]),
        ]),
        el("span", { class: "tl-year", text: yr.y === yrs[0].y || yr.y === lastYear ||
          (yr.y % 5 === 0 && yr.y - yrs[0].y > 1 && lastYear - yr.y > 1) ? String(yr.y) : "" }),
      ]);
      function on() { readout.textContent = describe(yr); }
      function off() { readout.textContent = resting; }
      a.addEventListener("mouseenter", on); a.addEventListener("focus", on);
      a.addEventListener("mouseleave", off); a.addEventListener("blur", off);
      return a;
    }));
    return el("figure", { class: "tl-chart" }, [
      el("figcaption", { text: "HN comments linking a listed course, per year" }), cols, readout,
    ]);
  }

  // ---------- keyboard shortcuts ----------
  var SHORTCUTS = [
    ["/", "Search"], ["j / k", "Next / previous course"], ["Enter", "Open the selected course"],
    ["l", "Like the selected course (or this course)"], ["o", "Open the course site (course page)"],
    ["g h", "Home"], ["g c", "Categories"], ["g t", "Timeline"], ["g l", "Library: likes, history, For You"],
    ["?", "Show this help"], ["Esc", "Close / leave the search box"],
  ];
  var helpDialog = null;
  function showHelp() {
    if (!helpDialog) {
      helpDialog = el("dialog", { class: "help", "aria-label": "Keyboard shortcuts" }, [
        el("h2", { text: "Keyboard shortcuts" }),
        el("dl", null, SHORTCUTS.reduce(function (acc, s) {
          acc.push(el("dt", null, s[0].split(" ").map(function (k, i) {
            return k === "/" && s[0].length > 1 ? " / " : el("kbd", { text: k });
          })));
          acc.push(el("dd", { text: s[1] }));
          return acc;
        }, [])),
      ]);
      helpDialog.addEventListener("click", function (e) { if (e.target === helpDialog) helpDialog.close(); });
      document.body.appendChild(helpDialog);
    }
    if (helpDialog.open) helpDialog.close();
    else if (helpDialog.showModal) helpDialog.showModal();
    else helpDialog.setAttribute("open", "");
  }
  function cards() { return Array.prototype.slice.call(document.querySelectorAll("main .card")); }
  function selected() { return document.querySelector("main .card.kb"); }
  function select(n) {
    var all = cards();
    if (!all.length) return;
    var cur = selected(), i = cur ? all.indexOf(cur) : -1;
    var j = Math.max(0, Math.min(all.length - 1, i + n));
    if (cur) cur.classList.remove("kb");
    all[j].classList.add("kb");
    var link = all[j].querySelector("h3 a");
    if (link) link.focus({ preventScroll: true });
    all[j].scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  var gPending = 0;
  function onKey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
    if (e.key === "Escape") {
      if (typing) t.blur();
      var s = selected(); if (s) s.classList.remove("kb");
      return;
    }
    if (typing || (helpDialog && helpDialog.open && e.key !== "?")) return;
    if (gPending && Date.now() - gPending < 1500) {
      gPending = 0;
      var to = { h: "./", c: "category.html", t: "timeline.html", l: "library.html" }[e.key];
      if (to) { e.preventDefault(); location.href = to; }
      return;
    }
    var page = document.body.dataset.page;
    switch (e.key) {
      case "/": e.preventDefault(); var q = $("#q"); if (q) { q.focus(); q.select(); } break;
      case "?": e.preventDefault(); showHelp(); break;
      case "j": e.preventDefault(); select(1); break;
      case "k": e.preventDefault(); select(-1); break;
      case "g": gPending = Date.now(); break;
      case "l": {
        var sel = selected() || (t && t.closest && t.closest(".card"));
        var id = sel ? sel.dataset.id : page === "course" ? params.get("c") : null;
        if (id) { e.preventDefault(); toggleLike(id); }
        break;
      }
      case "o": {
        if (page !== "course") break;
        var go = document.querySelector(".actions .btn.primary");
        if (go) { e.preventDefault(); window.open(go.href, "_blank", "noopener"); }
        break;
      }
    }
  }
  document.addEventListener("keydown", onKey);
  document.addEventListener("focusin", function (e) {
    // keep the j/k selection in step with Tab focus
    var c = e.target.closest && e.target.closest("main .card");
    var s = selected();
    if (s && s !== c) s.classList.remove("kb");
    if (c) c.classList.add("kb");
  });
  var kb = $("#kbd-help");
  if (kb) kb.addEventListener("click", showHelp);

  // ---------- boot ----------
  var page = document.body.dataset.page;
  if (page !== "home") wireSearch(null); // header search submits to the home page
  loadCourses().then(function (d) {
    if (page === "home") home(d);
    else if (page === "category") params.get("c") ? categoryPage(d, params.get("c")) : categoryIndex(d);
    else if (page === "course") coursePage(d, params.get("c"));
    else if (page === "library") library(d);
    else if (page === "timeline") timeline(d);
  }).catch(fail);
})();
