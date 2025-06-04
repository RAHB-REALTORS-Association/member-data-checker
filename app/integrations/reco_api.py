import os
import requests
import logging
import time
import json
import sqlite3
from app.database import get_db_connection, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RECO_API_BASE_URL = os.environ.get("RECO_API_BASE_URL", "https://api.reco.on.ca/registrantsearch/api/v2/registrants")
RECO_API_KEY = os.environ.get("RECO_API_KEY")
CACHE_EXPIRY_SECONDS = 24 * 60 * 60

try:
    conn_test = get_db_connection()
    if conn_test:
        conn_test.execute("SELECT 1 FROM reco_cache LIMIT 1").fetchone()
        conn_test.close()
except sqlite3.OperationalError:
    logger.info("reco_cache table not found in reco_api.py, running init_db().")
    init_db()
except Exception as e:
    logger.error(f"Failed to check reco_cache, attempting init_db(): {e}")
    init_db()

def get_license_status(reco_number: str):
    if not reco_number:
        return {'status': 'error', 'message': 'RECO number cannot be empty', 'last_checked': time.time(), 'source': 'internal'}

    conn = get_db_connection()
    cursor = conn.cursor()
    current_time = time.time()

    try:
        cursor.execute("SELECT status, timestamp, raw_response FROM reco_cache WHERE reco_number = ?", (reco_number,))
        cached_row = cursor.fetchone()

        if cached_row:
            if current_time - cached_row['timestamp'] < CACHE_EXPIRY_SECONDS:
                logger.info(f"RECO {reco_number} from SQLite cache. Status: {cached_row['status']}")
                return {
                    'status': cached_row['status'],
                    'last_checked': cached_row['timestamp'],
                    'source': 'cache',
                    'raw_response': json.loads(cached_row['raw_response']) if cached_row['raw_response'] else None
                }
            else:
                logger.info(f"RECO {reco_number} in cache but expired.")

        logger.info(f"Fetching status for RECO {reco_number} from API.")
        status_from_api = 'error'
        api_response_data = None
        headers = {}
        if RECO_API_KEY:
            headers["X-Api-Key"] = RECO_API_KEY

        try:
            params = {"registrationNumber": reco_number}
            # Ensure requests is imported: import requests
            response = requests.get(RECO_API_BASE_URL, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            api_response_data = response.json()

            # Placeholder parsing logic from original function (adjust if needed)
            if isinstance(api_response_data, list) and len(api_response_data) > 0:
                registrant_info = api_response_data[0]
                api_status_str = registrant_info.get("statusDescription", "").lower()
                if "active" in api_status_str: status_from_api = "active"
                elif any(s in api_status_str for s in ["terminated", "expired", "suspended"]): status_from_api = "inactive"
                else: status_from_api = "not_found"; logger.warning(f"Unrecognized status '{api_status_str}' for {reco_number}")
            elif isinstance(api_response_data, dict) and api_response_data.get("items") is not None and len(api_response_data["items"]) > 0:
                registrant_info = api_response_data["items"][0]
                api_status_str = registrant_info.get("statusDescription", "").lower()
                if "active" in api_status_str: status_from_api = "active"
                elif any(s in api_status_str for s in ["terminated", "expired", "suspended"]): status_from_api = "inactive"
                else: status_from_api = "not_found"; logger.warning(f"Unrecognized status '{api_status_str}' for {reco_number}")
            else: status_from_api = "not_found"
            # End of placeholder parsing logic
            logger.info(f"RECO API response for {reco_number}: Status '{status_from_api}'")

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404: status_from_api = "not_found"; logger.info(f"RECO {reco_number} not found via API (404).")
            else: status_from_api = "error"; logger.error(f"HTTP error for {reco_number}: {e.response.status_code} - {e.response.text}"); api_response_data = {"error": e.response.text, "status_code": e.response.status_code}
        except requests.exceptions.RequestException as e:
            status_from_api = "error"; logger.error(f"Request error for {reco_number}: {e}"); api_response_data = {"error": str(e)}
        except ValueError as e:
            status_from_api = "error"; logger.error(f"Error decoding JSON for {reco_number}: {e}"); api_response_data = {"error": "Invalid JSON response"}

        raw_response_str = json.dumps(api_response_data) if api_response_data is not None else None
        cursor.execute('''
            INSERT INTO reco_cache (reco_number, status, timestamp, raw_response)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(reco_number) DO UPDATE SET
            status = excluded.status,
            timestamp = excluded.timestamp,
            raw_response = excluded.raw_response
        ''', (reco_number, status_from_api, current_time, raw_response_str))
        conn.commit()

        return {'status': status_from_api, 'last_checked': current_time, 'source': 'api', 'raw_response': api_response_data}

    except sqlite3.Error as e:
        logger.error(f"SQLite error for RECO {reco_number} in get_license_status: {e}")
        return {'status': 'db_error', 'message': f'SQLite error: {e}', 'last_checked': current_time, 'source': 'internal_db_error'}
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    init_db()
    logger.info(f"DB file located at: app/instance/mdc_app.sqlite3 (expected path from database.py)")
    test_reco_numbers = ["12345", "67890", "12345"]
    for num in test_reco_numbers:
        logger.info(f"Validating RECO number: {num}") # Changed print to logger.info
        result = get_license_status(num)
        logger.info(f"Status: {result['status']}")
        logger.info(f"Source: {result['source']}")
        logger.info(f"Last Checked: {time.ctime(result['last_checked'])}")


def check_reco_registration_status(reco_number: str):
    """
    Checks the registration status of a RECO number.

    Args:
        reco_number: The RECO number to check.

    Returns:
        "REGISTERED" if the RECO number is registered, "INACTIVE_OR_INVALID" otherwise.
    """
    if not reco_number:
        logger.error("RECO number cannot be empty.")
        return "INACTIVE_OR_INVALID"

    url = f"https://registrantsearch.reco.on.ca/api/Registrant/{reco_number}"
    logger.info(f"Checking RECO registration status for {reco_number} at {url}")

    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error during request to RECO API for {reco_number}: {e}")
        return "INACTIVE_OR_INVALID"

    try:
        data = response.json()
    except ValueError as e: # Catches JSONDecodeError as ValueError is its parent
        logger.error(f"Error parsing JSON response from RECO API for {reco_number}: {e}")
        logger.debug(f"Response text: {response.text}")
        return "INACTIVE_OR_INVALID"

    if data.get("registrationStatus") == "REGISTERED":
        logger.info(f"RECO number {reco_number} is REGISTERED.")
        return "REGISTERED"
    else:
        logger.info(f"RECO number {reco_number} is INACTIVE_OR_INVALID or status not found. Status: {data.get('registrationStatus')}")
        return "INACTIVE_OR_INVALID"
