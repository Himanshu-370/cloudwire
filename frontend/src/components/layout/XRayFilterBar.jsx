import React, { useEffect, useRef, useState } from "react";

function AnnotationKeyDropdown({ keys, selectedKey, onSelect, loading }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function handleClick(e) {
      if (!ref.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const filtered = search
    ? keys.filter((k) => k.toLowerCase().includes(search.toLowerCase()))
    : keys;

  return (
    <div ref={ref} className="xray-filter-dropdown-wrap">
      <button
        className={`xray-filter-dropdown-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Select annotation key"
      >
        <span className="xray-filter-dropdown-label">
          {loading ? "Loading..." : selectedKey || "Annotation key..."}
        </span>
        <span className="xray-filter-dropdown-caret">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="xray-filter-dropdown-panel">
          <input
            className="xray-filter-search"
            type="text"
            placeholder="Search keys..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            autoFocus
          />
          <div className="xray-filter-key-list">
            {filtered.length === 0 && (
              <div className="xray-filter-empty">
                {keys.length === 0
                  ? "No annotations found in recent traces"
                  : "No matching keys"}
              </div>
            )}
            {filtered.map((key) => (
              <button
                key={key}
                className={`xray-filter-key-item ${key === selectedKey ? "selected" : ""}`}
                onClick={() => {
                  onSelect(key === selectedKey ? "" : key);
                  setOpen(false);
                  setSearch("");
                }}
              >
                <span className="xray-filter-key-dot" />
                <span>{key}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AnnotationValueDropdown({ values, selectedValues, onToggle, onApply, annotationKey }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function handleClick(e) {
      if (!ref.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  // Auto-open when key is selected and values are available
  useEffect(() => {
    if (annotationKey && values.length > 0) setOpen(true);
  }, [annotationKey, values.length]);

  if (!annotationKey) return null;

  return (
    <div ref={ref} className="xray-filter-dropdown-wrap">
      <button
        className={`xray-filter-dropdown-trigger ${open ? "open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Select annotation values"
      >
        <span className="xray-filter-dropdown-label">
          {selectedValues.length === 0
            ? "Select values..."
            : selectedValues.length === 1
            ? selectedValues[0]
            : `${selectedValues.length} values`}
        </span>
        <span className="xray-filter-dropdown-caret">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="xray-filter-dropdown-panel">
          <div className="xray-filter-value-list">
            {values.length === 0 && (
              <div className="xray-filter-empty">No values found</div>
            )}
            {values.map((val) => {
              const checked = selectedValues.includes(val);
              return (
                <label key={val} className={`xray-filter-value-item ${checked ? "checked" : ""}`}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggle(val)}
                    className="xray-filter-checkbox"
                  />
                  <span className="xray-filter-value-check">
                    {checked && (
                      <svg viewBox="0 0 8 8" fill="none" width="8" height="8">
                        <path d="M1 4l2 2 4-4" stroke="#00e7aa" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </span>
                  <span>{val}</span>
                </label>
              );
            })}
          </div>
          {selectedValues.length > 0 && (
            <button
              className="xray-filter-apply-btn"
              onClick={() => {
                onApply();
                setOpen(false);
              }}
            >
              APPLY ({selectedValues.length})
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function GroupSelector({ groups, selectedGroup, onSelect, loading }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function handleClick(e) {
      if (!ref.current?.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  if (groups.length === 0 && !loading) return null;

  return (
    <div ref={ref} className="xray-filter-dropdown-wrap">
      <button
        className={`xray-filter-dropdown-trigger xray-filter-group-trigger ${open ? "open" : ""} ${selectedGroup ? "active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        title="Select an X-Ray group"
      >
        <span className="xray-filter-dropdown-label">
          {loading ? "Loading..." : selectedGroup || "Group..."}
        </span>
        <span className="xray-filter-dropdown-caret">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="xray-filter-dropdown-panel">
          <div className="xray-filter-key-list">
            {selectedGroup && (
              <button
                className="xray-filter-key-item xray-filter-clear-item"
                onClick={() => { onSelect(null); setOpen(false); }}
              >
                Clear group
              </button>
            )}
            {groups.map((g) => (
              <button
                key={g.name}
                className={`xray-filter-key-item ${g.name === selectedGroup ? "selected" : ""}`}
                onClick={() => { onSelect(g.name); setOpen(false); }}
                title={g.filter_expression || "No filter expression"}
              >
                <span className="xray-filter-group-dot" />
                <span>{g.name}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function XRayFilterBar({
  annotationKeys,
  annotationValues,
  annotationsLoading,
  annotationsError,
  selectedAnnotationKey,
  onAnnotationKeyChange,
  selectedAnnotationValues,
  onToggleAnnotationValue,
  onApplyAnnotationFilter,
  activeFilters,
  onRemoveFilter,
  onClearAllFilters,
  groups,
  groupsLoading,
  selectedGroup,
  onSelectGroup,
  advancedMode,
  onToggleAdvanced,
  rawExpression,
  onRawExpressionChange,
  onRefreshAnnotations,
}) {
  return (
    <div className="xray-filter-bar">
      {/* Groups selector */}
      <GroupSelector
        groups={groups}
        selectedGroup={selectedGroup}
        onSelect={onSelectGroup}
        loading={groupsLoading}
      />

      {/* Annotation dropdowns (only when no group is selected) */}
      {!selectedGroup && !advancedMode && (
        <>
          <AnnotationKeyDropdown
            keys={annotationKeys}
            selectedKey={selectedAnnotationKey}
            onSelect={onAnnotationKeyChange}
            loading={annotationsLoading}
          />

          <AnnotationValueDropdown
            values={annotationValues}
            selectedValues={selectedAnnotationValues}
            onToggle={onToggleAnnotationValue}
            onApply={onApplyAnnotationFilter}
            annotationKey={selectedAnnotationKey}
          />
        </>
      )}

      {/* Advanced mode: raw expression */}
      {advancedMode && (
        <input
          className="topbar-xray-filter-input"
          type="text"
          placeholder='annotation.key = "value"'
          value={rawExpression}
          onChange={(e) => onRawExpressionChange(e.target.value)}
          title="X-Ray filter expression"
        />
      )}

      {/* Toggle advanced mode */}
      <button
        className={`xray-filter-advanced-btn ${advancedMode ? "active" : ""}`}
        onClick={onToggleAdvanced}
        title={advancedMode ? "Switch to dropdown filters" : "Switch to advanced filter expression"}
      >
        {advancedMode ? "SIMPLE" : "ADV"}
      </button>

      {/* Refresh annotations */}
      {!advancedMode && !selectedGroup && (
        <button
          className="xray-filter-refresh-btn"
          onClick={onRefreshAnnotations}
          disabled={annotationsLoading}
          title="Refresh available annotations from recent traces"
        >
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
            <path d="M10 2L10 5H7M2 10L2 7H5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M2.5 4.5A4 4 0 0 1 9.5 3.5M9.5 7.5A4 4 0 0 1 2.5 8.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
        </button>
      )}

      {/* Active filter chips */}
      {activeFilters.length > 0 && (
        <div className="xray-filter-chips">
          {activeFilters.map((f, i) => (
            <span key={`${f.key}-${f.value}-${i}`} className="xray-filter-chip">
              <span className="xray-filter-chip-key">{f.key}</span>
              <span className="xray-filter-chip-eq">=</span>
              <span className="xray-filter-chip-val">{f.value}</span>
              <button className="xray-filter-chip-remove" onClick={() => onRemoveFilter(i)}>×</button>
            </span>
          ))}
          <button className="xray-filter-clear-btn" onClick={onClearAllFilters}>Clear all</button>
        </div>
      )}

      {/* Error */}
      {annotationsError && (
        <span className="xray-filter-error" title={annotationsError}>
          ⚠
        </span>
      )}
    </div>
  );
}
