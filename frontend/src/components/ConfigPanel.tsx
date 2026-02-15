import { useState, useEffect } from "react";
import type { SyncConfig, CustomField } from "../types";
import { getCustomFields, saveConfig } from "../api";

interface Props {
  config: SyncConfig | null;
  onSaved: () => void;
}

export default function ConfigPanel({ config, onSaved }: Props) {
  const [customFields, setCustomFields] = useState<CustomField[]>([]);
  const [selectedFieldKey, setSelectedFieldKey] = useState("");
  const [selectedFieldName, setSelectedFieldName] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCustomFields()
      .then((fields) => {
        setCustomFields(fields);
        if (config) {
          setSelectedFieldKey(config.ghl_ltv_field_key);
          setSelectedFieldName(config.ghl_ltv_field_name);
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [config]);

  const handleSave = async () => {
    if (!selectedFieldKey) return;
    setSaving(true);
    setError(null);
    try {
      await saveConfig({
        ghl_ltv_field_key: selectedFieldKey,
        ghl_ltv_field_name: selectedFieldName,
      });
      onSaved();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="card">Loading configuration...</div>;

  return (
    <div className="card">
      <h2>Configuration</h2>
      {error && <div className="error-msg">{error}</div>}
      <div className="form-group">
        <label>LTV Custom Field</label>
        <select
          value={selectedFieldKey}
          onChange={(e) => {
            setSelectedFieldKey(e.target.value);
            const field = customFields.find(
              (f) => (f.fieldKey || f.id) === e.target.value
            );
            setSelectedFieldName(field?.name || "");
          }}
        >
          <option value="">Select LTV field...</option>
          {customFields.map((f) => (
            <option key={f.id} value={f.fieldKey || f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </div>
      <p className="muted">
        All contacts with a non-empty value in this field will be synced.
      </p>
      <button
        className="btn btn-primary"
        onClick={handleSave}
        disabled={saving || !selectedFieldKey}
      >
        {saving ? "Saving..." : "Save Configuration"}
      </button>
    </div>
  );
}
