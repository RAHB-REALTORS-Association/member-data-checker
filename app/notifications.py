import os
import logging
import time
import json
import sqlite3
from sendgrid import SendGridAPIClient # Ensure sendgrid is imported
from sendgrid.helpers.mail import Mail # Ensure Mail is imported
from app.database import get_db_connection, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
NOTIFY_EMAIL_TO = os.environ.get("NOTIFY_EMAIL_TO", "cindy@example.com")
NOTIFY_EMAIL_FROM = os.environ.get("NOTIFY_EMAIL_FROM", "noreply@mdc.example.com")

# Renamed function to indicate DB usage and accept db_conn
def send_notification_for_lapsed_licenses_db(newly_flagged_members: list, db_conn_passed=None):
    if not SENDGRID_API_KEY:
        logger.error("SendGrid API Key not configured. Cannot send notifications.")
        return False, "SendGrid API Key not configured"

    if not newly_flagged_members:
        logger.info("No new members flagged for notification.")
        return True, "No new members to notify"

    sg = SendGridAPIClient(SENDGRID_API_KEY)

    subject = f"MDC Alert: {len(newly_flagged_members)} Member(s) with License Issues"

    html_content_parts = ["<p>The following members have been flagged with license issues by the Member Data Checker:</p><ul>"]
    for member in newly_flagged_members: # Ensure member dict has expected keys from core_logic
        html_content_parts.append(
            f"<li><b>Name:</b> {member.get('name', 'N/A')}<br>"
            f"<b>RECO Number:</b> {member.get('reco_number', 'N/A')}<br>"
            f"<b>Reported Status:</b> {member.get('status_reported_by_reco', 'N/A')}<br>"
            f"<b>Checked On:</b> {time.ctime(member.get('last_checked_reco', time.time()))}</li>"
        )
    html_content_parts.append("</ul><p>Please review these records in the MDC application.</p>")
    html_content = "".join(html_content_parts)

    message = Mail(
        from_email=NOTIFY_EMAIL_FROM,
        to_emails=NOTIFY_EMAIL_TO,
        subject=subject,
        html_content=html_content
    )

    # Determine if we own the connection or if it was passed in
    conn_provided = bool(db_conn_passed)
    conn = db_conn_passed if conn_provided else get_db_connection()

    try:
        response = sg.send(message)
        logger.info(f"Notification email sent to {NOTIFY_EMAIL_TO}. Status Code: {response.status_code}")

        if response.status_code in [200, 202]: # 202 is accepted by SendGrid
            cursor = conn.cursor()
            notification_details_str = json.dumps({
                "to": NOTIFY_EMAIL_TO,
                "subject": subject,
                "status_code": response.status_code,
                "sendgrid_response_headers": dict(response.headers), # Store headers for reference
                "body_sample": html_content[:250] # Store a sample of the body
            })
            notification_sent_time = time.time()
            updated_rows_count = 0
            for member_notified in newly_flagged_members:
                # Update the alert in the DB with notification timestamp and details
                cursor.execute("""
                    UPDATE alerts
                    SET notification_sent_timestamp = ?, notification_details = ?
                    WHERE reco_number = ?
                """, (notification_sent_time, notification_details_str, member_notified['reco_number']))
                updated_rows_count += cursor.rowcount

            if not conn_provided: # If we created the connection, we commit and close
                conn.commit()

            logger.info(f"{updated_rows_count} alert(s) updated in DB with notification status.")
            return True, f"Notification sent successfully. Status: {response.status_code}. Alerts updated: {updated_rows_count}"
        else:
            logger.error(f"Failed to send notification email. Status Code: {response.status_code}, Body: {response.body}")
            # Do not rollback here if conn was provided, let caller handle it.
            return False, f"Failed to send notification. Status: {response.status_code}, Body: {response.body}"

    except sqlite3.Error as db_err:
        logger.error(f"SQLite error updating notification status: {db_err}", exc_info=True)
        if not conn_provided and conn: conn.rollback() # Rollback if we own the connection
        return False, f"DB error after sending email: {db_err}"
    except Exception as e: # Catch other errors like SendGrid issues after DB ops or general errors
        logger.error(f"Error sending notification email or recording status: {e}", exc_info=True)
        if not conn_provided and conn:
             try: conn.rollback() # Attempt rollback if we own the connection
             except sqlite3.Error as rb_err: logger.error(f"Rollback failed during general exception handling: {rb_err}")
        return False, f"Error sending/recording notification: {str(e)}"
    finally:
        if not conn_provided and conn: # Close only if we created it
            conn.close()

if __name__ == '__main__':
    # This __main__ block needs to be updated to reflect DB usage
    # For direct testing, it would need to:
    # 1. Ensure DB is initialized (init_db())
    # 2. Potentially insert dummy alerts into the DB to simulate a state
    # 3. Call send_notification_for_lapsed_licenses_db with those dummy alerts
    # 4. Check the DB to see if notification_sent_timestamp and details were updated.

    init_db() # Make sure tables exist
    logger.info("Testing SendGrid Notification System with DB integration...")

    if not SENDGRID_API_KEY or not NOTIFY_EMAIL_TO or not NOTIFY_EMAIL_FROM:
        logger.warning("Please set SENDGRID_API_KEY, NOTIFY_EMAIL_TO, and NOTIFY_EMAIL_FROM environment variables to test.")
    else:
        logger.info(f"Attempting to send a test notification to: {NOTIFY_EMAIL_TO}")

        # Setup a temporary DB connection for this test
        test_conn = get_db_connection()
        test_cursor = test_conn.cursor()

        # Create dummy data similar to what core_logic would provide
        current_ts = time.time()
        dummy_flagged_members_for_db_test = [
            {
                "name": "DB Test Member One (Lapsed)", "reco_number": "DBTEST001",
                "status_reported_by_reco": "inactive", "last_checked_reco": current_ts - 3600,
                "first_flagged_timestamp": current_ts - 7200, "last_flagged_timestamp": current_ts - 3600
            },
            {
                "name": "DB Test Member Two (Not Found)", "reco_number": "DBTEST002",
                "status_reported_by_reco": "not_found", "last_checked_reco": current_ts - 1800,
                "first_flagged_timestamp": current_ts - 3000, "last_flagged_timestamp": current_ts - 1800
            }
        ]

        # Insert these dummy alerts into the DB (or update if they exist)
        for member in dummy_flagged_members_for_db_test:
            test_cursor.execute("""
                INSERT INTO alerts (reco_number, name, status_reported_by_reco, last_checked_reco, first_flagged_timestamp, last_flagged_timestamp, notification_sent_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(reco_number) DO UPDATE SET
                    name = excluded.name,
                    status_reported_by_reco = excluded.status_reported_by_reco,
                    last_checked_reco = excluded.last_checked_reco,
                    first_flagged_timestamp = excluded.first_flagged_timestamp,
                    last_flagged_timestamp = excluded.last_flagged_timestamp,
                    notification_sent_timestamp = NULL
            """, (member['reco_number'], member['name'], member['status_reported_by_reco'], member['last_checked_reco'], member['first_flagged_timestamp'], member['last_flagged_timestamp']))
        test_conn.commit()
        logger.info(f"{len(dummy_flagged_members_for_db_test)} dummy alerts prepared in DB for notification test.")

        # Call the function, passing the connection so it operates in the same transaction
        success, message = send_notification_for_lapsed_licenses_db(dummy_flagged_members_for_db_test, test_conn)

        if success:
            logger.info(f"Test notification attempt successful: {message}")
            # Check if alerts were updated in the DB
            for member in dummy_flagged_members_for_db_test:
                test_cursor.execute("SELECT notification_sent_timestamp, notification_details FROM alerts WHERE reco_number = ?", (member['reco_number'],))
                row = test_cursor.fetchone()
                if row and row['notification_sent_timestamp']:
                    logger.info(f"  RECO {member['reco_number']} DB record updated. Notified at: {time.ctime(row['notification_sent_timestamp'])}")
                    logger.info(f"    Details: {row['notification_details']}")
                else:
                    logger.warning(f"  RECO {member['reco_number']} DB record NOT updated with notification time.")
        else:
            logger.error(f"Test notification attempt failed: {message}")
            # If failed, perhaps rollback changes made by this test script?
            # test_conn.rollback() # Or just close without commit if only this script modified.

        test_conn.close()
