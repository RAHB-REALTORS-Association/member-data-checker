import logging
import time
import json
import sqlite3
from app.database import get_db_connection, init_db
# Ensure correct import path for integrations and notifications
from app.integrations import wicket_api, reco_api
from app.notifications import send_notification_for_lapsed_licenses_db # Name change reflected here

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    conn_test = get_db_connection()
    if conn_test:
        conn_test.execute("SELECT 1 FROM alerts LIMIT 1").fetchone()
        conn_test.execute("SELECT 1 FROM run_history LIMIT 1").fetchone()
        conn_test.close()
except sqlite3.OperationalError:
    logger.info("core_logic tables not found, running init_db().")
    init_db()
except Exception as e:
    logger.error(f"Failed to check core_logic tables, attempting init_db(): {e}")
    init_db()

def get_last_run_results():
    conn = get_db_connection()
    run_result = {"error": "No run history found.", "summary": {}, "all_processed_members": [], "flagged_this_run": []}
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM run_history ORDER BY run_timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            summary_data = json.loads(row["summary"]) if row["summary"] else {}
            all_processed_data = json.loads(row["all_processed_members_details"]) if row["all_processed_members_details"] else []
            # newly_flagged_members_count is already an int, no need to parse from JSON for flagged_this_run
            # flagged_this_run will be populated by perform_license_validation_sweep or kept empty if just fetching historical
            run_result = {
                "id": row["id"], "timestamp": row["run_timestamp"], "status": row["status"],
                "message": row["message"], "summary": summary_data,
                "all_processed_members": all_processed_data, # This is the detailed list for display
                "newly_flagged_members_count": row["newly_flagged_members_count"], # Count of those flagged in THIS run
                "flagged_this_run": [] # This field is usually populated by the sweep function for immediate use
            }
        return run_result
    except sqlite3.Error as e:
        logger.error(f"Error fetching last run results from DB: {e}")
        run_result["error"] = f"DB error fetching results: {e}"
        return run_result
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from run_history: {e}")
        run_result["error"] = f"JSON decode error from DB: {e}"
        return run_result
    finally:
        if conn: conn.close()

def get_all_alerts():
    conn = get_db_connection()
    alerts_list = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alerts ORDER BY last_flagged_timestamp DESC")
        for row in cursor.fetchall():
            alert_dict = dict(row) # Convert sqlite3.Row to dict
            if alert_dict.get('notification_details'):
                try:
                    alert_dict['notification_details'] = json.loads(alert_dict['notification_details'])
                except json.JSONDecodeError:
                    logger.warning(f"Could not parse notification_details JSON for alert ID {alert_dict.get('id')}")
                    alert_dict['notification_details'] = {"error": "Could not parse details"}
            alerts_list.append(alert_dict)
        return alerts_list
    except sqlite3.Error as e:
        logger.error(f"Error fetching all alerts from DB: {e}")
        return [] # Return empty list on DB error
    finally:
        if conn: conn.close()

def perform_license_validation_sweep():
    logger.info("Starting license validation sweep with SQLite backend...")
    conn = get_db_connection()
    # Initialize run_outcome to a default error state or a structure that get_last_run_results expects
    run_outcome = {"timestamp": time.time(), "status": "error", "message": "Sweep did not complete.", "summary": {}, "flagged_this_run": [], "all_processed_members": []}

    try:
        active_wicket_members = wicket_api.get_active_members()
        current_time_sweep = time.time()

        if not active_wicket_members:
            logger.warning("No active members from Wicket. Aborting sweep.")
            run_data_tuple = (current_time_sweep, "aborted", "No active members from Wicket", json.dumps({}), 0, json.dumps([]))
            cursor = conn.cursor()
            cursor.execute("INSERT INTO run_history (run_timestamp, status, message, summary, newly_flagged_members_count, all_processed_members_details) VALUES (?, ?, ?, ?, ?, ?)", run_data_tuple)
            conn.commit()
            # Use get_last_run_results to ensure consistent return format
            run_outcome = get_last_run_results()
            return run_outcome

        newly_flagged_for_notification = []
        processed_members_for_history = []
        cursor = conn.cursor()

        for member in active_wicket_members:
            reco_number = member.get("reco_number")
            member_name = member.get("name", "N/A")
            overall_status_for_history = "ok"

            if not reco_number:
                logger.warning(f"Member {member_name} missing RECO. Skipping.")
                processed_members_for_history.append({"name": member_name, "reco_number": "MISSING", "wicket_status": "active", "reco_status_details": {"status":"skipped"}, "overall_status": "skipped"})
                continue

            reco_status_details = reco_api.get_license_status(reco_number)

            # This object is for the 'flagged_this_run' part of the response, and for notifications
            alert_obj_for_notification = {
                "name": member_name, "reco_number": reco_number,
                "status_reported_by_reco": reco_status_details['status'],
                "last_checked_reco": reco_status_details.get('last_checked', current_time_sweep),
                "first_flagged_timestamp": current_time_sweep, # Default to now if new
                "last_flagged_timestamp": current_time_sweep,
                # notification_sent_timestamp and details will be added by notification logic
            }

            if reco_status_details['status'] not in ['active', 'error', 'db_error']:
                overall_status_for_history = "flagged"
                cursor.execute("SELECT id, first_flagged_timestamp, notification_sent_timestamp FROM alerts WHERE reco_number = ?", (reco_number,))
                existing_alert_row = cursor.fetchone()
                if existing_alert_row:
                    alert_obj_for_notification["first_flagged_timestamp"] = existing_alert_row["first_flagged_timestamp"]
                    # Reset notification status on re-flagging if it's a persistent issue.
                    # Or, decide if you want to notify again based on time elapsed since last notification.
                    # For now, if it's re-flagged, it becomes a candidate for notification again.
                    cursor.execute("UPDATE alerts SET last_flagged_timestamp = ?, status_reported_by_reco = ?, last_checked_reco = ?, name = ?, notification_sent_timestamp = NULL, notification_details = NULL WHERE reco_number = ?",
                                   (current_time_sweep, reco_status_details['status'], reco_status_details.get('last_checked'), member_name, reco_number))
                else:
                    cursor.execute("INSERT INTO alerts (reco_number, name, status_reported_by_reco, last_checked_reco, first_flagged_timestamp, last_flagged_timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                                   (reco_number, member_name, reco_status_details['status'], reco_status_details.get('last_checked'), current_time_sweep, current_time_sweep))
                newly_flagged_for_notification.append(alert_obj_for_notification) # Add to list for current run's notifications

            elif reco_status_details['status'] == 'active':
                overall_status_for_history = "ok"
                # If member is now active, remove any existing alert for them.
                cursor.execute("DELETE FROM alerts WHERE reco_number = ?", (reco_number,))
                if cursor.rowcount > 0: logger.info(f"Alert for RECO number {reco_number} ({member_name}) cleared as license is now active.")

            elif reco_status_details['status'] in ['error', 'db_error']:
                overall_status_for_history = "error_checking_reco"
                logger.warning(f"Error checking RECO for {member_name} ({reco_number}). Status: {reco_status_details.get('message', reco_status_details['status'])}")

            processed_members_for_history.append({
                "name": member_name, "reco_number": reco_number, "wicket_status": "active", # Assuming all from Wicket are 'active' in Wicket
                "reco_status_details": reco_status_details, # This contains status, source, last_checked from reco_api
                "overall_status": overall_status_for_history
            })

        conn.commit() # Commit changes from alert processing

        # Get count of all alerts currently in the DB for the summary
        all_alerts_from_db = get_all_alerts() # This uses a new connection, consider passing cursor/conn if in one transaction
        current_alert_count_from_db = len(all_alerts_from_db)

        summary_obj = {
            "total_wicket_members_processed": len(active_wicket_members),
            "members_missing_reco": sum(1 for m in processed_members_for_history if m["reco_number"] == "MISSING"),
            "members_ok": sum(1 for m in processed_members_for_history if m["overall_status"] == "ok"),
            "members_flagged_this_run": len(newly_flagged_for_notification), # Count of members added/updated in alerts table in THIS run
            "members_reco_check_error": sum(1 for m in processed_members_for_history if m["overall_status"] == "error_checking_reco"),
            "total_alerts_active": current_alert_count_from_db # Total number of alerts in the DB table
        }
        cursor.execute("INSERT INTO run_history (run_timestamp, status, summary, newly_flagged_members_count, all_processed_members_details) VALUES (?, ?, ?, ?, ?)",
                       (current_time_sweep, "completed", json.dumps(summary_obj), len(newly_flagged_for_notification), json.dumps(processed_members_for_history)))
        conn.commit()

        if newly_flagged_for_notification:
            logger.info(f"Attempting notifications for {len(newly_flagged_for_notification)} newly flagged members.")
            # Pass the DB connection to the notification function to use the same transaction context if needed,
            # or let it handle its own connection. For simplicity, passing the connection.
            notif_success, notif_message = send_notification_for_lapsed_licenses_db(newly_flagged_for_notification, conn)
            if notif_success:
                logger.info(f"Notification process completed: {notif_message}")
                conn.commit() # Commit notification status updates
            else:
                logger.error(f"Notification process failed: {notif_message}")
                conn.rollback() # Rollback notification status updates if sending failed but DB ops were attempted
        else:
            logger.info("No new/updated alerts requiring notification in this sweep.")

        run_outcome = get_last_run_results() # Fetch the full results of this run
        # The 'flagged_this_run' key in run_outcome is for members who were *newly* flagged or re-flagged *in this specific run*.
        # get_last_run_results() doesn't populate this; it's context for the current sweep.
        run_outcome["flagged_this_run"] = newly_flagged_for_notification
        return run_outcome

    except sqlite3.Error as e:
        logger.error(f"SQLite error during license validation sweep: {e}", exc_info=True)
        if conn: conn.rollback()
        # Ensure run_outcome is structured like a normal result but indicates error
        run_outcome = {"timestamp": time.time(), "status": "error", "message": f"Database error during sweep: {e}", "summary": {}, "flagged_this_run": [], "all_processed_members": []}
        return run_outcome
    except Exception as e_gen: # Catch any other unexpected errors
        logger.error(f"General error during license validation sweep: {e_gen}", exc_info=True)
        if conn: conn.rollback() # Rollback any partial DB changes
        run_outcome = {"timestamp": time.time(), "status": "error", "message": f"General error during sweep: {e_gen}", "summary": {}, "flagged_this_run": [], "all_processed_members": []}
        return run_outcome
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    init_db() # Ensure DB is initialized before running test
    logger.info("Performing a license validation sweep with DB (from __main__)...")

    sweep_results = perform_license_validation_sweep()

    logger.info("\n--- Sweep Results Summary (from __main__) ---")
    logger.info(f"Timestamp: {time.ctime(sweep_results.get('timestamp', 0))}")
    logger.info(f"Status: {sweep_results.get('status')}")
    if sweep_results.get("message"):
        logger.info(f"Message: {sweep_results.get('message')}")

    summary_data = sweep_results.get("summary", {})
    if summary_data:
        for key, value in summary_data.items():
            logger.info(f"  {key.replace('_', ' ').capitalize()}: {value}")
    else:
        logger.info("  No summary data in results.")

    logger.info("\n--- Newly Flagged Members This Run (from __main__) ---")
    flagged_this_run = sweep_results.get("flagged_this_run", [])
    if flagged_this_run:
        for member in flagged_this_run:
            logger.info(f"  Name: {member.get('name')}, RECO: {member.get('reco_number')}, Status: {member.get('status_reported_by_reco')}")
    else:
        logger.info("  No members were newly flagged in this run.")

    logger.info("\n--- All Current Alerts from DB (from __main__) ---")
    all_current_alerts = get_all_alerts()
    if all_current_alerts:
        for alert in all_current_alerts:
            logger.info(f"  Name: {alert.get('name')}, RECO: {alert.get('reco_number')}, Status: {alert.get('status_reported_by_reco')}, Last Flagged: {time.ctime(alert.get('last_flagged_timestamp',0))}")
            if alert.get('notification_sent_timestamp'):
                logger.info(f"    Notification Sent: {time.ctime(alert.get('notification_sent_timestamp'))}")
    else:
        logger.info("  No active alerts in the database.")

    logger.info(f"\nRun completed. Check logs and database (instance/mdc_app.sqlite3) for details.")
