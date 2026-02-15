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
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            if settings.SMTP_PORT != 25:
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


def send_test_email() -> None:
    """Send a test email to verify SMTP configuration."""
    subject = "GHL Meta Sync - Test Email"
    html = """
    <h2>Test Email</h2>
    <p>Your SMTP configuration is working correctly.</p>
    <p>You will receive notifications at this address for sync results.</p>
    """
    _send_email(subject, html)
