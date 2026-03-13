import React, { useCallback, useState } from "react";
import { useClickOutside } from "../../hooks/useClickOutside";

const LAYOUT_OPTIONS = [
  { value: "circular", label: "Circular", icon: "⬡" },
  { value: "flow", label: "Flow", icon: "⇶" },
  { value: "swimlane", label: "Swimlane", icon: "☰" },
];

export function LayoutDropdown({ layoutMode, onLayoutModeChange }) {
  const [open, setOpen] = useState(false);
  const wrapRef = React.useRef(null);
  const current = LAYOUT_OPTIONS.find((o) => o.value === layoutMode) || LAYOUT_OPTIONS[0];
  const close = useCallback(() => setOpen(false), []);
  useClickOutside(wrapRef, close, open);

  return (
    <div className="layout-select-wrap" ref={wrapRef}>
      <button
        className={`layout-select-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Switch layout mode"
      >
        <span className="layout-select-trigger-icon">{current.icon}</span>
        {current.label}
        <span className="layout-select-caret">{open ? "▼" : "▶"}</span>
      </button>
      {open && (
        <div className="layout-select-panel">
          {LAYOUT_OPTIONS.map((opt) => (
            <div
              key={opt.value}
              className={`layout-select-item ${layoutMode === opt.value ? "active" : ""}`}
              onClick={() => { onLayoutModeChange(opt.value); setOpen(false); }}
            >
              <span className="layout-select-item-icon">{opt.icon}</span>
              {opt.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
