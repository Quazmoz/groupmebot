# ==============================================================================
#                           Imports
# ==============================================================================
import os
import requests  # For making HTTP requests to the GroupMe API
import json      # For handling JSON data
import logging   # For better logging
from flask import Flask, request, Response # Flask for the web server framework
import traceback # For detailed exception logging

# ==============================================================================
#                           Logging Setup
# ==============================================================================
# Configure logging to output to console, which Azure App Service captures.
# Using a more detailed format.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
)

# ==============================================================================
#                           Flask App Initialization
# ==============================================================================
# Initialize the Flask application
app = Flask(__name__)

# ==============================================================================
#                           Configuration (Environment Variables)
# ==============================================================================
# Load configuration from environment variables.
# These MUST be set in your Azure App Service configuration.
BOT_ID = os.environ.get('GROUPME_BOT_ID')
GROUP_ID = os.environ.get('GROUPME_GROUP_ID')
ACCESS_TOKEN = os.environ.get('GROUPME_ACCESS_TOKEN') # Needed to fetch group members

# New: Blacklist configuration
# Expects a comma-separated string of GroupMe User IDs
BLACKLISTED_USER_IDS_STR = os.environ.get('GROUPME_BLACKLIST_USER_IDS', '') # Default to empty string
BLACKLISTED_USER_IDS = set(BLACKLISTED_USER_IDS_STR.split(',')) if BLACKLISTED_USER_IDS_STR else set()

# GroupMe API Base URL
GROUPME_API_URL = 'https://api.groupme.com/v3'

# --- Configuration Validation & Logging ---
if not BOT_ID:
    logging.warning("Environment variable GROUPME_BOT_ID is not set.")
if not GROUP_ID:
    logging.warning("Environment variable GROUPME_GROUP_ID is not set.")
if not ACCESS_TOKEN:
    logging.warning("Environment variable GROUPME_ACCESS_TOKEN is not set. Member fetching will fail.")
if BLACKLISTED_USER_IDS:
    logging.info(f"Blacklisted User IDs loaded: {BLACKLISTED_USER_IDS}")
else:
    logging.info("No blacklisted User IDs configured or found.")

# ==============================================================================
#                           Helper Functions
# ==============================================================================

def get_group_members():
    """
    Fetches all members of the specified GroupMe group.

    Requires GROUPME_ACCESS_TOKEN and GROUPME_GROUP_ID to be set.

    Returns:
        list: A list of member objects (dictionaries), or None if an error occurs.
    """
    if not ACCESS_TOKEN or not GROUP_ID:
        logging.error("CRITICAL: GROUPME_ACCESS_TOKEN or GROUPME_GROUP_ID not configured. Cannot fetch members.")
        return None

    url = f"{GROUPME_API_URL}/groups/{GROUP_ID}?token={ACCESS_TOKEN}"
    logging.info(f"Attempting to fetch group members from GroupMe API for group ID: {GROUP_ID}")

    try:
        response = requests.get(url, timeout=15)
        logging.info(f"Group members fetch response status: {response.status_code}")
        response.raise_for_status()
        group_info = response.json()
        members = group_info.get('response', {}).get('members', [])
        logging.info(f"Successfully fetched {len(members)} members raw from API.")
        if members:
            logging.debug(f"First few members (debug, raw): {json.dumps(members[:2], indent=2)}")
        return members
    except requests.exceptions.Timeout:
        logging.error("Error fetching group members: Request timed out.")
        return None
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error fetching group members: {http_err}")
        logging.error(f"Response content: {http_err.response.text}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Generic error fetching group members: {e}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON response from GroupMe API (members fetch): {e}")
        return None

def send_bot_message(text, attachments=None):
    """
    Sends a message from the bot to the GroupMe group.

    Args:
        text (str): The text content of the message.
        attachments (list, optional): A list of GroupMe attachment objects.

    Returns:
        bool: True if the message was sent successfully (2xx status), False otherwise.
    """
    if not BOT_ID:
        logging.error("CRITICAL: GROUPME_BOT_ID not configured. Cannot send message.")
        return False

    url = f"{GROUPME_API_URL}/bots/post"
    
    payload = {'bot_id': BOT_ID, 'text': text}
    if attachments:
        payload['attachments'] = attachments

    logging.info(f"Attempting to send message. Bot ID: {BOT_ID}, URL: {url}")
    logging.info(f"Payload being sent to GroupMe: {json.dumps(payload)}")

    try:
        response = requests.post(url, json=payload, timeout=15)
        logging.info(
            f"GroupMe API response. Status: {response.status_code}, "
            f"Headers: {response.headers}, Body: {response.text}"
        )

        if 200 <= response.status_code < 300:
            logging.info(f"Successfully sent message (received {response.status_code}). Text: '{text[:70]}...'")
            return True
        else:
            logging.error(
                f"Error sending message. GroupMe API returned non-2xx status. "
                f"Status: {response.status_code}, Response: {response.text}"
            )
            return False
    except requests.exceptions.Timeout:
        logging.error("Error sending message: Request timed out.")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error posting message via bot: {e}")
        return False

# ==============================================================================
#                           Flask Routes
# ==============================================================================

@app.route('/', methods=['POST'])
def webhook():
    """
    Handles incoming POST requests from GroupMe.
    Checks for '@all' command and triggers mentions if found, respecting blacklist.
    """
    try:
        data = request.get_json()
        logging.info(f"Received webhook data: {json.dumps(data)}")

        if not data:
            logging.warning("Webhook received empty data.")
            return Response(status=204)

        if data.get('sender_type') == 'bot':
            logging.info(f"Ignoring message from bot (sender_type: 'bot', name: {data.get('name')}).")
            return Response(status=200)

        message_text = data.get('text', '').strip()
        sender_name = data.get('name', 'Someone')
        sender_id = data.get('sender_id')

        if '@all' in message_text.lower():
            logging.info(f"Detected '@all' command from {sender_name} (User ID: {sender_id}). Message: '{message_text}'")

            if not BOT_ID or not GROUP_ID or not ACCESS_TOKEN:
                logging.error("Bot is not fully configured (BOT_ID, GROUP_ID, or ACCESS_TOKEN missing). Cannot process @all.")
                if BOT_ID:
                    send_bot_message(f"Sorry @{sender_name}, I'm not fully configured to handle @all right now.")
                return Response("Bot configuration incomplete", status=500)

            all_members = get_group_members()
            if not all_members:
                logging.error("Failed to get group members. Cannot proceed with @all.")
                send_bot_message(f"Sorry @{sender_name}, I couldn't fetch group members for the @all command.")
                return Response("Failed to fetch group members", status=500)

            # Filter members based on the blacklist
            members_to_mention = []
            for member in all_members:
                if member.get('user_id') in BLACKLISTED_USER_IDS:
                    logging.info(f"User {member.get('nickname')} (ID: {member.get('user_id')}) is blacklisted. Skipping.")
                    continue
                # Optional: Avoid self-mentioning the sender of '@all' if desired
                # if member.get('user_id') == sender_id:
                #    logging.info(f"Skipping sender {sender_name} from @all mention as per configuration (optional).")
                #    continue
                members_to_mention.append(member)
            
            logging.info(f"Total members fetched: {len(all_members)}. Members to mention after blacklist: {len(members_to_mention)}.")

            if not members_to_mention:
                logging.info("No users to mention after filtering (empty group or all members blacklisted/filtered).")
                send_bot_message(f"@{sender_name}, there was no one to mention with @all after applying filters.")
                return Response(status=200)

            mention_intro = f"Message from: @{sender_name} to all\n"
            
            # Refined Loci Calculation:
            running_offset = len(mention_intro) 
            
            final_mention_text_parts = [mention_intro.strip()] # Start with the intro, no leading/trailing whitespace for this part
            processed_user_ids_for_attachment = []
            loci_data_for_attachment = []

            for member in members_to_mention:
                nickname_mention_str = f"@{member['nickname']}"
                final_mention_text_parts.append(nickname_mention_str)

                loci_data_for_attachment.append([running_offset, len(nickname_mention_str)])
                processed_user_ids_for_attachment.append(member['user_id'])

                running_offset += len(nickname_mention_str) + 1 # +1 for the space
            
            final_message_text = " ".join(final_mention_text_parts) # Join intro and all @nickname parts

            mention_attachment = {
                "type": "mentions",
                "user_ids": processed_user_ids_for_attachment,
                "loci": loci_data_for_attachment
            }

            logging.info(f"Final mention text (first 150 chars): {final_message_text[:150]}...")
            logging.info(f"Final mention attachment: {json.dumps(mention_attachment)}")

            if not send_bot_message(final_message_text, [mention_attachment]):
                logging.error("send_bot_message returned False. The @all message might not have been delivered.")
                return Response("Failed to send mention message via bot", status=500)
            else:
                logging.info("send_bot_message returned True. GroupMe API likely accepted the @all message.")

        else:
            logging.info(f"No '@all' command detected in message: '{message_text}'")

    except Exception as e:
        logging.error(f"An unexpected error occurred in webhook: {e}")
        logging.error(traceback.format_exc())
        return Response("Internal server error during webhook processing", status=500)

    return Response(status=200)


@app.route('/', methods=['GET'])
def health_check():
    logging.info("Health check endpoint '/' accessed via GET.")
    status_message = "GroupMe @all Bot is running."
    issues = []
    if not BOT_ID: issues.append("GROUPME_BOT_ID missing")
    if not GROUP_ID: issues.append("GROUPME_GROUP_ID missing")
    if not ACCESS_TOKEN: issues.append("GROUPME_ACCESS_TOKEN missing")

    if issues:
        status_message += " WARNING: Configuration issues detected: " + ", ".join(issues)
    else:
        status_message += " All essential configurations seem present."
    
    if BLACKLISTED_USER_IDS_STR:
        status_message += f" Blacklist is configured with {len(BLACKLISTED_USER_IDS)} ID(s)."
    else:
        status_message += " No blacklist configured."

    return status_message, 200

# ==============================================================================
#                           Main Execution Block
# ==============================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logging.info(f"Starting Flask development server on host 0.0.0.0 port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

