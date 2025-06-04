import unittest
import json
from unittest.mock import patch
from app.main import app # Import the Flask app instance

class TestRecoEndpoint(unittest.TestCase):

    def setUp(self):
        """Set up test client for each test."""
        app.testing = True  # Enable testing mode
        self.client = app.test_client()
        # Ensure the app context is pushed if your app uses it during setup
        # self.app_context = app.app_context()
        # self.app_context.push()

    def tearDown(self):
        """Clean up after each test."""
        # if self.app_context:
        #     self.app_context.pop()
        pass

    @patch('app.main.check_reco_registration_status') # Patch where it's used in main.py
    def test_reco_status_registered(self, mock_check_status):
        """Test /reco/status/<reco_number> when registrant is REGISTERED."""
        mock_check_status.return_value = "REGISTERED"

        response = self.client.get('/reco/status/12345')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, 'application/json')

        data = json.loads(response.data.decode('utf-8'))
        self.assertEqual(data.get("registrationStatus"), "REGISTERED")
        mock_check_status.assert_called_once_with("12345")

    @patch('app.main.check_reco_registration_status') # Patch where it's used in main.py
    def test_reco_status_inactive_or_invalid(self, mock_check_status):
        """Test /reco/status/<reco_number> when registrant is INACTIVE_OR_INVALID."""
        mock_check_status.return_value = "INACTIVE_OR_INVALID"

        response = self.client.get('/reco/status/67890')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, 'application/json')

        data = json.loads(response.data.decode('utf-8'))
        self.assertEqual(data.get("registrationStatus"), "INACTIVE_OR_INVALID")
        mock_check_status.assert_called_once_with("67890")

    # Example of how you might test for an empty RECO number if the endpoint handled it specifically
    # (though in our current design, check_reco_registration_status handles empty input,
    # so the endpoint will still return what that function returns)
    @patch('app.main.check_reco_registration_status')
    def test_reco_status_empty_reco_number(self, mock_check_status):
        """Test /reco/status/ with an effectively empty RECO number if needed,
           or how the current system handles it via the mocked function."""
        # Let's assume check_reco_registration_status itself handles empty strings
        # by returning "INACTIVE_OR_INVALID" as per its implementation.
        mock_check_status.return_value = "INACTIVE_OR_INVALID" # Simulating behavior for empty/bad input

        # Test with an empty string path segment if the route could match it,
        # or a placeholder that signifies an invalid RECO number.
        # Flask might not route an empty segment as a path parameter by default.
        # Let's use a placeholder like "invalid_reco" for the test path.
        response = self.client.get('/reco/status/invalid_reco_placeholder')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, 'application/json')
        data = json.loads(response.data.decode('utf-8'))
        self.assertEqual(data.get("registrationStatus"), "INACTIVE_OR_INVALID")
        mock_check_status.assert_called_once_with("invalid_reco_placeholder")


if __name__ == '__main__':
    unittest.main()
