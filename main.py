from flask import Flask, request, jsonify
import requests
import json
import os

# --- Configuration (Environment Variables) ---
BOT_ID = os.environ.get("GROUPME_BOT_ID")
GROUP_ID = os.environ.get("GROUPME_GROUP_ID")
ACCESS_TOKEN = os.environ.get("GROUPME_ACCESS_TOKEN")
BOT_NAME = "All"

app = Flask(__name__)

# --- Helper Functions ---
def get_group_members():
    """Retrieves the list of members in the specified GroupMe group."""
    url = f"https://api.groupme.com/v3/groups/{GROUP_ID}"
    params = {"token": ACCESS_TOKEN}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raise an exception for bad status codes
        data = response.json()
        return data['response']['members']
    except requests.exceptions.RequestException as e:
        print(f"Error fetching group members: {e}")
        return None

def send_message(text, attachments=None):
    """Sends a message to the GroupMe group via the bot."""
    if BOT_ID is None:
        print("Error: GROUPME_BOT_ID environment variable not set.")
        return

    url = "https://api.groupme.com/v3/bots/post"
    data = {
        "bot_id": BOT_ID,
        "text": text,
        "attachments": attachments if attachments else []
    }
    try:
        response = requests.post(url, data=json.dumps(data))
        response.raise_for_status()
        print(f"Bot '{BOT_NAME}' sent message: '{text[:20]}...'")
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")

def process_message(message_data):
    """Processes incoming messages to check for the '@all' command."""
    if message_data['sender_type'] != 'bot':  # Avoid the bot replying to itself
        text = message_data.get('text', '').lower()
        if text == "@all":
            members = get_group_members()
            if members:
                mentions = []
                mention_text = ""
                for member in members:
                    user_id = member['user_id']
                    nickname = member['nickname']
                    mentions.append({'loci': [[len(mention_text), len(nickname) + 1]], 'user_ids': [user_id]})
                    mention_text += f"@{nickname} "

                if mentions:
                    send_message(mention_text.strip(), attachments=[{'type': 'mentions', 'loci': [loc for m in mentions for loc in m['loci']], 'user_ids': [uid for m in mentions for uid in m['user_ids']]}])
                else:
                    send_message("No members found in this group.")

@app.route('/', methods=['POST'])
def receive_message():
    """Webhook endpoint to receive GroupMe messages."""
    data = request.get_json()
    process_message(data)
    return "OK", 200

if __name__ == '__main__':
    app.run(host='10.0.0.33', port=5000, debug=True)