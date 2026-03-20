import { useState } from "react";

interface NewScanModalProps {
  onSubmit: (path: string) => void;
  onCancel: () => void;
  loading: boolean;
}

export function NewScanModal({ onSubmit, onCancel, loading }: NewScanModalProps) {
  const [path, setPath] = useState("/workspace");

  return (
    <div className="apme-modal-overlay" onClick={onCancel}>
      <div className="apme-modal" onClick={(e) => e.stopPropagation()}>
        <h2>New Scan</h2>
        <label style={{ fontSize: 14, color: "#8a8d90", marginBottom: 8, display: "block" }}>
          Path inside the gateway container (host workspace is mounted at /workspace)
        </label>
        <input
          type="text"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="/workspace/my-project"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === "Enter" && path.trim()) onSubmit(path.trim());
          }}
        />
        <div className="apme-modal-actions">
          <button className="apme-btn apme-btn-secondary" onClick={onCancel} disabled={loading}>
            Cancel
          </button>
          <button
            className="apme-btn apme-btn-primary"
            onClick={() => path.trim() && onSubmit(path.trim())}
            disabled={!path.trim() || loading}
          >
            {loading ? "Scanning..." : "Scan"}
          </button>
        </div>
      </div>
    </div>
  );
}
