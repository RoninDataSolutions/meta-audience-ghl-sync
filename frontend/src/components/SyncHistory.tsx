import type { SyncRun } from "../types";

interface Props {
  runs: SyncRun[];
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  onViewDetail: (id: number) => void;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const min = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60);
  return `${min}m ${sec}s`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function SyncHistory({
  runs,
  page,
  totalPages,
  onPageChange,
  onViewDetail,
}: Props) {
  return (
    <div className="card">
      <h2>Sync History</h2>
      {runs.length === 0 ? (
        <p className="muted">No sync runs yet.</p>
      ) : (
        <>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Status</th>
                  <th>Contacts</th>
                  <th>Matched</th>
                  <th>Match Rate</th>
                  <th>Audience</th>
                  <th>Duration</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const rate =
                    run.contacts_processed > 0
                      ? (
                          (run.contacts_matched / run.contacts_processed) *
                          100
                        ).toFixed(1)
                      : "—";
                  return (
                    <tr
                      key={run.id}
                      className={run.status === "failed" ? "row-failed" : run.status === "warning" ? "row-warning" : ""}
                    >
                      <td>{formatDate(run.started_at)}</td>
                      <td>
                        <span className={`badge ${run.status}`}>
                          {run.status}
                        </span>
                      </td>
                      <td>{run.contacts_processed}</td>
                      <td>{run.contacts_matched}</td>
                      <td>{rate}%</td>
                      <td>{run.meta_audience_name || "—"}</td>
                      <td>{formatDuration(run.duration_seconds)}</td>
                      <td>
                        <button
                          className="btn btn-sm"
                          onClick={() => onViewDetail(run.id)}
                        >
                          Details
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="pagination">
              <button
                className="btn btn-sm"
                disabled={page <= 1}
                onClick={() => onPageChange(page - 1)}
              >
                Prev
              </button>
              <span>
                Page {page} of {totalPages}
              </span>
              <button
                className="btn btn-sm"
                disabled={page >= totalPages}
                onClick={() => onPageChange(page + 1)}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
