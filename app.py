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

# GroupMe API Base URL
GROUPME_API_URL = 'https://api.groupme.com/v3'

# --- Configuration Validation ---
if not BOT_ID:
    logging.warning("Environment variable GROUPME_BOT_ID is not set.")
if not GROUP_ID:
    logging.warning("Environment variable GROUPME_GROUP_ID is not set.")
if not ACCESS_TOKEN:
    logging.warning("Environment variable GROUPME_ACCESS_TOKEN is not set. Member fetching will fail.")

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
        response = requests.get(url, timeout=15) # Increased timeout slightly
        logging.info(f"Group members fetch response status: {response.status_code}")
        response.raise_for_status()
        group_info = response.json()
        members = group_info.get('response', {}).get('members', [])
        logging.info(f"Successfully fetched {len(members)} members.")
        if members: # Log first few members for verification if needed, but be mindful of PII
            logging.debug(f"First few members (debug): {json.dumps(members[:2], indent=2)}")
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
    logging.info(f"Payload being sent to GroupMe: {json.dumps(payload)}") # Log the exact payload

    try:
        response = requests.post(url, json=payload, timeout=15) # Increased timeout
        # Log the full response details, regardless of status code, for better debugging
        logging.info(
            f"GroupMe API response. Status: {response.status_code}, "
            f"Headers: {response.headers}, Body: {response.text}"
        )

        if 200 <= response.status_code < 300: # Typically 202 Accepted
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
    Checks for '@all' command and triggers mentions if found.
    """
    try:
        data = request.get_json()
        logging.info(f"Received webhook data: {json.dumps(data)}")

        if not data:
            logging.warning("Webhook received empty data.")
            return Response(status=204)

        if data.get('sender_type') == 'bot': # Important: check sender_type, not name
            logging.info(f"Ignoring message from bot (sender_type: 'bot', name: {data.get('name')}).")
            return Response(status=200)

        message_text = data.get('text', '').strip()
        sender_name = data.get('name', 'Someone')
        sender_id = data.get('sender_id') # User ID of the person who sent the message

        if '@all' in message_text.lower(): # Case-insensitive check
            logging.info(f"Detected '@all' command from {sender_name} (User ID: {sender_id}). Message: '{message_text}'")

            if not BOT_ID or not GROUP_ID or not ACCESS_TOKEN:
                logging.error("Bot is not fully configured (BOT_ID, GROUP_ID, or ACCESS_TOKEN missing). Cannot process @all.")
                # Optionally send a message back if BOT_ID is available
                if BOT_ID:
                    send_bot_message(f"Sorry @{sender_name}, I'm not fully configured to handle @all right now.")
                return Response("Bot configuration incomplete", status=500)

            members = get_group_members()
            if not members:
                logging.error("Failed to get group members. Cannot proceed with @all.")
                send_bot_message(f"Sorry @{sender_name}, I couldn't fetch group members for the @all command.")
                return Response("Failed to fetch group members", status=500)

            mention_intro = f"@{sender_name} summoned everyone!\n"
            user_ids_to_mention = []
            loci_data = []
            
            # Text construction: Intro + space-separated @Nicknames
            current_text_parts = [mention_intro.strip()] # Start with the intro

            for member in members:
                # Optional: Avoid mentioning the bot itself if it appears in member list
                # (though GroupMe usually handles this)
                # if member['user_id'] == BOT_USER_ID: # BOT_USER_ID would be bot's actual user ID, not BOT_ID
                #    continue

                # Optional: Avoid self-mentioning the sender of '@all' if desired
                # if member['user_id'] == sender_id:
                #    continue

                nickname_mention = f"@{member['nickname']}"
                current_text_parts.append(nickname_mention)
                user_ids_to_mention.append(member['user_id'])

            # Combine all text parts to form the final message text
            final_message_text = " ".join(current_text_parts)
            
            # Calculate loci based on the final_message_text
            # The first mention starts after the intro phrase and a space
            current_pos = len(mention_intro) # Start after the intro line (includes its trailing space if any)
                                            # If intro is "User summoned!\n", next mention is after that.

            # Re-iterate to build loci based on the *final* text structure
            # Start from the first actual @Nickname, which is after the intro.
            # The intro itself is not a "mention" in the attachment.
            temp_text_for_loci_calc = mention_intro 
            for member in members: # Assuming same order as user_ids_to_mention
                # This part needs to be precise: loci are for @nickname in the *final* text
                nickname_to_find = f"@{member['nickname']}"
                
                # Find the start of this specific nickname_mention in the *final_message_text*
                # starting from *after* the intro part.
                # This is tricky if nicknames are substrings of each other.
                # A more robust way is to build loci as we build the string.
                
                # Let's rebuild loci more carefully:
                # loci_data is already being built below, let's refine that loop.
                pass # Placeholder, main loci logic is below.

            # Refined Loci Calculation:
            # Iterate through the constructed text parts to build loci accurately.
            # The `mention_intro` is the prefix. Mentions start after it.
            running_offset = len(mention_intro) # Start of the first actual mention part
            
            # Clear and rebuild loci_data
            loci_data = []
            processed_user_ids = [] # To match with user_ids_to_mention if filtering applied

            # Iterate through members again to build loci based on the final text structure
            temp_mention_text_parts = []
            for member in members:
                # Apply same filtering as above if any (e.g. skip sender, skip bot)
                # For now, assuming all members fetched are mentioned
                
                nickname_mention_str = f"@{member['nickname']}"
                temp_mention_text_parts.append(nickname_mention_str) # For joining later

                # Loci: [start_index_in_final_string, length_of_this_mention_string]
                # The start_index is relative to the beginning of `final_message_text`
                loci_data.append([running_offset, len(nickname_mention_str)])
                processed_user_ids.append(member['user_id']) # ensure this matches user_ids_to_mention

                # Update running_offset for the next mention: length of current + 1 for the space
                running_offset += len(nickname_mention_str) + 1
            
            # Construct the final text by joining the intro and the nickname parts
            final_message_text = mention_intro + " ".join(temp_mention_text_parts)


            if not processed_user_ids:
                logging.info("No users to mention after filtering (or empty group).")
                # send_bot_message(f"@{sender_name}, there was no one to mention with @all.")
                return Response(status=200) # Or handle as an error if preferred

            mention_attachment = {
                "type": "mentions",
                "user_ids": processed_user_ids, # Use the list of user_ids that correspond to the loci
                "loci": loci_data
            }

            logging.info(f"Final mention text (first 150 chars): {final_message_text[:150]}...")
            logging.info(f"Final mention attachment: {json.dumps(mention_attachment)}")

            if not send_bot_message(final_message_text, [mention_attachment]):
                logging.error("send_bot_message returned False. The @all message might not have been delivered.")
                # Potentially send a simpler message to the user if this fails
                # send_bot_message(f"@{sender_name}, I tried to send the @all but encountered an issue.")
                return Response("Failed to send mention message via bot", status=500)
            else:
                logging.info("send_bot_message returned True. GroupMe API likely accepted the @all message.")

        else:
            logging.info(f"No '@all' command detected in message: '{message_text}'")

    except Exception as e:
        logging.error(f"An unexpected error occurred in webhook: {e}")
        logging.error(traceback.format_exc()) # Log full traceback
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
        return status_message, 200 # Still 200, but with warning
    else:
        status_message += " All essential configurations seem present."
        return status_message, 200

# ==============================================================================
#                           Main Execution Block
# ==============================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logging.info(f"Starting Flask development server on host 0.0.0.0 port {port}")
    # Use debug=False for production-like testing, True for active development
    # Azure App Service will use Gunicorn specified in the Startup Command, not this.
    app.run(host='0.0.0.0', port=port, debug=False)

