import React, { useState, useRef } from "react";
import { createServiceIcon, SERVICE_VISUALS } from "../../lib/serviceVisuals.jsx";
import { useClickOutside } from "../../hooks/useClickOutside";

export function GraphLegend() {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  useClickOutside(wrapRef, () => setOpen(false), open);

  return (
    <div ref={wrapRef} className="graph-legend-wrap">
      <button
        className={`graph-legend-toggle${open ? " active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Show service legend"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <circle cx="6" cy="6" r="5" stroke="currentColor" strokeWidth="1.2" />
          <circle cx="6" cy="4" r="1" fill="currentColor" />
          <rect x="5.3" y="6" width="1.4" height="3" rx="0.7" fill="currentColor" />
        </svg>
        LEGEND
      </button>

      {open && (
        <div className="graph-legend-popover">
          <div className="graph-legend-popover-title">SERVICE LEGEND</div>
          <div className="graph-legend-grid">
            {Object.entries(SERVICE_VISUALS)
              .filter(([service]) => service !== "unknown")
              .map(([service, visual]) => (
                <div key={service} className="graph-legend-item">
                  <span className="graph-legend-icon" style={{ color: visual.color }}>
                    {createServiceIcon(service, visual.color)}
                  </span>
                  <span className="graph-legend-label">{visual.label}</span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
