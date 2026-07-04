import requests
import os
import time
import logging
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# --- Configuration ---
# Load .env file for local development if it exists
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - [ZombieCleaner] - %(levelname)s - %(message)s'
)

# --- Get credentials and URL from Environment Variables ---
WEBJOB_USERNAME = os.getenv('WEBJOB_USERNAME')
WEBJOB_PASSWORD = os.getenv('WEBJOB_PASSWORD')

# Default to Azure URL if not set locally
BASE_API_URL = os.getenv(
    'FLASK_API_BASE_URL', 
    'https://tmc-time-tracker-a8aba8cxfpdwfseq.westeurope-01.azurewebsites.net'
)

# [IMPORTANT] Ensure this matches the route defined in your api.py
# If your api_bp has url_prefix='/api', this should be:
API_ENDPOINT = "/api/admin/cleanup_zombie_sessions" 
# If your previous code worked with /admin/api/..., check your Blueprint setup.

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
                timeout=60  # 60 second timeout for potentially long database updates
            )
            
            # Check for successful status codes (200 OK)
            if response.status_code == 200:
                logging.info(f"Success! Status: {response.status_code}")
                return response
            else:
                logging.warning(
                    f"Attempt {attempt + 1} failed. "
                    f"Status: {response.status_code}, Response: {response.text}"
                )
                
                # Exponential backoff: 2s, 4s, 8s...
                if attempt < max_retries - 1:
                    sleep_time = 2 ** (attempt + 1)
                    logging.info(f"Retrying in {sleep_time} seconds...")
                    time.sleep(sleep_time)
                
        except requests.exceptions.RequestException as e:
            logging.error(f"Attempt {attempt + 1} encountered a network error: {e}")
            if attempt < max_retries - 1:
                sleep_time = 2 ** (attempt + 1)
                time.sleep(sleep_time)
    
    return None

def main():
    """Main execution function for the Zombie Cleaner WebJob."""
    logging.info("Starting zombie session cleanup job...")
    
    response = make_authenticated_request(FULL_API_URL)
    
    if response:
        logging.info("Job executed successfully.")
        try:
            data = response.json()
            logging.info(f"Result: {data.get('message', 'No message')} | Sessions Cleaned: {data.get('sessions_cleaned', 0)}")
        except Exception:
            logging.info(f"Response Text: {response.text}")
    else:
        logging.error("Job failed after multiple retries.")
        # Exit with error code so Azure knows the job failed
        exit(1)

if __name__ == "__main__":
    main()