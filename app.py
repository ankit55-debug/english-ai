from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

import sqlite3
from supabase import create_client
import os
import threading

from google import genai

app = Flask(__name__)
CORS(app)

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase = create_client(supabase_url, supabase_key)

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
     # Create groups table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            invite_code TEXT UNIQUE NOT NULL
        )
    """)

    # Create group members table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            UNIQUE(group_id, username)
        )
    """)

    # Add group_id to existing messages table
    columns = [
        row["name"]
        for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    ]

    if "group_id" not in columns:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN group_id INTEGER"
        )

    # Create default group
    conn.execute("""
        INSERT OR IGNORE INTO groups (id, name, invite_code)
        VALUES (1, 'General', 'general-room')
    """)

    # Move existing messages into General
    conn.execute("""
        UPDATE messages
        SET group_id = 1
        WHERE group_id IS NULL
    """)
    conn.commit()
    conn.close()


# =========================================================
# ENGLISH CORRECTION ENGINE
# =========================================================

def correct_english(message):

    # -----------------------------------------------------
    # 1. Gemini AI - Advanced correction
    # -----------------------------------------------------
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=f"""
You are an expert English teacher and professional English editor.

Correct the user's English sentence thoroughly.

Check ALL of these:

1. Grammar
2. Verb tense and aspect
3. Subject-verb agreement
4. Articles: a, an, the
5. Prepositions
6. Singular/plural forms
7. Pronouns
8. Verb forms
9. Word order
10. Spelling
11. Punctuation
12. Word choice
13. Sentence structure
14. Natural and fluent English
15. Awkward or unnatural expressions
16. Conditional sentences
17. Modal verbs
18. Active/passive voice
19. Reported speech
20. Advanced grammar errors

IMPORTANT:

- Pay special attention to time expressions.
- Words such as yesterday, last week, ago, since, for,
  already, yet, tomorrow, next week, every day,
  usually, now, currently must match the correct tense.
- Do not change the meaning of the user's sentence.
- Preserve names and intended meaning.
- If the sentence is already correct and natural,
  return it unchanged.
- Return ONLY the corrected English sentence.
- Do NOT give explanations.
- Do NOT add labels such as "Correction:".
- Do NOT use quotation marks.

Examples:

Input: He go to school yesterday.
Output: He went to school yesterday.

Input: I am living here since five years.
Output: I have been living here for five years.

Input: She don't likes coffee.
Output: She doesn't like coffee.

Input: If I would know, I will tell you.
Output: If I knew, I would tell you.

Input: He has went to market yesterday.
Output: He went to the market yesterday.

Input:
{message}

Corrected sentence:
"""
        )

        if response.text:
            return response.text.strip()

    except Exception:
        pass


    # -----------------------------------------------------
    # 2. LanguageTool fallback
    # -----------------------------------------------------
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
            headers={
                "User-Agent": "English-AI-Group-Chat"
            }
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

        corrected = message

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

        if corrected != message:
            return corrected

    except Exception:
        pass


    # -----------------------------------------------------
    # 3. Local smart fallback
    # -----------------------------------------------------

    corrected = message

    lower = message.lower()

    # Common tense correction with "yesterday"
    if "yesterday" in lower:

        replacements = {
            " goes ": " went ",
            " go ": " went ",
            " comes ": " came ",
            " come ": " came ",
            " sees ": " saw ",
            " see ": " saw ",
            " eats ": " ate ",
            " eat ": " ate ",
            " takes ": " took ",
            " take ": " took ",
            " gives ": " gave ",
            " give ": " gave ",
            " gets ": " got ",
            " get ": " got ",
            " makes ": " made ",
            " make ": " made ",
            " does ": " did ",
            " do ": " did ",
            " has ": " had ",
            " have ": " had ",
            " goes": " went",
            " go": " went",
            " comes": " came",
            " come": " came",
            " sees": " saw",
            " see": " saw",
            " eats": " ate",
            " eat": " ate",
            " takes": " took",
            " take": " took",
            " gives": " gave",
            " give": " gave",
            " gets": " got",
            " get": " got",
            " makes": " made",
            " make": " made",
            " does": " did",
            " do": " did",
            " has": " had",
            " have": " had"
        }

        for old, new in replacements.items():
            corrected = corrected.replace(old, new)


    # "last week / last month / last year"
    time_words = [
        "last week",
        "last month",
        "last year"
    ]

    if any(word in lower for word in time_words):

        replacements = {
            " goes ": " went ",
            " go ": " went ",
            " comes ": " came ",
            " come ": " came ",
            " eats ": " ate ",
            " eat ": " ate ",
            " sees ": " saw ",
            " see ": " saw ",
            " takes ": " took ",
            " take ": " took ",
            " does ": " did ",
            " do ": " did ",
            " has ": " had ",
            " have ": " had "
        }

        for old, new in replacements.items():
            corrected = corrected.replace(old, new)


    # Basic subject-verb agreement
    basic_replacements = {
        "i is ": "I am ",
        "i are ": "I am ",
        "he are ": "he is ",
        "she are ": "she is ",
        "it are ": "it is ",
        "they is ": "they are ",
        "we is ": "we are ",
        "you is ": "you are ",

        "he don't ": "he doesn't ",
        "she don't ": "she doesn't ",
        "it don't ": "it doesn't ",

        "he doesn't likes ": "he doesn't like ",
        "she doesn't likes ": "she doesn't like ",
        "it doesn't likes ": "it doesn't like "
    }

    for old, new in basic_replacements.items():
        corrected = corrected.replace(old, new)


    # Common "am/is/are + verb" corrections
    continuous_replacements = {
        "i am go ": "I am going ",
        "i am eat ": "I am eating ",
        "i am work ": "I am working ",
        "i am study ": "I am studying ",
        "he is go ": "he is going ",
        "she is go ": "she is going ",
        "they are go ": "they are going "
    }

    for old, new in continuous_replacements.items():
        corrected = corrected.replace(old, new)


    # Basic article corrections
    article_replacements = {
        "a apple": "an apple",
        "a orange": "an orange",
        "a umbrella": "an umbrella",
        "a hour": "an hour"
    }

    for old, new in article_replacements.items():
        corrected = corrected.replace(old, new)


    # Basic punctuation
    corrected = corrected.strip()

    if corrected and corrected[-1] not in ".!?":
        corrected += "."


    return corrected


# =========================================================
# BACKGROUND CORRECTION
# =========================================================
def correct_message_in_background(message_id, message):
    print("CORRECTION STARTED:", message)

    try:
        correction = correct_english(message)

        print("CORRECTION RESULT:", correction)

        conn = get_db()

        conn.execute(
            """
            UPDATE messages
            SET correction = ?
            WHERE id = ?
            """,
            (correction, message_id)
        )

        conn.commit()
        conn.close()

        print("CORRECTION SAVED:", message_id)

    except Exception as e:
        print("CORRECTION ERROR:", repr(e))


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/create_group", methods=["POST"])
def create_group():
    import secrets

    data = request.get_json(silent=True) or {}
    group_name = (data.get("name") or "").strip()

    if not group_name:
        return jsonify({"error": "Group name is required"}), 400

    invite_code = secrets.token_urlsafe(16)

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO groups (name, invite_code) VALUES (?, ?)",
        (group_name, invite_code)
    )
    group_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return jsonify({
        "group_id": group_id,
        "group_name": group_name,
        "invite_link": request.host_url + "join/" + invite_code
    }) 

    
@app.route("/join/<invite_code>")
def join_group(invite_code):
    conn = get_db()

    group = conn.execute(
        "SELECT id, name FROM groups WHERE invite_code = ?",
        (invite_code,)
    ).fetchone()

    conn.close()

    if group is None:
        return "Invalid group invite link", 404

    return render_template(
        "index.html",
        group_id=group["id"],
        group_name=group["name"]
    )
# =========================================================
# GET MESSAGES
# =========================================================

@app.route("/messages", methods=["GET"])
def get_messages():

    group_id = request.args.get("group_id", type=int)

    if not group_id:
        return jsonify({"error": "Group ID is required"}), 400

    conn = get_db()

    group = conn.execute(
        "SELECT id FROM groups WHERE id = ?",
        (group_id,)
    ).fetchone()

    if group is None:
        conn.close()
        return jsonify({"error": "Group not found"}), 404

    rows = conn.execute(
        """
        SELECT id, username, message, correction
        FROM messages
        WHERE group_id = ?
        ORDER BY id ASC
        """,
        (group_id,)
    ).fetchall()

    conn.close()

    return jsonify([
        {
            "id": row["id"],
            "username": row["username"],
            "message": row["message"],
            "correction": row["correction"]
        }
        for row in rows
    ])

# =========================================================
# SEND MESSAGE
# =========================================================

@app.route("/send", methods=["POST"])
def send_message():

    data = request.get_json(silent=True) or {}

    username = (data.get("username") or "User").strip()
    message = (data.get("message") or "").strip()
    group_id = data.get("group_id")

    if not message:
        return jsonify({"error": "Message cannot be empty"}), 400

    if not username:
        username = "User"

    try:
        group_id = int(group_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Valid group ID is required"}), 400

    conn = get_db()

    group = conn.execute(
        "SELECT id FROM groups WHERE id = ?",
        (group_id,)
    ).fetchone()

    if group is None:
        conn.close()
        return jsonify({"error": "Group not found"}), 404

    cursor = conn.execute(
        """
        INSERT INTO messages
        (username, message, correction, group_id)
        VALUES (?, ?, ?, ?)
        """,
        (username, message, None, group_id)
    )

    message_id = cursor.lastrowid

    conn.commit()
    conn.close()

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
        "group_id": group_id
    })

# =========================================================
# DIRECT CORRECTION ENDPOINT
# =========================================================

@app.route("/correct", methods=["POST"])
def correct():

    data = request.get_json(silent=True) or {}

    message = data.get("message", "").strip()

    if not message:
        return jsonify({
            "correction": ""
        })

    correction = correct_english(message)

    return jsonify({
        "correction": correction
    })


# =========================================================
# DATABASE INITIALIZATION
# =========================================================

init_db()


if __name__ == "__main__":

    init_db()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False
    )
