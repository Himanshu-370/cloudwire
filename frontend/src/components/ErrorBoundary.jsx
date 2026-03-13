import React from "react";

const styles = {
  container: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "2rem",
    minHeight: "120px",
    background: "#0d1b2a",
    border: "1px solid #1b2e40",
    borderRadius: "6px",
    color: "#7a9aaa",
    textAlign: "center",
    gap: "0.75rem",
  },
  heading: {
    margin: 0,
    fontSize: "0.95rem",
    fontWeight: 600,
    color: "#ff9900",
  },
  message: {
    margin: 0,
    fontSize: "0.8rem",
    color: "#7a9aaa",
    maxWidth: "360px",
    lineHeight: 1.5,
  },
  button: {
    marginTop: "0.25rem",
    padding: "0.35rem 1rem",
    fontSize: "0.78rem",
    fontWeight: 600,
    color: "#0d1b2a",
    background: "#ff9900",
    border: "none",
    borderRadius: "4px",
    cursor: "pointer",
  },
};

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, errorCount: 0 };
    this._autoResetTimer = null;
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    console.error(`[ErrorBoundary:${this.props.name || "unknown"}]`, error, info);
    // Auto-recover after a short delay — the next render with updated data
    // usually succeeds (e.g., scan polling provides fresh graph data).
    this.setState((prev) => ({ errorCount: prev.errorCount + 1 }));
    clearTimeout(this._autoResetTimer);
    this._autoResetTimer = setTimeout(() => {
      if (this.state.hasError && this.state.errorCount < 4) {
        this.setState({ hasError: false });
      }
    }, 800);
  }

  componentDidUpdate(prevProps) {
    // Reset error state when the resetKey prop changes (i.e., new data arrived).
    if (this.state.hasError && this.props.resetKey !== undefined && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, errorCount: 0 });
    }
  }

  componentWillUnmount() {
    clearTimeout(this._autoResetTimer);
  }

  handleReset = () => {
    this.setState({ hasError: false, errorCount: 0 });
  };

  render() {
    if (this.state.hasError) {
      const name = this.props.name || "Component";
      return (
        <div style={styles.container}>
          <p style={styles.heading}>{name} crashed</p>
          <p style={styles.message}>
            Something went wrong rendering this panel. The rest of the app should still work.
          </p>
          <button style={styles.button} onClick={this.handleReset}>
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
