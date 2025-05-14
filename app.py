# ==============================================================================
#                           Imports
# ==============================================================================
import os
import requests  # For making HTTP requests to the GroupMe API
import json      # For handling JSON data
from flask import Flask, request, Response # Flask for the web server framework

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

# ==============================================================================
#                           Helper Functions
# ==============================================================================

def get_group_members():
    """
    Fetches all members of the specified GroupMe group.

    Requires GROUPME_ACCESS_TOKEN to be set.

    Returns:
        list: A list of member objects (dictionaries), or None if an error occurs.
              Each member object contains keys like 'user_id', 'nickname', 'id' (membership_id).
    """
    if not ACCESS_TOKEN or not GROUP_ID:
        print("Error: GROUPME_ACCESS_TOKEN or GROUPME_GROUP_ID not configured.")
        return None

    # API endpoint to get group details, including members
    url = f"{GROUPME_API_URL}/groups/{GROUP_ID}?token={ACCESS_TOKEN}"

    try:
        response = requests.get(url)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        group_info = response.json()
        # Extract members from the response
        members = group_info.get('response', {}).get('members', [])
        print(f"Successfully fetched {len(members)} members.")
        return members
    except requests.exceptions.RequestException as e:
        print(f"Error fetching group members: {e}")
        # Log the response text if available for more details
        if hasattr(e, 'response') and e.response is not None:
             print(f"Response Status: {e.response.status_code}")
             print(f"Response Text: {e.response.text}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON response from GroupMe API: {e}")
        return None

def send_bot_message(text, attachments=None):
    """
    Sends a message from the bot to the GroupMe group.

    Args:
        text (str): The text content of the message.
        attachments (list, optional): A list of GroupMe attachment objects.
                                      Defaults to None.

    Returns:
        bool: True if the message was sent successfully, False otherwise.
    """
    if not BOT_ID:
        print("Error: GROUPME_BOT_ID not configured.")
        return False

    # API endpoint for posting messages via a bot
    url = f"{GROUPME_API_URL}/bots/post"

    # Prepare the payload
    payload = {
        'bot_id': BOT_ID,
        'text': text
    }
    if attachments:
        payload['attachments'] = attachments

    try:
        response = requests.post(url, json=payload)
        # GroupMe often returns 202 Accepted for successful posts
        if 200 <= response.status_code < 300:
             print(f"Successfully sent message: '{text[:50]}...'")
             return True
        else:
             print(f"Error sending message. Status: {response.status_code}, Response: {response.text}")
             response.raise_for_status() # Raise an exception for bad status codes
             return False # Should not be reached if raise_for_status works
    except requests.exceptions.RequestException as e:
        print(f"Error posting message via bot: {e}")
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
        # Get the JSON data sent by GroupMe
        data = request.get_json()
        print("Received data:", json.dumps(data, indent=2)) # Log incoming data

        # --- Basic Validation and Checks ---
        if not data:
            print("No data received.")
            return Response(status=204) # No content, but acknowledge receipt

        # Ignore messages sent by bots (including this one) to prevent loops
        if data.get('sender_type') == 'bot':
            print("Ignoring message from bot.")
            return Response(status=200) # OK, but do nothing

        # Check if the message text contains '@all'
        message_text = data.get('text', '').strip()
        sender_name = data.get('name', 'Someone') # Get sender's name for the message

        if '@all' in message_text:
            print(f"Detected '@all' command from {sender_name}.")

            # --- Fetch Group Members ---
            members = get_group_members()
            if not members:
                print("Failed to get group members. Cannot proceed with @all.")
                # Optionally send an error message back? Be careful not to spam.
                # send_bot_message(f"Sorry {sender_name}, I couldn't fetch group members to tag @all.")
                return Response(status=500) # Internal server error

            # --- Prepare Mention Message ---
            mention_text = f"Tagging everyone! (Triggered by @{sender_name})\n"
            user_ids = []
            loci = []

            # Iterate through members to build the text and mention data
            current_pos = len(mention_text) # Start position for the first mention
            for member in members:
                # Don't mention the bot itself or the person who triggered @all
                # Note: GroupMe API might handle self-mentions gracefully, but explicit check is safer.
                # if member['user_id'] == data.get('sender_id'): # Optional: skip sender
                #     continue

                nickname = f"@{member['nickname']}"
                user_id = member['user_id']

                # Add nickname to text
                mention_text += nickname + " "

                # Add user ID to list for attachment
                user_ids.append(user_id)

                # Add loci entry: [start_position, length_of_nickname]
                loci.append([current_pos, len(nickname)])

                # Update current position for the next mention
                current_pos += len(nickname) + 1 # +1 for the space

            # Trim trailing space
            mention_text = mention_text.strip()

            # Create the mention attachment object
            mention_attachment = {
                "type": "mentions",
                "user_ids": user_ids,
                "loci": loci
            }

            # --- Send the Message ---
            print(f"Constructed mention text: {mention_text}")
            print(f"Constructed mention attachment: {json.dumps(mention_attachment)}")

            if not send_bot_message(mention_text, [mention_attachment]):
                print("Failed to send the @all mention message.")
                # Consider how to handle this failure (retry? log?)

        else:
            # Message didn't contain '@all', do nothing
            print("No '@all' command detected.")

    except Exception as e:
        # Catch any unexpected errors during processing
        print(f"An error occurred processing the webhook: {e}")
        # Log the exception traceback for debugging if possible
        import traceback
        traceback.print_exc()
        # Return an error status code
        return Response(status=500)

    # Return a success status code to GroupMe
    # Using 200 OK is generally fine for webhooks
    return Response(status=200)


@app.route('/', methods=['GET'])
def health_check():
    """
    A simple GET endpoint to check if the bot is running.
    Useful for Azure's health checks or manual verification.
    """
    return "GroupMe @all Bot is running.", 200

# ==============================================================================
#                           Main Execution Block
# ==============================================================================
if __name__ == '__main__':
    # Get port from environment variable or default to 5000 for local dev
    port = int(os.environ.get('PORT', 5000))
    # Run the Flask app
    # host='0.0.0.0' makes it accessible externally (needed for containers/deployment)
    # debug=True is useful for development but should be False in production
    app.run(host='0.0.0.0', port=port, debug=False)