// art98 tab for the Hermes web dashboard (hermes dashboard), at /art98.
// Plain IIFE, no build step: React and components come from window.__HERMES_PLUGIN_SDK__.
// Styling follows the dashboard's own pages (icon + title card headers, sortable
// tables like Analytics); layout sizes live in dist/style.css.
//
// Layout (left | right):
//   Status  [refresh] [cog]    | Video output [ live | recording ]
//   Stored animation scripts   | (4:3, the guest is 800x600)
//   History                    |
// The cog opens a settings popout; settings are saved in the plugin's config.json.
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { useState, useEffect, useCallback, useMemo, useRef } = SDK.hooks;
  const {
    Card, CardHeader, CardTitle, CardContent, Badge, Button, Input, Label,
    Checkbox, Select, SelectOption, TabsList, TabsTrigger
  } = SDK.components;
  const cn = SDK.utils.cn;
  const timeAgo = SDK.utils.timeAgo;
  const h = React.createElement;
  const API = "/api/plugins/art98";

  // ── icons (Lucide paths; the SDK has no icon set) ─────────────────────

  const ICONS = {
    edit: [["path", { d: "M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" }],
      ["path", { d: "M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" }]],
    activity: [["path", { d: "M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" }]],
    fileCode: [["path", { d: "M10 12.5 8 15l2 2.5" }], ["path", { d: "m14 12.5 2 2.5-2 2.5" }],
      ["path", { d: "M14 2v4a2 2 0 0 0 2 2h4" }], ["path", { d: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z" }]],
    history: [["path", { d: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" }], ["path", { d: "M3 3v5h5" }], ["path", { d: "M12 7v5l4 2" }]],
    monitor: [["rect", { width: 20, height: 14, x: 2, y: 3, rx: 2 }], ["line", { x1: 8, x2: 16, y1: 21, y2: 21 }], ["line", { x1: 12, x2: 12, y1: 17, y2: 21 }]],
    refresh: [["path", { d: "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" }], ["path", { d: "M21 3v5h-5" }],
      ["path", { d: "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" }], ["path", { d: "M8 16H3v5" }]],
    cog: [["path", { d: "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" }],
      ["circle", { cx: 12, cy: 12, r: 3 }]],
    play: [["polygon", { points: "6 3 20 12 6 21 6 3" }]],
    download: [["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }], ["path", { d: "m7 10 5 5 5-5" }], ["path", { d: "M12 15V3" }]],
    up: [["path", { d: "m5 12 7-7 7 7" }], ["path", { d: "M12 19V5" }]],
    down: [["path", { d: "M12 5v14" }], ["path", { d: "m19 12-7 7-7-7" }]],
    x: [["path", { d: "M18 6 6 18" }], ["path", { d: "m6 6 12 12" }]],
    clapper: [["path", { d: "M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z" }], ["path", { d: "m6.2 5.3 3.1 3.9" }],
      ["path", { d: "m12.4 3.4 3.1 4" }], ["path", { d: "M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" }]],
    plug: [["path", { d: "M12 22v-5" }], ["path", { d: "M9 8V2" }], ["path", { d: "M15 8V2" }], ["path", { d: "M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" }]]
  };

  function Icon({ name, className }) {
    return h("svg", {
      viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2,
      strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true, className: cn("h-4 w-4 shrink-0", className)
    }, ICONS[name].map(([tag, attrs], i) => h(tag, { key: i, ...attrs })));
  }

  // Card header the way the dashboard draws them: muted icon, title, then actions on the right.
  function Header({ icon, title, children }) {
    return h(CardHeader, null,
      h("div", { className: "flex items-center gap-2" },
        h(Icon, { name: icon, className: "h-5 w-5 text-muted-foreground" }),
        h(CardTitle, { className: "text-base" }, title),
        children && h("div", { className: "ml-auto flex items-center gap-2" }, children)));
  }

  // ── data ──────────────────────────────────────────────────────────────

  function errorText(e) {
    // fetchJSON throws "400: {\"detail\": ...}"; show just the detail.
    const m = String((e && e.message) || e).match(/^\d+: (.*)$/s);
    if (m) { try { return JSON.parse(m[1]).detail || m[1]; } catch (x) { return m[1]; } }
    return String((e && e.message) || e);
  }

  function useJSON(path, everyMs) {
    const [state, setState] = useState({ data: null, error: null });
    const load = useCallback(() => {
      SDK.fetchJSON(API + path)
        .then((data) => setState({ data, error: null }))
        .catch((error) => setState({ data: null, error: errorText(error) }));
    }, [path]);
    useEffect(() => {
      load();
      const id = setInterval(load, everyMs);
      return () => clearInterval(id);
    }, [load, everyMs]);
    return [state, load];
  }

  // <img>/<video> can't send the session header, so binary files come in as blobs.
  async function fetchBlobURL(path) {
    const headers = {};
    if (window.__HERMES_SESSION_TOKEN__) headers["X-Hermes-Session-Token"] = window.__HERMES_SESSION_TOKEN__;
    const res = await fetch(API + path, { headers, credentials: "include" });
    if (!res.ok) {
      let detail = res.status + " " + res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* not JSON */ }
      throw new Error(detail);
    }
    return URL.createObjectURL(await res.blob());
  }

  // A plain <a href> can't send the session header either: fetch, then hand the blob to the browser.
  async function downloadFile(kind, name) {
    const url = await fetchBlobURL("/media/" + kind + "/" + encodeURIComponent(name));
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  function useBlobURL(path, everyMs) {
    const [state, setState] = useState({ url: null, error: null });
    const current = useRef(null);
    useEffect(() => {
      if (!path) { setState({ url: null, error: null }); return undefined; }
      // Polls every everyMs while it works; after a failure (no VM yet) it waits 5 s
      // so a stub backend doesn't fill the console with a 503 a second.
      let alive = true;
      let timer = null;
      const next = (ms) => { if (alive && everyMs) timer = setTimeout(load, ms); };
      const load = () => fetchBlobURL(path)
        .then((url) => {
          if (!alive) { URL.revokeObjectURL(url); return; }
          if (current.current) URL.revokeObjectURL(current.current);
          current.current = url;
          setState({ url, error: null });
          next(everyMs);
        })
        .catch((error) => {
          if (!alive) return;
          setState({ url: null, error: String(error.message || error) });
          next(Math.max(everyMs, 5000));
        });
      load();
      return () => {
        alive = false;
        if (timer) clearTimeout(timer);
        if (current.current) { URL.revokeObjectURL(current.current); current.current = null; }
      };
    }, [path, everyMs]);
    return state;
  }

  const bytes = (n) => (n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB");
  const muted = (text) => h("p", { className: "text-sm text-muted-foreground" }, text);
  const failed = (text) => h("p", { className: "text-sm text-destructive" }, text);

  // ── tables (same look as the dashboard's Analytics tables) ────────────

  function useSort(rows, key, dir) {
    const [sort, setSort] = useState({ key, dir });
    const sorted = useMemo(() => {
      const out = rows.slice();
      out.sort((a, b) => {
        const x = a[sort.key], y = b[sort.key];
        const c = typeof x === "string" ? x.localeCompare(y) : (x > y) - (x < y);
        return sort.dir === "asc" ? c : -c;
      });
      return out;
    }, [rows, sort]);
    const toggle = (k) => setSort((s) => (s.key === k ? { key: k, dir: s.dir === "asc" ? "desc" : "asc" } : { key: k, dir: "asc" }));
    return { sorted, sort, toggle };
  }

  // Ten rows a page. When rows shrink past the page (a file removed), the page moves back and stays.
  const PAGE = 10;
  function usePages(rows, size) {
    const per = size || PAGE;
    const [page, setPage] = useState(0);
    const pages = Math.max(1, Math.ceil(rows.length / per));
    const at = Math.min(page, pages - 1);
    useEffect(() => { if (page !== at) setPage(at); }, [page, at]);
    const shown = useMemo(() => rows.slice(at * per, at * per + per), [rows, at, per]);
    return { shown, page: at, pages, per, total: rows.length, go: (p) => setPage(Math.max(0, Math.min(p, pages - 1))) };
  }

  // "11–20 of 34" and Prev/Next under a table; nothing while everything fits on one page.
  function Pager({ pager }) {
    if (pager.pages <= 1) return null;
    const first = pager.page * pager.per + 1, last = Math.min(pager.total, first + pager.per - 1);
    return h("div", { className: "flex items-center gap-2 pt-3 text-xs text-muted-foreground" },
      h("span", { className: "mr-auto" }, first + "–" + last + " of " + pager.total),
      h(Button, { size: "sm", variant: "outline", disabled: pager.page === 0, onClick: () => pager.go(pager.page - 1) }, "Prev"),
      h("span", null, (pager.page + 1) + " / " + pager.pages),
      h(Button, { size: "sm", variant: "outline", disabled: pager.page >= pager.pages - 1, onClick: () => pager.go(pager.page + 1) }, "Next"));
  }

  // columns: [{ key, label, hidden, className, sortable, right }]; `hidden` names a column with no
  // visible label for screen readers. Pass `sort` from useSort to make headers clickable.
  function DataTable({ columns, rows, sort, rowKey, renderRow }) {
    return h("table", { className: "art98-table font-mondwest normal-case text-sm" },
      h("thead", null,
        h("tr", { className: "border-b border-border text-muted-foreground text-xs" },
          columns.map((c, i) => {
            const sortable = sort && c.sortable;
            const active = sortable && sort.sort.key === c.key;
            const pad = i === 0 ? "pr-4" : i === columns.length - 1 ? "pl-4" : "px-4";
            return h("th", {
              key: c.key,
              className: cn("py-2 font-medium", pad, c.right ? "text-right" : "text-left", sortable && "cursor-pointer select-none", c.className),
              onClick: sortable ? () => sort.toggle(c.key) : undefined,
              "aria-sort": active ? (sort.sort.dir === "asc" ? "ascending" : "descending") : undefined
            }, sortable
              ? h("span", { className: "inline-flex items-center gap-1.5 rounded px-1 -mx-1 py-0.5 hover:bg-muted/40 transition-colors" },
                  c.label,
                  active && h(Icon, { name: sort.sort.dir === "asc" ? "up" : "down", className: "h-3.5 w-3.5 text-foreground/80" }))
              : c.label || (c.hidden && h("span", { className: "art98-sr-only" }, c.hidden)));
          }))),
      h("tbody", null, rows.map((r, i) => renderRow(r, rowKey(r, i)))));
  }

  // A row's bottom border: full under the last line of an entry, faint between a script and its scenes.
  const rowBorder = (last) => last ? "border-b border-border/50 hover:bg-secondary/20 transition-colors" : "border-b border-border/20";
  // The count badge in a card header.
  const count = (n) => h("span", { className: "art98-big-badge" }, h(Badge, { variant: "secondary" }, String(n)));
  // A text-or-icon button that looks like a link (table cells); Button is for real buttons.
  const linkButton = ({ label, icon, title, className, onClick }) => h("button", {
      type: "button", onClick, title, "aria-label": label ? undefined : title,
      className: cn("art98-open inline-flex items-center gap-1.5 hover:underline", className) },
    icon && h(Icon, { name: icon, className: "h-3.5 w-3.5" }), label);
  // Download a media file; a failure shows in Status.
  const downloadTo = (say, kind, name) => downloadFile(kind, name)
    .catch((e) => say("Couldn't download " + name + ": " + errorText(e), true));
  // vscode://file/<path> opens the file in VS Code on this PC. The container only knows its own
  // paths, so the link uses the host_folder setting; no setting, no link.
  const vscodeURL = (hostFolder, name) => hostFolder
    ? "vscode://file" + encodeURI(hostFolder.replace(/\/+$/, "") + "/animations/" + name).replace(/#/g, "%23").replace(/\?/g, "%3F")
    : null;
  // Tooltips: Tip for anything that explains (a sentence), a plain `title` for short labels.
  // Tip is position: fixed so a scrolling table can't clip it; it hides on scroll and on Escape,
  // and opens above its anchor near the bottom of the window.
  let tipIds = 0;
  function Tip({ text, children }) {
    const [at, setAt] = useState(null);
    const id = useMemo(() => "art98-tip-" + (++tipIds), []);
    const show = (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      const below = r.bottom + 120 < window.innerHeight;
      setAt({ x: Math.max(8, Math.min(r.left, window.innerWidth - 272)), y: below ? r.bottom + 6 : r.top - 6, below });
    };
    const hide = () => setAt(null);
    useEffect(() => {
      if (!at) return undefined;
      const key = (e) => { if (e.key === "Escape") hide(); };
      window.addEventListener("scroll", hide, true);
      window.addEventListener("keydown", key);
      return () => { window.removeEventListener("scroll", hide, true); window.removeEventListener("keydown", key); };
    }, [at]);
    return h("span", { className: "art98-tip-anchor", "aria-describedby": id, onMouseEnter: show, onMouseLeave: hide, onFocus: show, onBlur: hide },
      children,
      at && h("span", { id, role: "tooltip", className: "art98-tip border border-border bg-card text-card-foreground shadow-lg",
        style: { left: at.x + "px", top: at.y + "px", transform: at.below ? undefined : "translateY(-100%)" } }, text));
  }

  // A script name that downloads the file when clicked, with an Open in VS Code icon beside it.
  const scriptCell = (name, { pad, hostFolder, say }) => h("td", { className: cn("py-2", pad || "pr-4") },
    h("span", { className: "art98-name" },
      h("button", { type: "button", className: "art98-link font-mono-ui text-xs", title: "Download " + name,
        onClick: () => downloadTo(say, "animations", name) },
        name),
      hostFolder && h(Tip, { text: "Open " + name + " in VS Code. What you save there is the script in animations/, so the next Render uses it." },
        h("a", { href: vscodeURL(hostFolder, name), className: "art98-open text-muted-foreground",
            "aria-label": "Open " + name + " in VS Code" },
          h(Icon, { name: "edit", className: "h-3.5 w-3.5" })))));
  const dimCell = (text, pad, right) => h("td", { className: cn("art98-big py-2 text-muted-foreground", pad, right && "text-right") }, text);

  // ── settings ──────────────────────────────────────────────────────────

  // Shared by the page: the popout edits them, the live view reads its refresh rate.
  function useSettings() {
    const [state, setState] = useState({ values: null, schema: null, error: null });
    const load = useCallback(() => {
      SDK.fetchJSON(API + "/config")
        .then((d) => setState({ values: d.values, schema: d.schema, error: null }))
        .catch((e) => setState((s) => ({ ...s, error: errorText(e) })));
    }, []);
    useEffect(load, [load]);
    const save = useCallback((changes) =>
      SDK.fetchJSON(API + "/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes)
      }).then((d) => { setState({ values: d.values, schema: d.schema, error: null }); return d.values; }), []);
    return { ...state, save };
  }

  // What each setting means, in the order the popout shows them.
  const FIELD_TEXT = {
    backend: ["Backend", "Which Windows 98 the tools drive: drive is the real VM, stub and demo are stand-ins."],
    desktop: ["Revert to", "The desktop a revert starts from."],
    screenshot_after: ["Screenshot after actions", "Default when the agent doesn't say."],
    allow_win32: ["Allow art98_win32", "Lets the agent call Win32 functions in the guest directly."],
    live_interval_ms: ["Live refresh (ms)", "How often the live view asks for a new screenshot."],
    record_fps: ["Recording frame rate", "Frames per second for new recordings."],
    host_folder: ["Data folder on this PC", "The plugin's data folder (Hermes's plugin-data/art98) as your PC sees it, not the container's path. Fills in the Open in VS Code links; empty hides them."]
  };

  function Field({ name, spec, value, onChange }) {
    const [label, help] = FIELD_TEXT[name] || [name, ""];
    const id = "art98-setting-" + name;
    if (spec.type === "bool") {
      return h("div", { className: "flex items-start gap-3" },
        h(Checkbox, { id, checked: !!value, onCheckedChange: (v) => onChange(v === true), className: "mt-0.5" }),
        h("div", { className: "space-y-1" },
          h(Label, { htmlFor: id }, label),
          help && h("p", { className: "text-xs text-muted-foreground" }, help)));
    }
    let control;
    if (spec.choices) {
      control = h(Select, { id, value, onValueChange: onChange },
        spec.choices.map((c) => h(SelectOption, { key: c, value: c }, c)));
    } else if (spec.type === "int") {
      control = h(Input, { id, type: "number", min: spec.min, max: spec.max, value: value === "" || value == null ? "" : value,
        onChange: (e) => onChange(e.target.value === "" ? "" : Number(e.target.value)) });
    } else {
      control = h(Input, { id, value: value == null ? "" : value, placeholder: spec.default || "", onChange: (e) => onChange(e.target.value) });
    }
    return h("div", { className: "space-y-1" },
      h(Label, { htmlFor: id }, label),
      control,
      h("p", { className: "text-xs text-muted-foreground" },
        help + (spec.min != null ? " " + spec.min + "–" + spec.max + "." : "")));
  }

  // Cog button plus a popout anchored under it. Closes on Escape or a click outside.
  function SettingsButton({ settings }) {
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState({});
    const [status, setStatus] = useState({ busy: false, error: null, saved: false });
    const box = useRef(null);

    useEffect(() => {
      if (!open) return undefined;
      const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
      const onDown = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
      document.addEventListener("keydown", onKey);
      document.addEventListener("mousedown", onDown);
      return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
    }, [open]);

    const toggle = () => {
      if (!open) { setDraft(settings.values ? { ...settings.values } : {}); setStatus({ busy: false, error: null, saved: false }); }
      setOpen(!open);
    };
    const changed = settings.values ? Object.keys(draft).filter((k) => draft[k] !== settings.values[k]) : [];
    const save = () => {
      const changes = {};
      changed.forEach((k) => { changes[k] = draft[k]; });
      setStatus({ busy: true, error: null, saved: false });
      settings.save(changes)
        .then((values) => { setDraft({ ...values }); setStatus({ busy: false, error: null, saved: true }); })
        .catch((e) => setStatus({ busy: false, error: errorText(e), saved: false }));
    };
    const reset = () => {
      const d = {};
      Object.entries(settings.schema || {}).forEach(([k, spec]) => { d[k] = spec.default; });
      setDraft(d);
    };
    const names = settings.schema
      ? Object.keys(FIELD_TEXT).filter((k) => settings.schema[k]).concat(Object.keys(settings.schema).filter((k) => !FIELD_TEXT[k]))
      : [];

    return h("div", { ref: box, className: "relative" },
      h(Button, { size: "sm", variant: open ? "default" : "outline", onClick: toggle, title: "Settings", "aria-label": "Settings",
        "aria-expanded": open }, h(Icon, { name: "cog" })),
      open && h("div", { role: "dialog", "aria-label": "art98 settings", className: "art98-popout border border-border bg-card text-card-foreground shadow-lg" },
        h("div", { className: "flex items-center gap-2 border-b border-border px-4 py-3" },
          h(Icon, { name: "cog", className: "h-5 w-5 text-muted-foreground" }),
          h(CardTitle, { className: "text-base" }, "Settings"),
          h(Button, { size: "sm", variant: "ghost", className: "ml-auto", onClick: () => setOpen(false), "aria-label": "Close" }, h(Icon, { name: "x" }))),
        h("div", { className: "art98-popout-body space-y-4 px-4 py-3" },
          settings.error && !settings.values ? failed(settings.error)
            : !settings.schema ? muted("loading…")
            : names.map((k) => h(Field, { key: k, name: k, spec: settings.schema[k], value: draft[k],
                onChange: (v) => { setDraft((d) => ({ ...d, [k]: v })); setStatus((s) => ({ ...s, saved: false, error: null })); } }))),
        h("div", { className: "flex items-center gap-2 border-t border-border px-4 py-3" },
          h(Button, { size: "sm", variant: "outline", onClick: reset, disabled: !settings.schema }, "Defaults"),
          h("span", { className: cn("flex-1 truncate text-xs", status.error ? "text-destructive" : "text-muted-foreground"), title: status.error || "" },
            status.error || (status.saved ? "Saved." : changed.length ? changed.length + " unsaved" : "")),
          h(Button, { size: "sm", onClick: save, disabled: status.busy || changed.length === 0 }, status.busy ? "Saving…" : "Save"))));
  }

  // ── left column ───────────────────────────────────────────────────────

  // One status line: icon coloured by tone, label, value, optional detail.
  // tone: "good" | "busy" | "bad" | "idle" (colours in style.css follow the theme).
  function StatusRow({ icon, tone, label, value, detail, grow }) {
    return h("div", { className: cn("flex min-w-0 items-center gap-2 py-1", grow && "art98-grow") },
      h("span", { className: "art98-tone-" + tone }, h(Icon, { name: icon, className: "h-5 w-5" })),
      h("span", { className: "shrink-0 text-muted-foreground" }, label),
      h("span", { className: "art98-tone-" + tone + " shrink-0" }, value),
      detail && h("span", { className: "min-w-0 truncate text-xs text-muted-foreground", title: detail }, detail));
  }

  const VM = { running: ["good", "Running"], booting: ["busy", "Booting"], paused: ["busy", "Paused"],
    off: ["idle", "Off"], none: ["idle", "No VM"] };

  // The one place messages show: what the page just did (`say`) or how a run that ended while
  // the page was open went, whichever came last. Runs that ended before the page opened are
  // not shown. Times are this browser's arrival times, so the server's clock never matters.
  function Messages({ runs, notice }) {
    const seen = useRef(null);  // newest run already accounted for; null until History loads
    const [fromRun, setFromRun] = useState(null);
    const [hiddenBefore, setHiddenBefore] = useState(0);
    useEffect(() => {
      if (!runs) return;
      const run = runs[0];
      if (seen.current === null) { seen.current = run ? run.t : 0; return; }
      if (!run || run.t <= seen.current) return;
      seen.current = run.t;
      setFromRun({
        at: Date.now() / 1000, bad: !run.ok,
        text: run.ok ? run.script + " rendered: " + run.recording
          : run.script + " failed" + (run.code ? " (" + run.code + ")" : "") + ": " + (run.message || "no message")
      });
    }, [runs]);
    const shown = [notice, fromRun].filter((m) => m && m.at > hiddenBefore).sort((x, y) => y.at - x.at)[0];
    if (!shown) return null;
    return h("div", { role: shown.bad ? "alert" : "status", className: cn("art98-message border",
        shown.bad ? "border-destructive/60 text-destructive" : "border-border text-foreground") },
      h("span", { className: "art98-message-when text-muted-foreground" }, timeAgo(shown.at)),
      h("span", { className: "art98-message-text" }, shown.text),
      h(Button, { size: "sm", variant: "ghost", onClick: () => setHiddenBefore(shown.at), "aria-label": "Dismiss message", title: "Dismiss" },
        h(Icon, { name: "x" })));
  }

  function StatusCard({ settings, status, reload, runs, notice }) {
    const { data, error } = status;
    const vm = VM[(data && data.vm) || "none"] || ["idle", data && data.vm];
    const act = data && data.activity;
    const secs = (since) => Math.max(0, Math.round(Date.now() / 1000 - since)) + "s";
    return h(Card, null,
      h(Header, { icon: "activity", title: "Status" },
        h(Button, { size: "sm", variant: "outline", onClick: reload, title: "Refresh", "aria-label": "Refresh" }, h(Icon, { name: "refresh" })),
        h(SettingsButton, { settings })),
      h(CardContent, null,
        error ? failed("Plugin backend unreachable: " + error + ". Is art98 in plugins.enabled?")
          : !data ? muted("loading…")
          : h("div", { className: "font-mondwest normal-case text-sm" },
            h("div", { className: "art98-status-row" },
              h(StatusRow, { icon: "monitor", tone: vm[0], label: "VM", value: vm[1] }),
              h(StatusRow, act
                ? { icon: "clapper", grow: true, tone: act.kind === "record" ? "bad" : "busy", label: "Animation",
                    value: act.kind === "render" ? "Rendering" : act.kind === "record" ? "Recording" : "Running",
                    detail: act.name + " · " + secs(act.since) }
                : { icon: "clapper", grow: true, tone: "idle", label: "Animation", value: "Idle" }),
              h(StatusRow, { icon: "plug", tone: data.connected ? "good" : "idle", label: "Backend",
                value: data.backend, detail: data.connected ? "connected" : "not connected" }))),
        h(Messages, { runs, notice })));
  }

  function ScriptsCard({ hostFolder, say, rendering, onStarted }) {
    const [{ data, error }] = useJSON("/media/animations", 10000);
    const [{ data: sceneData, error: sceneError }] = useJSON("/scenes", 10000);
    const [starting, setStarting] = useState(false);
    const files = useMemo(() => (data && data.files) || [], [data]);
    // POST /render answers at once; the run then shows in Status and lands in History.
    const render = (file, scene) => {
      const what = scene ? file + " › " + scene : file;
      setStarting(true);
      SDK.fetchJSON(API + "/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ animation: file, scene: scene || null })
      }).then((d) => { onStarted(); say("Rendering " + what + " to " + d.output + (d.replaces ? " (replaces the old one)" : "") + "…", false); })
        .catch((e) => say("Couldn't start " + what + ": " + errorText(e), true))
        .finally(() => setStarting(false));
    };
    // The same button on every row; a file with several scenes gets one sub-row per scene.
    // Off while a render runs (from here or the agent) and until the scene list is in.
    const known = sceneData && sceneData.scenes;
    const why = rendering ? "A render is running" : !known ? (sceneError ? "Couldn't read the scenes: " + sceneError : "Reading the scenes…") : null;
    const renderButton = (file, scene) => h(Button, {
        size: "sm", variant: "outline", className: "art98-render", disabled: starting || !!why,
        title: why || "Render " + (scene ? file + " › " + scene : file), onClick: () => render(file, scene) },
      h(Icon, { name: "play", className: "h-3.5 w-3.5 shrink-0" }), "Render");
    const actionCell = (content) => h("td", { className: "art98-fit py-1 pl-4 text-right" }, content);
    const sort = useSort(files, "mtime", "desc");
    const pager = usePages(sort.sorted);
    const columns = [
      { key: "name", label: "Script", sortable: true },
      { key: "bytes", label: "Size", sortable: true, right: true, className: "art98-col-size" },
      { key: "mtime", label: "Modified", sortable: true, right: true, className: "art98-col-when" },
      { key: "action", label: "", hidden: "Render", className: "art98-col-action" }
    ];
    return h(Card, null,
      h(Header, { icon: "fileCode", title: "Stored animation scripts" }, data && count(files.length)),
      h(CardContent, null,
        error ? failed(error)
          : !data ? muted("loading…")
          : files.length === 0 ? muted("No scripts yet. Put art98 scene files (.py) in animations/.")
          : h("div", { className: "art98-scroll" },
              h(DataTable, {
                columns, rows: pager.shown, sort: { ...sort, toggle: (k) => { sort.toggle(k); pager.go(0); } }, rowKey: (f) => f.name,
                renderRow: (f, key) => {
                  const names = (known && known[f.name]) || [];
                  const helper = known && known[f.name] === null;
                  const many = names.length > 1;
                  return h(React.Fragment, { key },
                    h("tr", { className: rowBorder(!many) },
                      scriptCell(f.name, { hostFolder, say }),
                      dimCell(bytes(f.bytes), "px-4", true),
                      dimCell(timeAgo(f.mtime), "px-4", true),
                      actionCell(helper
                        ? h(Tip, { text: "Can't render: " + f.name + " defines no Scene class, so there is nothing to play. It holds code other scripts import (from " + f.name.replace(/\.py$/, "") + " import ...)." },
                            h("span", { className: "art98-noscene text-xs text-muted-foreground", tabIndex: 0 }, "No scene to run"))
                        : many ? h("span", { className: "text-xs text-muted-foreground" }, names.length + " scenes")
                        : renderButton(f.name, null))),
                    many && names.map((n, i) => h("tr", { key: n, className: rowBorder(i === names.length - 1) },
                      h("td", { className: "art98-scene py-1 pr-4", colSpan: 3, title: n },
                        h("span", { className: "font-mono-ui text-xs text-muted-foreground" }, "› " + n)),
                      actionCell(renderButton(f.name, n)))));
                }
              })),
        h(Pager, { pager })));
  }

  // One row per script run: when, which script, how it went, and its recording when it worked.
  // Watch plays the recording in the output card; the icon beside it downloads it.
  function HistoryCard({ history, hostFolder, say, onPlay, playing }) {
    const { data, error } = history;
    const runs = useMemo(() => (data && data.entries) || [], [data]);
    const pager = usePages(runs);
    const columns = [
      { key: "t", label: "When", className: "art98-col-when" },
      { key: "script", label: "Script" },
      { key: "ok", label: "Result", className: "art98-col-result" },
      { key: "recording", label: "Recording", className: "art98-col-rec" }
    ];
    return h(Card, null,
      h(Header, { icon: "history", title: "History" }, data && count(runs.length)),
      h(CardContent, null,
        error ? failed(error)
          : !data ? muted("loading…")
          : runs.length === 0 ? muted("No runs yet. Each animation script the agent renders shows up here.")
          : h("div", { className: "art98-scroll" },
              h(DataTable, {
                columns, rows: pager.shown, rowKey: (r) => r.t + ":" + r.script,
                renderRow: (r, key) => h("tr", { key, className: cn(rowBorder(true), r.recording && r.recording === playing && "bg-secondary/30") },
                  dimCell(timeAgo(r.t), "pr-4"),
                  scriptCell(r.script, { pad: "px-4", hostFolder, say }),
                  h("td", { className: "art98-fit art98-big-badge py-2 px-4", title: r.message || "" },
                    h(Badge, { variant: r.ok ? "default" : "secondary" }, r.ok ? "ok" : r.code || "error")),
                  h("td", { className: "py-1 pl-4", title: r.recording || r.message || "" },
                    r.recording
                      ? h("span", { className: "inline-flex max-w-full items-center gap-3" },
                          linkButton({ label: "Watch", icon: "play", title: "Play " + r.recording + " in Video output",
                            className: r.recording === playing ? "text-foreground" : "art98-tone-good",
                            onClick: () => onPlay(r.recording) }),
                          linkButton({ icon: "download", title: "Download " + r.recording, className: "text-muted-foreground",
                            onClick: () => downloadTo(say, "recordings", r.recording) }))
                      : h("span", { className: "text-muted-foreground" }, "—")))
              })),
        h(Pager, { pager })));
  }

  // ── right column ──────────────────────────────────────────────────────

  function Screen({ children }) {
    return h("div", { className: "art98-screen border border-border" },
      h("div", { className: "flex items-center justify-center" }, children));
  }

  // The live screen. Over VNC (the real backend, VM started with --vnc) it polls fast and the
  // server answers 204 when nothing changed, and it keeps going during renders. Otherwise each
  // frame is a screenshot through the backend, every `everyMs`.
  const VNC_MS = 150;
  function useLiveFrame(everyMs) {
    const [state, setState] = useState({ url: null, error: null, source: null });
    useEffect(() => {
      let alive = true, timer = null, frame = null, current = null;
      const next = (ms) => { if (alive) timer = setTimeout(load, ms); };
      const load = async () => {
        try {
          const headers = {};
          if (window.__HERMES_SESSION_TOKEN__) headers["X-Hermes-Session-Token"] = window.__HERMES_SESSION_TOKEN__;
          const res = await fetch(API + "/live" + (frame ? "?since=" + frame : ""), { headers, credentials: "include", cache: "no-store" });
          if (!alive) return;
          const source = res.headers.get("X-Art98-Source");
          if (res.status === 204) { next(VNC_MS); return; }
          if (!res.ok) {
            let detail = res.status + " " + res.statusText;
            try { detail = (await res.json()).detail || detail; } catch (e) { /* not JSON */ }
            throw new Error(detail);
          }
          frame = res.headers.get("X-Art98-Frame");
          const url = URL.createObjectURL(await res.blob());
          if (!alive) { URL.revokeObjectURL(url); return; }
          if (current) URL.revokeObjectURL(current);
          current = url;
          setState({ url, error: null, source });
          next(source === "vnc" ? VNC_MS : everyMs);
        } catch (error) {
          if (!alive) return;
          frame = null;
          setState({ url: null, error: String(error.message || error), source: null });
          next(Math.max(everyMs, 5000));
        }
      };
      load();
      return () => { alive = false; if (timer) clearTimeout(timer); if (current) URL.revokeObjectURL(current); };
    }, [everyMs]);
    return state;
  }

  function LiveView({ everyMs }) {
    const { url, error, source } = useLiveFrame(everyMs);
    if (url) return h("div", { className: "flex flex-col gap-1" },
      h(Screen, null, h("img", { src: url, alt: "Windows 98 screen", className: "art98-pixels" })),
      h("p", { className: "text-xs text-muted-foreground" },
        source === "vnc" ? "Live over VNC (view only), also during renders." : "Screenshots through the backend; paused while a render runs."));
    return h(Screen, null, h("p", { className: "px-6 text-center text-sm text-muted-foreground" }, error ? "No live view: " + error : "connecting…"));
  }

  // Plays one recording: the one picked in History, else the newest in recordings/.
  function RecordingView({ name: picked }) {
    const [{ data, error }] = useJSON("/media/recordings", 10000);
    const newest = data && data.files && data.files[0] ? data.files[0].name : null;
    const name = picked || newest;
    const video = useBlobURL(name ? "/media/recordings/" + encodeURIComponent(name) : null, 0);
    const isGif = name && /\.gif$/i.test(name);
    return h("div", { className: "flex flex-col gap-2" },
      h(Screen, null,
        !name ? h("p", { className: "text-sm text-muted-foreground" }, error ? error : "No recordings yet.")
          : video.error ? h("p", { className: "text-sm text-destructive" }, video.error)
          : !video.url ? h("p", { className: "text-sm text-muted-foreground" }, "loading " + name + "…")
          : isGif ? h("img", { src: video.url, alt: name })
          : h("video", { key: video.url, src: video.url, controls: true, autoPlay: !!picked })),
      name && h("p", { className: "font-mono-ui text-xs text-muted-foreground truncate", title: name },
        name + (picked ? "" : " (newest)")));
  }

  function OutputCard({ settings, tab, setTab, recording }) {
    const everyMs = (settings.values && settings.values.live_interval_ms) || 1000;
    return h(Card, null,
      h(Header, { icon: "monitor", title: "Video output" }),
      h(CardContent, null,
        h("div", { className: "flex flex-col gap-4" },
          h(TabsList, null,
            h(TabsTrigger, { value: "live", active: tab === "live", onClick: () => setTab("live") }, "Live"),
            h(TabsTrigger, { value: "recording", active: tab === "recording", onClick: () => setTab("recording") }, "Recording")),
          tab === "live" ? h(LiveView, { everyMs }) : h(RecordingView, { name: recording }))));
  }

  // ── page ──────────────────────────────────────────────────────────────

  function Art98Page() {
    const settings = useSettings();
    const [tab, setTab] = useState("live");
    const [recording, setRecording] = useState(null);
    const play = (name) => { setRecording(name); setTab("recording"); };
    const [notice, setNotice] = useState(null);
    const say = useCallback((text, bad) => setNotice({ at: Date.now() / 1000, text, bad }), []);
    // Polled once here and shared: Status and Messages read both, Scripts reads whether a render runs.
    const [status, reloadStatus] = useJSON("/status", 3000);
    const [history] = useJSON("/history?limit=500", 3000);
    const runs = history.data ? history.data.entries || [] : null;
    const act = status.data && status.data.activity;
    const hostFolder = (settings.values || {}).host_folder;
    return h("div", { className: "art98-page" },
      h("div", { className: "art98-wide" }, h(StatusCard, { settings, status, reload: reloadStatus, runs, notice })),
      h("div", { className: "art98-column" },
        h(ScriptsCard, { hostFolder, say, rendering: !!(act && act.kind === "render"), onStarted: reloadStatus }),
        h(HistoryCard, { history, hostFolder, say, onPlay: play, playing: tab === "recording" ? recording : null })),
      h("div", { className: "art98-column" }, h(OutputCard, { settings, tab, setTab, recording })));
  }

  window.__HERMES_PLUGINS__.register("art98", Art98Page);
})();
