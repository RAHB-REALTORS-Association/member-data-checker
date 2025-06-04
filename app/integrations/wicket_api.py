import os
import requests
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WICKET_API_BASE_URL = os.environ.get("WICKET_API_BASE_URL", "https://api.examplewicket.com/v1") # Replace with actual URL
WICKET_API_TOKEN = os.environ.get("WICKET_API_TOKEN")

def _get_auth_headers():
    if not WICKET_API_TOKEN:
        logger.error("WICKET_API_TOKEN is not set. Cannot authenticate with Wicket API.")
        raise ValueError("WICKET_API_TOKEN is not configured.")
    return {"Authorization": f"Bearer {WICKET_API_TOKEN}"}

def check_wicket_api_health():
    """Checks the health of the Wicket API."""
    try:
        response = requests.get(f"{WICKET_API_BASE_URL}/health", headers=_get_auth_headers(), timeout=10) # Assuming a /health endpoint
        response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)
        logger.info("Wicket API health check successful.")
        return True, "Wicket API is healthy."
    except requests.exceptions.RequestException as e:
        logger.error(f"Wicket API health check failed: {e}")
        return False, f"Wicket API health check failed: {e}"
    except ValueError as e: # Handles missing token
         return False, str(e)


def get_active_members():
    """
    Fetches active members (name and RECO number) from the Wicket API.

    Returns:
        list: A list of dictionaries, where each dictionary contains
              'name' and 'reco_number' for an active member.
              Returns an empty list if an error occurs or no members are found.
    """
    if not WICKET_API_TOKEN:
         logger.error("Cannot fetch active members: WICKET_API_TOKEN is not set.")
         return []

    try:
        headers = _get_auth_headers()
        # Assuming an endpoint like /members and parameters to filter active ones
        # Also assuming the API returns RECO numbers directly.
        # This will likely need adjustment based on the actual Wicket API structure.
        params = {"status": "active", "fields": "name,recoNumber"} # Example params
        response = requests.get(f"{WICKET_API_BASE_URL}/members", headers=headers, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()
        members = []
        # The structure of the response needs to be known. Assuming it's a list of member objects.
        # e.g., data = {"members": [{"name": "John Doe", "recoNumber": "12345"}, ...]}
        for member_data in data.get("members", []): # Adjust .get("members", []) based on actual API response
            if member_data.get("name") and member_data.get("recoNumber"):
                members.append({
                    "name": member_data["name"],
                    "reco_number": member_data["recoNumber"]
                })
            else:
                logger.warning(f"Incomplete member data received: {member_data}")

        logger.info(f"Successfully fetched {len(members)} active members from Wicket.")
        return members

    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error fetching active members from Wicket: {e.response.status_code} - {e.response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching active members from Wicket: {e}")
    except ValueError as e: # Catches JSON decoding errors or missing token
        logger.error(f"Error processing Wicket API response: {e}")

    return []

if __name__ == '__main__':
    # This is for basic testing of the module directly.
    # You would need to set WICKET_API_TOKEN and potentially WICKET_API_BASE_URL environment variables to test.

    print("Checking Wicket API Health...")
    healthy, message = check_wicket_api_health()
    print(f"Health: {healthy}, Message: {message}")

    if healthy and WICKET_API_TOKEN: # Only try to get members if healthy and token is present
        print("\nFetching active members...")
        active_members = get_active_members()
        if active_members:
            print(f"Found {len(active_members)} members:")
            for member in active_members[:5]: # Print first 5 members as a sample
                print(f"  Name: {member['name']}, RECO Number: {member['reco_number']}")
        else:
            print("No active members found or an error occurred.")
    elif not WICKET_API_TOKEN:
        print("\nSkipped fetching active members because WICKET_API_TOKEN is not set.")
