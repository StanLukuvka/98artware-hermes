// art98 tab for the Hermes web dashboard (hermes dashboard), at /art98.
// Plain IIFE, no build step: React and components come from window.__HERMES_PLUGIN_SDK__.
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { useState, useEffect, useCallback } = SDK.hooks;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button } = SDK.components;
  const h = React.createElement;
  const KINDS = ["recordings", "animations", "states", "screenshots"];
  const API = "/api/plugins/art98";

  function useJSON(path, everyMs) {
    const [state, setState] = useState({ data: null, error: null });
    const load = useCallback(() => {
      SDK.fetchJSON(API + path)
        .then((data) => setState({ data, error: null }))
        .catch((error) => setState({ data: null, error: String(error && error.message || error) }));
    }, [path]);
    useEffect(() => {
      load();
      const id = setInterval(load, everyMs);
      return () => clearInterval(id);
    }, [load, everyMs]);
    return [state, load];
  }

  function Folder({ kind }) {
    const [{ data, error }] = useJSON("/media/" + kind, 10000);
    const files = (data && data.files) || [];
    return h(Card, null,
      h(CardHeader, null, h(CardTitle, null, kind + " (" + (error ? "!" : data ? files.length : "…") + ")")),
      h(CardContent, null,
        error
          ? h("p", { className: "text-sm text-destructive" }, error)
          : files.length === 0
            ? h("p", { className: "text-sm text-muted-foreground" }, "empty")
            : h("ul", { className: "text-sm space-y-1" },
                files.slice(0, 20).map((f) => h("li", { key: f.name, className: "font-mono truncate" }, f.name)))
      )
    );
  }

  function Art98Page() {
    const [{ data, error }, reload] = useJSON("/status", 5000);
    const connected = !!(data && data.connected);
    return h("div", { className: "flex flex-col gap-4" },
      h(Card, null,
        h(CardHeader, null,
          h("div", { className: "flex items-center gap-3" },
            h(CardTitle, null, "Windows 98"),
            h(Badge, { variant: connected ? "default" : "secondary" }, error ? "backend off" : connected ? "attached" : "not attached"),
            h(Button, { size: "sm", variant: "outline", onClick: reload }, "Refresh")
          )
        ),
        h(CardContent, null,
          error
            ? h("p", { className: "text-sm text-destructive" },
                "Plugin backend unreachable: " + error + ". Is art98 in plugins.enabled?")
            : data
              ? h("div", { className: "text-sm text-muted-foreground space-y-1" },
                  h("p", null, "backend: " + data.backend + (data.note ? " · " + data.note : "")),
                  h("p", { className: "font-mono" }, data.folder))
              : h("p", { className: "text-sm text-muted-foreground" }, "loading…")
        )
      ),
      h("div", { className: "grid gap-4 md:grid-cols-2" }, KINDS.map((k) => h(Folder, { key: k, kind: k })))
    );
  }

  window.__HERMES_PLUGINS__.register("art98", Art98Page);
})();
