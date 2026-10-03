from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

import sqlite3
import os
from google import genai
app = Flask(__name__)
CORS(app)

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

DATABASE = "chat.db"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            correction TEXT
        )
    """)

    try:
        conn.execute("ALTER TABLE messages ADD COLUMN correction TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


def correct_english(message):

    # 1. First try Gemini AI
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=f"""
Correct the English sentence below.

Fix ALL grammar, spelling, punctuation, word choice, and verb tense errors.

Pay special attention to time words such as:
yesterday, last week, last month, ago, tomorrow, next week, every day, usually, now, currently.

Make sure the verb tense matches the time meaning of the sentence.

Return ONLY the corrected English sentence.
Do not explain anything.
Sentence:
{message}
"""
        )

        if response.text:
            return response.text.strip()

    except Exception:
        pass

    # 2. If Gemini quota is finished, use LanguageTool
    try:
        import urllib.parse
        import urllib.request
        import json

        data = urllib.parse.urlencode({
            "text": message,
            "language": "en-US"
        }).encode("utf-8")

        request = urllib.request.Request(
            "https://api.languagetool.org/v2/check",
            data=data,
            headers={"User-Agent": "English-AI-Group-Chat"}
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))

        corrected = message

        # Apply corrections from right to left
        matches = sorted(
            result.get("matches", []),
            key=lambda x: x["offset"],
            reverse=True
        )

        for match in matches:
            replacements = match.get("replacements", [])

            if replacements:
                replacement = replacements[0]["value"]
                start = match["offset"]
                end = start + match["length"]

                corrected = (
                    corrected[:start]
                    + replacement
                    + corrected[end:]
                )

        return corrected

    except Exception:
        # Even if both services fail, message will still be sent
        return message
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/messages", methods=["GET"])
def get_messages():
    conn = get_db()

    rows = conn.execute(
        "SELECT username, message, correction FROM messages ORDER BY id ASC"
    ).fetchall()

    conn.close()

    return jsonify([
        {
            "username": row["username"],
            "message": row["message"],
            "correction": row["correction"]
        }
        for row in rows
    ])


@app.route("/send", methods=["POST"])
def send_message():
    data = request.get_json(silent=True) or {}

    username = data.get("username", "User").strip()
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400

    if not username:
        username = "User"

    conn = get_db()

    correction = correct_english(message)

    conn.execute(
        "INSERT INTO messages (username, message, correction) VALUES (?, ?, ?)",
        (username, message, correction)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "status": "success",
        "correction": correction
    })

@app.route("/correct", methods=["POST"])
def correct():
    data = request.get_json(silent=True) or {}

    message = data.get("message", "").strip()

    if not message:
        return jsonify({"correction": ""})

    correction = correct_english(message)

    return jsonify({
        "correction": correction
    })

init_db()
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
