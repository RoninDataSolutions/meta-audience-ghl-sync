import { useState } from "react";
import { sendTestEmail } from "../api";

interface Props {
  smtpFrom: string;
  smtpTo: string;
}

export default function EmailSettings({ smtpFrom, smtpTo }: Props) {
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);

  const handleTest = async () => {
    setSending(true);
    setMessage(null);
    try {
      await sendTestEmail();
      setMessage({ type: "success", text: "Test email sent successfully!" });
    } catch (e: any) {
      setMessage({ type: "error", text: e.message });
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="card">
      <h2>Email Notifications</h2>
      <div className="form-group">
        <label>From</label>
        <input type="text" value={smtpFrom || "Not configured"} readOnly />
      </div>
      <div className="form-group">
        <label>To</label>
        <input type="text" value={smtpTo || "Not configured"} readOnly />
      </div>
      {message && (
        <div className={message.type === "success" ? "success-msg" : "error-msg"}>
          {message.text}
        </div>
      )}
      <button
        className="btn btn-secondary"
        onClick={handleTest}
        disabled={sending}
      >
        {sending ? "Sending..." : "Send Test Email"}
      </button>
    </div>
  );
}
