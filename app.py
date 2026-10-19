from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from supabase import create_client
from google import genai
import os
import threading
import secrets
import urllib.parse
import urllib.request
import json

app = Flask(__name__)
CORS(app)

# =========================================================
# CONFIGURATION
# =========================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_URL and SUPABASE_KEY must be set in Render Environment."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)


# =========================================================
# DATABASE HELPERS
# =========================================================

def get_group(group_id):
    result = (
        supabase.table("groups")
        .select("id, name, invite_code")
        .eq("id", group_id)
        .execute()
    )

    if result.data:
        return result.data[0]

    return None


def ensure_general_group():
    result = (
        supabase.table("groups")
        .select("id, name, invite_code")
        .eq("invite_code", "general-room")
        .execute()
    )

    if result.data:
        return result.data[0]

    result = (
        supabase.table("groups")
        .insert({
            "name": "General",
            "invite_code": "general-room"
        })
        .execute()
    )

    if not result.data:
        raise RuntimeError("Could not create the General group.")

    return result.data[0]


# =========================================================
# ENGLISH CORRECTION ENGINE
# =========================================================

def correct_english(message):

    # 1. Gemini AI correction
    if client:
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=f"""
You are an expert English teacher.

Correct the user's English sentence for grammar,
tense, spelling, articles, prepositions, word order,
punctuation and natural expression.

Preserve the original meaning.
If the sentence is already correct, return it unchanged.
Return ONLY the corrected sentence.
Do not add explanations, labels or quotation marks.

User sentence:
{message}

Corrected sentence:
"""
            )

            if response.text:
                return response.text.strip()

        except Exception as e:
            print("Gemini correction failed:", repr(e))

    # 2. LanguageTool fallback
    try:
        data = urllib.parse.urlencode({
            "text": message,
            "language": "en-US"
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.languagetool.org/v2/check",
            data=data,
            headers={"User-Agent": "English-AI-Group-Chat"}
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))

        corrected = message

        matches = sorted(
            result.get("matches", []),
            key=lambda item: item["offset"],
            reverse=True
        )

        for match in matches:
            replacements = match.get("replacements", [])

            if replacements:
                start = match["offset"]
                end = start + match["length"]

                corrected = (
                    corrected[:start]
                    + replacements[0]["value"]
                    + corrected[end:]
                )

        if corrected != message:
            return corrected

    except Exception as e:
        print("LanguageTool correction failed:", repr(e))

    # 3. Basic local fallback
    corrected = message.strip()

    replacements = {
        "i am go to college": "I am going to college",
        "i am go college": "I am going to college",
        "i am go": "I am going",
        "i am eat": "I am eating",
        "i am study": "I am studying",
        "he go": "he goes",
        "she go": "she goes",
        "he don't": "he doesn't",
        "she don't": "she doesn't",
        "i is": "I am",
        "i are": "I am",
        "he are": "he is",
        "she are": "she is",
        "they is": "they are",
        "we is": "we are",
        "you is": "you are",
        "a apple": "an apple",
        "a orange": "an orange",
        "a umbrella": "an umbrella",
        "a hour": "an hour"
    }

    lower = corrected.lower()

    if "yesterday" in lower or "last week" in lower:
        past_replacements = {
            " go ": " went ",
            " goes ": " went ",
            " come ": " came ",
            " comes ": " came ",
            " eat ": " ate ",
            " eats ": " ate ",
            " see ": " saw ",
            " sees ": " saw ",
            " do ": " did ",
            " does ": " did ",
            " have ": " had ",
            " has ": " had "
        }

        padded = " " + corrected + " "

        for old, new in past_replacements.items():
            padded = padded.replace(old, new)

        corrected = padded.strip()

    for old, new in replacements.items():
        corrected = corrected.replace(old, new)

    if corrected and corrected[-1] not in ".!?":
        corrected += "."

    return corrected


# =========================================================
# BACKGROUND CORRECTION
# =========================================================

def correct_message_in_background(message_id, message):
    try:
        correction = correct_english(message)

        supabase.table("messages").update({
            "correction": correction
        }).eq("id", message_id).execute()

        print("Correction saved for message:", message_id)

    except Exception as e:
        print("Background correction error:", repr(e))


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    try:
        group = ensure_general_group()

        return render_template(
            "index.html",
            group_id=group["id"],
            group_name=group["name"]
        )

    except Exception as e:
        print("Home route error:", repr(e))
        return "Database connection failed. Please try again later.", 500


# =========================================================
# CREATE GROUP
# =========================================================

@app.route("/create_group", methods=["POST"])
def create_group():
    data = request.get_json(silent=True) or {}
    group_name = (data.get("name") or "").strip()

    if not group_name:
        return jsonify({"error": "Group name is required"}), 400

    if len(group_name) > 100:
        return jsonify({"error": "Group name is too long"}), 400

    try:
        invite_code = secrets.token_urlsafe(16)

        result = (
            supabase.table("groups")
            .insert({
                "name": group_name,
                "invite_code": invite_code
            })
            .execute()
        )

        if not result.data:
            return jsonify({"error": "Could not create group"}), 500

        group = result.data[0]

        return jsonify({
            "group_id": group["id"],
            "group_name": group["name"],
            "invite_link": request.host_url.rstrip("/")
                           + "/join/" + group["invite_code"]
        })

    except Exception as e:
        print("Create group error:", repr(e))
        return jsonify({"error": "Could not create group"}), 500


# =========================================================
# JOIN GROUP
# =========================================================

@app.route("/join/<invite_code>")
def join_group(invite_code):
    try:
        result = (
            supabase.table("groups")
            .select("id, name")
            .eq("invite_code", invite_code)
            .execute()
        )

        if not result.data:
            return "Invalid group invite link", 404

        group = result.data[0]

        return render_template(
            "index.html",
            group_id=group["id"],
            group_name=group["name"]
        )

    except Exception as e:
        print("Join group error:", repr(e))
        return "Could not open group. Please try again later.", 500


# =========================================================
# GET MESSAGES
# =========================================================

@app.route("/messages", methods=["GET"])
def get_messages():
    group_id = request.args.get("group_id", type=int)

    if not group_id:
        return jsonify({"error": "Valid group ID is required"}), 400

    try:
        group = get_group(group_id)

        if group is None:
            return jsonify({"error": "Group not found"}), 404

        result = (
            supabase.table("messages")
            .select("id, username, message, correction")
            .eq("group_id", group_id)
            .order("id")
            .execute()
        )

        return jsonify(result.data or [])

    except Exception as e:
        print("Get messages error:", repr(e))
        return jsonify({"error": "Could not load messages"}), 500


# =========================================================
# SEND MESSAGE
# =========================================================

@app.route("/send", methods=["POST"])
def send_message():
    data = request.get_json(silent=True) or {}

    username = (data.get("username") or "User").strip()
    message = (data.get("message") or "").strip()

    try:
        group_id = int(data.get("group_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Valid group ID is required"}), 400

    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400

    if len(message) > 5000:
        return jsonify({"error": "Message is too long"}), 400

    if not username:
        username = "User"

    try:
        group = get_group(group_id)

        if group is None:
            return jsonify({"error": "Group not found"}), 404

        result = (
            supabase.table("messages")
            .insert({
                "username": username,
                "message": message,
                "correction": None,
                "group_id": group_id
            })
            .execute()
        )

        if not result.data:
            return jsonify({"error": "Message could not be saved"}), 500

        saved_message = result.data[0]
        message_id = saved_message["id"]

        thread = threading.Thread(
            target=correct_message_in_background,
            args=(message_id, message),
            daemon=True
        )
        thread.start()

        return jsonify({
            "status": "success",
            "id": message_id,
            "username": username,
            "message": message,
            "correction": None,
            "group_id": group_id
        })

    except Exception as e:
        print("Send message error:", repr(e))
        return jsonify({"error": "Could not send message"}), 500


# =========================================================
# DIRECT CORRECTION ENDPOINT
# =========================================================

@app.route("/correct", methods=["POST"])
def correct():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"correction": ""})

    try:
        return jsonify({
            "correction": correct_english(message)
        })

    except Exception as e:
        print("Direct correction error:", repr(e))
        return jsonify({"error": "Correction failed"}), 500


# =========================================================
# RUN LOCALLY
# =========================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
