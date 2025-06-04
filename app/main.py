import os
import datetime
import json
from flask import Flask, jsonify, request, render_template
from app.database import init_db, get_db_connection
from app.core_logic import perform_license_validation_sweep, get_last_run_results, get_all_alerts
from app.notifications import send_notification_for_lapsed_licenses_db
from app.integrations.wicket_api import check_wicket_api_health

IS_TEST_ENVIRONMENT = os.environ.get("FLASK_TESTING", "false").lower() == "true"

app = Flask(__name__)
# Ensure init_db is called once when the application starts.
# Putting it here means it runs when main.py is imported or run.
init_db()

def format_datetime_filter(value, format_str='%Y-%m-%d %H:%M:%S'): # Renamed format to format_str
    if value is None: return "N/A"
    if isinstance(value, (int, float)):
        try: return datetime.datetime.fromtimestamp(value).strftime(format_str)
        except (TypeError, ValueError): return str(value)
    return str(value)

app.jinja_env.filters['format_datetime'] = format_datetime_filter

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/check-members', methods=['GET', 'POST'])
def check_members_route():
    app.logger.info("Received request to /check-members. Triggering sweep.")
    results = perform_license_validation_sweep()
    app.logger.info("/check-members sweep completed.")
    return render_template('_results_table.html', results=results)

@app.route('/results', methods=['GET'])
def get_results_route():
    app.logger.info("Received request for /results (HTML partial).")
    results_data = get_last_run_results()
    return render_template('_results_table.html', results=results_data)

@app.route('/alerts', methods=['GET'])
def get_alerts_route():
    app.logger.info("Received request for /alerts (HTML partial).")
    alerts_data = get_all_alerts()
    return render_template('_alerts_table.html', alerts=alerts_data)

@app.route('/wicket-api-health', methods=['GET'])
def wicket_api_health_route():
    app.logger.info("Received request for /wicket-api-health (HTML partial).")
    if IS_TEST_ENVIRONMENT and not os.environ.get("WICKET_API_TOKEN"):
        app.logger.warning("WICKET_API_TOKEN not set for Wicket health check in test env.")
    healthy, message = check_wicket_api_health()
    return render_template('_wicket_health.html', health_status={"wicket_api_healthy": healthy, "message": message})

@app.route('/resend-alert', methods=['POST'])
def resend_alert_route():
    app.logger.info("Received request to /resend-alert (DB version).")
    data = request.get_json()
    reco_to_resend = "" # Initialize to ensure it's defined
    conn = None # Initialize conn
    final_alert_status_dict = None # Initialize

    try:
        if not data or "reco_number" not in data:
            # For HTMX, returning an HTML snippet is better than just text/JSON for errors
            return f"<tr id='alert-row-error'><td colspan='7' class='status-error'>Missing 'reco_number' in request.</td></tr>", 400

        reco_to_resend = data["reco_number"]
        conn = get_db_connection()
        cursor = conn.cursor()
        # Fetch the alert details from the database to pass to the notification function
        cursor.execute("SELECT * FROM alerts WHERE reco_number = ?", (reco_to_resend,))
        alert_row_from_db = cursor.fetchone()

        if not alert_row_from_db:
            # Ensure reco_to_resend is safe for HTML attribute if it contains quotes
            safe_reco_number_attr = reco_to_resend.replace('"', '&quot;')
            return f"<tr id='alert-row-{safe_reco_number_attr}'><td colspan='7' class='status-error'>No active alert for RECO: {reco_to_resend}</td></tr>", 404

        # Convert sqlite3.Row to a dictionary for easier manipulation and to pass to notification function
        alert_to_resend_original = dict(alert_row_from_db)
        # If notification_details is stored as JSON string, parse it (optional, depends on needs)
        if alert_to_resend_original.get('notification_details') and isinstance(alert_to_resend_original.get('notification_details'), str):
            try:
                alert_to_resend_original['notification_details'] = json.loads(alert_to_resend_original['notification_details'])
            except json.JSONDecodeError:
                logger.warning(f"Could not parse notification_details for {reco_to_resend} during resend prep.")
                alert_to_resend_original['notification_details'] = None # Or some error indicator

        # No need to keep the connection open to reco_api's DB during the SendGrid call
        conn.close()
        conn = None

        app.logger.info(f"Attempting to resend notification for RECO: {reco_to_resend}")
        # The send_notification_for_lapsed_licenses_db function now handles its own DB connection by default
        success, message = send_notification_for_lapsed_licenses_db([alert_to_resend_original])

        # After notification attempt, fetch the LATEST status of the alert from DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alerts WHERE reco_number = ?", (reco_to_resend,))
        final_alert_row_from_db = cursor.fetchone()

        if final_alert_row_from_db:
            final_alert_status_dict = dict(final_alert_row_from_db)
            if final_alert_status_dict.get('notification_details') and isinstance(final_alert_status_dict.get('notification_details'), str):
                try:
                    final_alert_status_dict['notification_details'] = json.loads(final_alert_status_dict['notification_details'])
                except json.JSONDecodeError:
                     final_alert_status_dict['notification_details'] = {"error": "parse failed"}
            final_alert_status_dict['message_after_resend'] = message # Add the outcome message for display
        else:
            # Alert might have been cleared by another process between sending and re-fetching.
            # Use the original data (pre-send attempt) for rendering, but indicate it might be gone.
            final_alert_status_dict = alert_to_resend_original
            final_alert_status_dict['message_after_resend'] = f"Alert status after send: {message}. (Note: Alert may have been cleared from system)."
            # To prevent error in template if it's truly gone, ensure essential keys.
            final_alert_status_dict.setdefault('name', 'N/A') # if somehow missing
            final_alert_status_dict.setdefault('status_reported_by_reco', 'Unknown - Possibly Cleared')


        template_to_render = '_alert_row.html'
        # If the alert is no longer in the DB after the send attempt (e.g. it became 'active' and was deleted by a concurrent sweep)
        if not final_alert_row_from_db:
            # If the alert is gone, we can't render the standard row. Send a message row.
            safe_reco_number_attr = reco_to_resend.replace('"', '&quot;')
            return f"<tr id='alert-row-{safe_reco_number_attr}'><td colspan='7' class='status-ok'>Alert for {reco_to_resend} processed. Notification status: {message}. Alert may have been cleared.</td></tr>"

        if success:
            app.logger.info(f"Resend successful for RECO {reco_to_resend}: {message}")
        else:
            app.logger.error(f"Resend failed for RECO {reco_to_resend}: {message}")

        return render_template(template_to_render, alert=final_alert_status_dict)

    except Exception as e_main_resend:
        app.logger.error(f"General error in /resend-alert for {reco_to_resend}: {e_main_resend}", exc_info=True)
        error_message_for_row = f"Server error during resend: {str(e_main_resend)[:50]}"

        # Fallback rendering for the row in case of unexpected error
        # Ensure final_alert_status_dict is at least a dict to avoid template errors
        if not isinstance(final_alert_status_dict, dict): # If it's None due to error before its creation
            final_alert_status_dict = {}

        # Populate with minimal data for the template to avoid breaking
        final_alert_status_dict.setdefault('reco_number', reco_to_resend if reco_to_resend else "UnknownRECO")
        final_alert_status_dict.setdefault('name', 'Error Occurred')
        final_alert_status_dict.setdefault('status_reported_by_reco', 'Error')
        final_alert_status_dict.setdefault('first_flagged_timestamp', time.time()) # Default to now
        final_alert_status_dict.setdefault('last_flagged_timestamp', time.time())
        final_alert_status_dict.setdefault('notification_sent_timestamp', None) # No notification sent
        final_alert_status_dict['message_after_resend'] = error_message_for_row # Show the error

        return render_template('_alert_row.html', alert=final_alert_status_dict), 500
    finally:
        if conn: conn.close()

if __name__ == '__main__':
    # Set FLASK_ENV for development to enable debug mode (though app.run(debug=True) also does this)
    # os.environ['FLASK_ENV'] = 'development'
    app.run(debug=True, host='0.0.0.0', port=5001)
