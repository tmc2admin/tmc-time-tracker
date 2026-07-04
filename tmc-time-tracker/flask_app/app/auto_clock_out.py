import requests
import os
import time
import logging
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# --- Configuration ---
# Load .env file for local development if it exists
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Get credentials and URL from Environment Variables ---
# In Azure, these will be set in App Service -> Configuration -> Application settings
WEBJOB_USERNAME = os.getenv('WEBJOB_USERNAME')
WEBJOB_PASSWORD = os.getenv('WEBJOB_PASSWORD')
# Ensure you have a trailing slash if your base URL does, and no double slashes
BASE_API_URL = os.getenv('FLASK_API_BASE_URL', 'https://tmc-time-tracker-a8aba8cxfpdwfseq.westeurope-01.azurewebsites.net')
API_ENDPOINT = "/admin/api/enforce_auto_clock_out"
FULL_API_URL = f"{BASE_API_URL.rstrip('/')}{API_ENDPOINT}"

def make_authenticated_request(url, max_retries=3):
    """
    Make an authenticated POST request with retry logic.
    """
    if not all([WEBJOB_USERNAME, WEBJOB_PASSWORD]):
        logging.error("FATAL: WEBJOB_USERNAME or WEBJOB_PASSWORD environment variables not set.")
        return None

    for attempt in range(max_retries):
        try:
            logging.info(f"Attempt {attempt + 1}: Calling API endpoint at {url}")
            response = requests.post(
                url=url,
                auth=HTTPBasicAuth(WEBJOB_USERNAME, WEBJOB_PASSWORD),
                timeout=60  # 60 second timeout for potentially long cleanup
            )
            
            # Check for successful status codes
            if response.status_code == 200:
                logging.info(f"Success! Status: {response.status_code}")
                return response
            else:
                logging.warning(f"Attempt {attempt + 1} failed. Status: {response.status_code}, Response: {response.text}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff: 1, 2, 4 seconds
                
        except requests.exceptions.RequestException as e:
            logging.error(f"Attempt {attempt + 1} encountered a network error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    
    return None

def main():
    """Main execution function for the WebJob."""
    logging.info("Starting auto clock-out enforcement job.")
    
    response = make_authenticated_request(FULL_API_URL)
    
    if response:
        logging.info("Job executed successfully.")
        try:
            logging.info(f"Response JSON: {response.json()}")
        except Exception:
            logging.info(f"Response Text: {response.text}")
    else:
        logging.error("Job failed after multiple retries.")

if __name__ == "__main__":
    main()