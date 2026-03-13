import { useCallback, useEffect, useRef, useState } from "react";

const API_PREFIX = "/api";

async function fetchJson(path) {
  const response = await fetch(`${API_PREFIX}${path}`);
  if (!response.ok) {
    let msg = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      msg = body?.error?.message || msg;
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return response.json();
}

/**
 * Hook for managing X-Ray filter state: annotation key/value dropdowns
 * and X-Ray groups. Constructs the filter expression automatically.
 *
 * @param {string} region - Current AWS region
 * @param {number} timeRangeMinutes - Time window for annotation discovery
 */
export function useXRayFilters(region, timeRangeMinutes = 60) {
  // Annotation discovery state
  const [annotations, setAnnotations] = useState({}); // { key: [val1, val2, ...] }
  const [annotationsLoading, setAnnotationsLoading] = useState(false);
  const [annotationsError, setAnnotationsError] = useState("");

  // Groups state
  const [groups, setGroups] = useState([]); // [{ name, arn, filter_expression }]
  const [groupsLoading, setGroupsLoading] = useState(false);

  // Selected filter state
  const [selectedGroup, setSelectedGroup] = useState(null); // group name or null
  const [selectedAnnotationKey, setSelectedAnnotationKey] = useState("");
  const [selectedAnnotationValues, setSelectedAnnotationValues] = useState([]);
  const [activeFilters, setActiveFilters] = useState([]); // [{ key, value, type: "annotation" }]

  // Advanced mode: raw expression
  const [advancedMode, setAdvancedMode] = useState(false);
  const [rawExpression, setRawExpression] = useState("");

  const fetchTokenRef = useRef(0);

  // Fetch annotations when region or time range changes
  const refreshAnnotations = useCallback(async () => {
    fetchTokenRef.current += 1;
    const token = fetchTokenRef.current;
    setAnnotationsLoading(true);
    setAnnotationsError("");

    try {
      const data = await fetchJson(
        `/xray/annotations?region=${encodeURIComponent(region)}&minutes=${timeRangeMinutes}`
      );
      if (token !== fetchTokenRef.current) return;
      setAnnotations(data.annotations || {});
    } catch (err) {
      if (token !== fetchTokenRef.current) return;
      setAnnotationsError(err instanceof Error ? err.message : String(err));
      setAnnotations({});
    } finally {
      if (token === fetchTokenRef.current) {
        setAnnotationsLoading(false);
      }
    }
  }, [region, timeRangeMinutes]);

  // Fetch groups when region changes
  const refreshGroups = useCallback(async () => {
    setGroupsLoading(true);
    try {
      const data = await fetchJson(`/xray/groups?region=${encodeURIComponent(region)}`);
      setGroups(data.groups || []);
    } catch {
      // Groups are optional — fail silently
      setGroups([]);
    } finally {
      setGroupsLoading(false);
    }
  }, [region]);

  // Auto-fetch on region change
  useEffect(() => {
    refreshAnnotations();
    refreshGroups();
    // Reset selections on region change
    setSelectedGroup(null);
    setSelectedAnnotationKey("");
    setSelectedAnnotationValues([]);
    setActiveFilters([]);
    setRawExpression("");
  }, [region, refreshAnnotations, refreshGroups]);

  // Toggle annotation value selection
  const toggleAnnotationValue = useCallback((value) => {
    setSelectedAnnotationValues((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]
    );
  }, []);

  // Add current annotation selection as an active filter
  const addAnnotationFilter = useCallback(() => {
    if (!selectedAnnotationKey || selectedAnnotationValues.length === 0) return;

    const newFilters = selectedAnnotationValues.map((val) => ({
      key: selectedAnnotationKey,
      value: val,
      type: "annotation",
    }));

    setActiveFilters((prev) => {
      // Remove any existing filters for same key, replace with new
      const withoutKey = prev.filter((f) => f.key !== selectedAnnotationKey);
      return [...withoutKey, ...newFilters];
    });

    // Reset annotation selection
    setSelectedAnnotationKey("");
    setSelectedAnnotationValues([]);
  }, [selectedAnnotationKey, selectedAnnotationValues]);

  // Remove a specific filter chip
  const removeFilter = useCallback((index) => {
    setActiveFilters((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // Clear all filters
  const clearAllFilters = useCallback(() => {
    setActiveFilters([]);
    setSelectedGroup(null);
    setSelectedAnnotationKey("");
    setSelectedAnnotationValues([]);
    setRawExpression("");
  }, []);

  // Select a group (clears annotation filters)
  const selectGroup = useCallback((groupName) => {
    if (groupName === selectedGroup) {
      setSelectedGroup(null);
    } else {
      setSelectedGroup(groupName);
      setActiveFilters([]);
    }
  }, [selectedGroup]);

  // Construct the final filter expression
  const filterExpression = (() => {
    if (advancedMode && rawExpression.trim()) {
      return rawExpression.trim();
    }

    // Annotation filters → filter expression
    if (activeFilters.length > 0) {
      // Group by key for OR within same key
      const byKey = {};
      activeFilters.forEach((f) => {
        if (!byKey[f.key]) byKey[f.key] = [];
        byKey[f.key].push(f.value);
      });

      // Build expression: annotation.key = "val1" OR annotation.key = "val2"
      // Multiple keys are ANDed
      const parts = Object.entries(byKey).map(([key, values]) => {
        if (values.length === 1) {
          return `annotation.${key} = "${values[0]}"`;
        }
        const orParts = values.map((v) => `annotation.${key} = "${v}"`);
        return `(${orParts.join(" OR ")})`;
      });

      return parts.join(" AND ");
    }

    return null;
  })();

  // Get the group name to pass (for X-Ray group-based filtering)
  const groupName = selectedGroup || null;

  // Available annotation keys (sorted)
  const annotationKeys = Object.keys(annotations).sort();

  // Values for currently selected key
  const annotationValues = selectedAnnotationKey
    ? (annotations[selectedAnnotationKey] || [])
    : [];

  return {
    // Annotation discovery
    annotations,
    annotationKeys,
    annotationValues,
    annotationsLoading,
    annotationsError,
    refreshAnnotations,

    // Groups
    groups,
    groupsLoading,
    selectedGroup,
    selectGroup,

    // Annotation selection
    selectedAnnotationKey,
    setSelectedAnnotationKey,
    selectedAnnotationValues,
    toggleAnnotationValue,
    addAnnotationFilter,

    // Active filters
    activeFilters,
    removeFilter,
    clearAllFilters,

    // Advanced mode
    advancedMode,
    setAdvancedMode,
    rawExpression,
    setRawExpression,

    // Computed
    filterExpression,
    groupName,
  };
}
