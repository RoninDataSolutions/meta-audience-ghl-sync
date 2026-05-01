import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import settings

logger = logging.getLogger(__name__)


def _send_email(subject: str, html_body: str):
    """Send an email via SMTP."""
    if not settings.SMTP_HOST or not settings.SMTP_TO_EMAIL:
        logger.warning("SMTP not configured, skipping email")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = settings.SMTP_TO_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    try:
        if settings.SMTP_PORT == 465:
            # SSL from the start (e.g. Zoho, some Gmail configs)
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
                if settings.SMTP_USERNAME:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM_EMAIL, settings.SMTP_TO_EMAIL, msg.as_string())
        else:
            # STARTTLS (port 587 default)
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if settings.SMTP_USERNAME:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM_EMAIL, settings.SMTP_TO_EMAIL, msg.as_string())
        logger.info(f"Email sent: {subject}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        raise


def send_success_email(sync_run) -> None:
    """Send sync success notification."""
    stats = sync_run.normalization_stats or {}
    dist = stats.get("distribution", [0] * 10)
    top_10 = dist[9] if len(dist) > 9 else 0
    middle_50 = sum(dist[3:8]) if len(dist) >= 8 else 0
    bottom_40 = sum(dist[0:4]) if len(dist) >= 4 else 0
    match_rate = (
        round(sync_run.contacts_matched / sync_run.contacts_processed * 100, 1)
        if sync_run.contacts_processed > 0
        else 0
    )

    subject = f"GHL Meta Sync Successful - {sync_run.completed_at.strftime('%Y-%m-%d')}"
    html = f"""
    <h2>Sync completed successfully</h2>
    <p>Completed at: {sync_run.completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
    <h3>Summary</h3>
    <ul>
        <li>Contacts Processed: {sync_run.contacts_processed}</li>
        <li>Contacts Matched by Meta: {sync_run.contacts_matched} ({match_rate}%)</li>
        <li>Custom Audience: {sync_run.meta_audience_name} (ID: {sync_run.meta_audience_id})</li>
        <li>Lookalike Audience: {sync_run.meta_lookalike_name} (ID: {sync_run.meta_lookalike_id})</li>
    </ul>
    <h3>Value Distribution</h3>
    <ul>
        <li>Top 10%: {top_10} contacts</li>
        <li>Middle 50%: {middle_50} contacts</li>
        <li>Bottom 40%: {bottom_40} contacts</li>
    </ul>
    """
    _send_email(subject, html)


def send_failure_email(sync_run, error_message: str) -> None:
    """Send sync failure notification."""
    subject = f"GHL Meta Sync Failed - {sync_run.started_at.strftime('%Y-%m-%d')}"
    html = f"""
    <h2>Sync failed</h2>
    <p>Failed at: {sync_run.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
    <h3>Error</h3>
    <p style="color: red;">{error_message}</p>
    <h3>Details</h3>
    <ul>
        <li>Contacts Retrieved: {sync_run.contacts_processed}</li>
    </ul>
    <p>Please check the application and retry manually.</p>
    """
    _send_email(subject, html)


def send_audit_email(
    account_id: str,
    account_name: str,
    report_id: int,
    metrics: dict,
    pdf_bytes: bytes | None,
    pdf_filename: str | None,
) -> None:
    """Send audit completion email with PDF attachment."""
    from email.mime.application import MIMEApplication

    to_email = settings.AUDIT_EMAIL_TO or settings.SMTP_TO_EMAIL
    if not settings.SMTP_HOST or not to_email:
        logger.warning("SMTP not configured, skipping audit email")
        return

    spend_7d = metrics.get("total_spend_7d", 0) or 0
    spend_30d = metrics.get("total_spend_30d", 0) or 0
    conv_30d = metrics.get("total_conversions_30d", 0) or 0
    cpa_30d = metrics.get("avg_cpa_30d")
    roas_30d = metrics.get("avg_roas_30d")

    subject = f"Meta Audit Report — {account_name} — {__import__('datetime').date.today()}"
    html = f"""
    <h2>Meta Ad Account Audit Complete</h2>
    <p><strong>Account:</strong> {account_name} ({account_id})</p>
    <h3>30-Day Summary</h3>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Metric</th><th>7 Days</th><th>30 Days</th></tr>
        <tr><td>Spend</td><td>${spend_7d:,.2f}</td><td>${spend_30d:,.2f}</td></tr>
        <tr><td>Conversions</td><td>{metrics.get('total_conversions_7d', '—')}</td><td>{conv_30d}</td></tr>
        <tr><td>CPA</td><td>—</td><td>${cpa_30d:,.2f if cpa_30d else '—'}</td></tr>
        <tr><td>ROAS</td><td>—</td><td>{f'{roas_30d:.2f}x' if roas_30d else '—'}</td></tr>
    </table>
    <p>Full report attached as PDF. Report ID: {report_id}</p>
    """

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    if pdf_bytes and pdf_filename:
        attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
        attachment.add_header("Content-Disposition", "attachment", filename=pdf_filename)
        msg.attach(attachment)

    try:
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
                if settings.SMTP_USERNAME:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM_EMAIL, to_email, msg.as_string())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if settings.SMTP_USERNAME:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM_EMAIL, to_email, msg.as_string())
        logger.info(f"Audit email sent: {subject}")
    except Exception as e:
        logger.error(f"Failed to send audit email: {e}")
        raise


def send_test_email() -> None:
    """Send a test email to verify SMTP configuration."""
    subject = "GHL Meta Sync - Test Email"
    html = """
    <h2>Test Email</h2>
    <p>Your SMTP configuration is working correctly.</p>
    <p>You will receive notifications at this address for sync results.</p>
    """
    _send_email(subject, html)
