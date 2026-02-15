import { useState, useEffect } from "react";
import type { SyncRunDetail } from "../types";
import { getSyncDetail } from "../api";

interface Props {
  runId: number;
  onClose: () => void;
}

export default function SyncDetailModal({ runId, onClose }: Props) {
  const [detail, setDetail] = useState<SyncRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getSyncDetail(runId)
      .then(setDetail)
      .catch((e) => setError(e.message));
  }, [runId]);

  if (error) {
    return (
      <div className="modal-overlay" onClick={onClose}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            <h2>Error</h2>
            <button className="btn-close" onClick={onClose}>
              &times;
            </button>
          </div>
          <div className="error-msg">{error}</div>
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="modal-overlay">
        <div className="modal">
          <p>Loading...</p>
        </div>
      </div>
    );
  }

  const stats = detail.normalization_stats;
  const duration = detail.duration_seconds
    ? `${Math.round(detail.duration_seconds)}s`
    : "—";
  const matchRate =
    detail.contacts_processed > 0
      ? ((detail.contacts_matched / detail.contacts_processed) * 100).toFixed(1)
      : "—";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Sync Run #{detail.id}</h2>
          <button className="btn-close" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-body">
          <div className="detail-grid">
            <div>
              <strong>Status:</strong>{" "}
              <span className={`badge ${detail.status}`}>{detail.status}</span>
            </div>
            <div>
              <strong>Started:</strong>{" "}
              {detail.started_at
                ? new Date(detail.started_at).toLocaleString()
                : "—"}
            </div>
            <div>
              <strong>Completed:</strong>{" "}
              {detail.completed_at
                ? new Date(detail.completed_at).toLocaleString()
                : "—"}
            </div>
            <div>
              <strong>Duration:</strong> {duration}
            </div>
            <div>
              <strong>Contacts Processed:</strong> {detail.contacts_processed}
            </div>
            <div>
              <strong>Contacts Matched:</strong> {detail.contacts_matched} (
              {matchRate}%)
            </div>
            <div>
              <strong>Audience:</strong>{" "}
              {detail.meta_audience_name || "—"} (
              {detail.meta_audience_id || "—"})
            </div>
            <div>
              <strong>Lookalike:</strong>{" "}
              {detail.meta_lookalike_name || "—"} (
              {detail.meta_lookalike_id || "—"})
            </div>
          </div>

          {stats && (
            <div className="stats-section">
              <h3>Normalization Stats</h3>
              <div className="detail-grid">
                <div>
                  <strong>Min LTV:</strong> ${stats.min_ltv.toFixed(2)}
                </div>
                <div>
                  <strong>Max LTV:</strong> ${stats.max_ltv.toFixed(2)}
                </div>
                <div>
                  <strong>Median LTV:</strong> ${stats.median_ltv.toFixed(2)}
                </div>
                <div>
                  <strong>Total Contacts:</strong> {stats.count}
                </div>
              </div>
            </div>
          )}

          {detail.error_message && (
            <div className="error-section">
              <h3>Error</h3>
              <pre className="error-msg">{detail.error_message}</pre>
            </div>
          )}

          {detail.contact_samples.length > 0 && (
            <div className="samples-section">
              <h3>Sample Contacts (first 10)</h3>
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Raw LTV</th>
                    <th>Normalized</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.contact_samples.map((c, i) => (
                    <tr key={i}>
                      <td>
                        {c.first_name} {c.last_name}
                      </td>
                      <td>{c.email || "—"}</td>
                      <td>${c.raw_ltv.toFixed(2)}</td>
                      <td>{c.normalized_value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
