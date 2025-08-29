from flask import Flask, request, jsonify, render_template, session
import mysql.connector
from mysql.connector import Error
import json
from difflib import get_close_matches
from flask_cors import CORS
from config import DB_CONFIG

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)
app.secret_key = "supersecretkey"  # change in production

# ---------- Load Intents (initial fallback) ----------
with open("intents.json", "r") as f:
    intents = json.load(f)

# ---------- Database ----------
def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

def create_tables():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_logs (
            log_id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT,
            session_id INT,
            sender ENUM('user','bot'),
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            feedback_id INT AUTO_INCREMENT PRIMARY KEY,
            log_id INT,
            rating INT,
            comments TEXT,
            FOREIGN KEY (log_id) REFERENCES chat_logs(log_id)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS intents (
            intent_id INT AUTO_INCREMENT PRIMARY KEY,
            tag VARCHAR(100),
            patterns JSON,
            responses JSON
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()

create_tables()

# ---------- User + Session ----------
def get_or_create_user(username="guest"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username=%s", (username,))
    result = cursor.fetchone()
    if result:
        user_id = result[0]
    else:
        cursor.execute("INSERT INTO users (username) VALUES (%s)", (username,))
        conn.commit()
        user_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return user_id

def create_session(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sessions (user_id) VALUES (%s)", (user_id,))
    conn.commit()
    session_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return session_id

# ---------- Logging ----------
def save_chat(user_id, sender, message, session_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    sql = "INSERT INTO chat_logs (user_id, sender, message, session_id) VALUES (%s, %s, %s, %s)"
    cursor.execute(sql, (user_id, sender, message, session_id))
    conn.commit()
    cursor.close()
    conn.close()

# ---------- Intent Matching ----------
def get_response(user_message):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM intents")
    intents_db = cursor.fetchall()
    cursor.close()
    conn.close()

    if intents_db:
        # Exact match
        for intent in intents_db:
            patterns = json.loads(intent["patterns"])
            if user_message.lower() in [p.lower() for p in patterns]:
                responses = json.loads(intent["responses"])
                return responses[0] if responses else "I don't have a response."

        # Fuzzy match
        all_patterns = []
        for intent in intents_db:
            all_patterns.extend(json.loads(intent["patterns"]))
        match = get_close_matches(user_message, all_patterns, n=1, cutoff=0.6)
        if match:
            for intent in intents_db:
                if match[0] in json.loads(intent["patterns"]):
                    responses = json.loads(intent["responses"])
                    return responses[0] if responses else "I don't have a response."

    # fallback to intents.json
    for intent in intents["intents"]:
        if user_message.lower() in [p.lower() for p in intent["patterns"]]:
            return intent["responses"][0]

    return "Sorry, I didn't understand that."

# ---------- Routes ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/admin")
def admin_panel():
    return render_template("admin.html")

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        username = data.get("user_id", "guest")
        user_message = data.get("message", "")

        # User + session
        user_id = get_or_create_user(username)
        session_id = create_session(user_id)

        # Log user message
        save_chat(user_id, "user", user_message, session_id)

        # Bot response
        bot_response = get_response(user_message)

        # Log bot message
        save_chat(user_id, "bot", bot_response, session_id)

        return jsonify({"response": bot_response})

    except Exception as e:
        print("❌ ERROR in /chat:", str(e))
        return jsonify({"response": "Server error: " + str(e)}), 500

# ---------- Admin APIs ----------
@app.route("/login", methods=["POST"])
def login():
    data = request.json
    if data.get("username") == "admin" and data.get("password") == "admin123":
        session["admin"] = True
        return jsonify({"status": "success"})
    return jsonify({"status": "fail"}), 401

@app.route("/logout")
def logout():
    session.pop("admin", None)
    return jsonify({"status": "logged_out"})

@app.route("/add_intent", methods=["POST"])
def add_intent():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403
    data = request.json
    tag = data.get("tag")
    patterns = json.dumps(data.get("patterns", []))
    responses = json.dumps(data.get("responses", []))

    conn = get_db_connection()
    cursor = conn.cursor()
    sql = "INSERT INTO intents (tag, patterns, responses) VALUES (%s, %s, %s)"
    cursor.execute(sql, (tag, patterns, responses))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"status": "intent_added"})

@app.route("/get_intents", methods=["GET"])
def get_intents():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 403
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM intents")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for r in rows:
        r["patterns"] = json.loads(r["patterns"])
        r["responses"] = json.loads(r["responses"])
    return jsonify(rows)

if __name__ == "__main__":
    app.run(debug=True)
