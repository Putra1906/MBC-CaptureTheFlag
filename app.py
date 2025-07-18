import sqlite3
import json
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv

# --- KONFIGURASI APLIKASI ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'default-secret-key-for-development')
DB_NAME = 'leaderboard.db'

# --- MEMUAT DATA SENSITIF ---
try:
    USERS = json.loads(os.getenv('USERS_JSON'))
    CORRECT_FLAGS = json.loads(os.getenv('CORRECT_FLAGS_JSON'))
    HINTS = json.loads(os.getenv('HINTS_JSON'))
except (TypeError, json.JSONDecodeError) as e:
    print(f"FATAL ERROR: Pastikan variabel JSON di file .env sudah benar formatnya. Error: {e}")
    exit()

# --- STRUKTUR DATA TANTANGAN ---
CHALLENGES = [
    {"id": 1, "title": "Reconnaissance", "points": 10, "difficulty": "Mudah", "question": "Dari hasil pemindaian, IP mana yang terdeteksi melakukan port scanning?"},
    {"id": 2, "title": "Port Analysis", "points": 10, "difficulty": "Mudah", "question": "Berapa jumlah total port TCP yang ditemukan terbuka pada target?"},
    {"id": 3, "title": "Service Enumeration", "points": 10, "difficulty": "Mudah", "question": "Tools Nmap menggunakan script (NSE) untuk enumerasi. Apa nama script yang paling relevan dengan layanan web?"},
    {"id": 4, "title": "Log Analysis: SSH", "points": 10, "difficulty": "Sedang", "question": "Sebuah aktivitas brute force terdeteksi pada layanan SSH. Dari IP mana serangan itu berasal?"},
    {"id": 5, "title": "Credential Guessing", "points": 10, "difficulty": "Sedang", "question": "Apa username yang paling sering digunakan dalam serangan brute force SSH tersebut?"},
    {"id": 6, "title": "Timestamp Forensics", "points": 10, "difficulty": "Sedang", "question": "Pada jam berapa (timestamp paling awal) serangan brute force SSH dimulai?"},
    {"id": 7, "title": "Web Shell Detection", "points": 10, "difficulty": "Sulit", "question": "Sebuah file mencurigakan diunggah ke server. Apa nama file tersebut?"},
    {"id": 8, "title": "File Forensics", "points": 10, "difficulty": "Sulit", "question": "Berapakah ukuran file mencurigakan tersebut dalam bytes?"},
    {"id": 9, "title": "Hashing", "points": 10, "difficulty": "Sulit", "question": "Ekstrak hash SHA256 dari file mencurigakan tersebut."},
    {"id": 10, "title": "Payload Analysis", "points": 10, "difficulty": "Expert", "question": "Di dalam file yang diunggah, terdapat sebuah domain tersembunyi yang digunakan sebagai C2 Server. Apa domain tersebut?"}
]

# --- FUNGSI DATABASE ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS leaderboard (
                username TEXT PRIMARY KEY,
                name TEXT,
                score INTEGER DEFAULT 0,
                last_submit TEXT,
                answers TEXT DEFAULT '{}',
                used_hints TEXT DEFAULT '{}',
                active_times TEXT DEFAULT '{}'
            )
        ''')

# --- HALAMAN LOGIN ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        return redirect(url_for('flags'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = USERS.get(username)
        
        if user and user['password'] == password:
            session.clear()
            session['username'] = username
            session['name'] = user['name']
            session['role'] = user['role']
            
            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT username FROM leaderboard WHERE username = ?", (username,))
                if cursor.fetchone() is None:
                    cursor.execute("INSERT INTO leaderboard (username, name) VALUES (?, ?)", (username, user['name']))
            return redirect(url_for('flags'))
        return render_template('login.html', error='User ID atau Access Key salah.')
    return render_template('login.html')

# --- HALAMAN UTAMA (DAFTAR SOAL) ---
@app.route('/flags')
def flags():
    if 'username' not in session: return redirect(url_for('login'))
    
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT answers FROM leaderboard WHERE username = ?", (session['username'],))
        row = cursor.fetchone()
        solved_flags = json.loads(row[0] if row and row[0] else '{}').keys()

    return render_template('flags.html', 
                           name=session.get('name'), 
                           challenges=CHALLENGES,
                           solved_flags=solved_flags,
                           role=session.get('role'))

# --- HALAMAN PERTANYAAN ---
@app.route('/question/<int:number>', methods=['GET', 'POST'])
def question(number):
    if 'username' not in session: return redirect(url_for('login'))

    challenge = next((c for c in CHALLENGES if c['id'] == number), None)
    if not challenge: return redirect(url_for('flags'))

    flag_key = f"flag{number}"
    start_time_key = f'start_time_{flag_key}'

    if start_time_key not in session:
        session[start_time_key] = datetime.now().isoformat()

    feedback = None
    correct = False

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT score, answers, used_hints FROM leaderboard WHERE username=?", (session['username'],))
        row = cursor.fetchone()
        current_score = row[0] if row else 0
        answers = json.loads(row[1] if row and row[1] else '{}')
        used_hints = json.loads(row[2] if row and row[2] else '{}')

    if flag_key in answers:
        correct = True
        feedback = '✅ Tantangan ini telah Anda selesaikan.'

    if request.method == 'POST' and not correct:
        user_flag = request.form['flag'].strip()
        
        if user_flag == CORRECT_FLAGS.get(flag_key):
            correct = True
            submit_time = datetime.now()
            timestamp = submit_time.strftime("%Y-%m-%d %H:%M:%S")

            start_time = datetime.fromisoformat(session.get(start_time_key))
            total_duration = str(timedelta(seconds=int((submit_time - start_time).total_seconds())))
            
            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.cursor()
                score_to_add = challenge.get('points', 10)
                score, current_answers = (current_score, answers)

                if flag_key not in current_answers:
                    score += score_to_add
                    current_answers[flag_key] = {
                        "answer": user_flag, 
                        "timestamp": timestamp,
                        "duration": total_duration
                    }
                    feedback = f'✅ Flag Benar! +{score_to_add} Poin'
                    
                    cursor.execute("UPDATE leaderboard SET score=?, last_submit=?, answers=? WHERE username=?",
                                   (score, timestamp, json.dumps(current_answers), session['username']))
                    current_score = score
        else:
            feedback = '❌ Flag Salah. Coba lagi!'

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM leaderboard ORDER BY score DESC, last_submit ASC")
        ranks = {user[0]: i + 1 for i, user in enumerate(cursor.fetchall())}
        current_rank = ranks.get(session['username'], '-')

    # ========== BLOK YANG DIPERBAIKI ==========
    return render_template('question.html',
                           challenge=challenge,
                           feedback=feedback,
                           correct=correct,
                           current_score=current_score,
                           rank=current_rank,
                           name=session.get('name'),
                           hint_taken=flag_key in used_hints,
                           HINTS=HINTS  # FIX: Kirim kamus HINTS ke template
                           )
    # ==========================================

# --- API ENDPOINTS ---
@app.route('/api/update_time', methods=['POST'])
def update_time():
    if 'username' not in session:
        return jsonify({"status": "unauthorized"}), 401
    
    data = request.json
    question_number = data.get('question_number')
    time_spent = data.get('time_spent')
    flag_key = f"flag{question_number}"

    if not all([question_number, isinstance(time_spent, (int, float))]):
        return jsonify({"status": "bad request"}), 400

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT active_times FROM leaderboard WHERE username = ?", (session['username'],))
        row = cursor.fetchone()
        if not row: return jsonify({"status": "user not found"}), 404

        active_times = json.loads(row[0] or '{}')
        active_times[flag_key] = active_times.get(flag_key, 0) + time_spent
        
        cursor.execute("UPDATE leaderboard SET active_times = ? WHERE username = ?",
                       (json.dumps(active_times), session['username']))
    return jsonify({"status": "success"})

@app.route('/api/get_hint/<int:number>', methods=['POST'])
def get_hint(number):
    if 'username' not in session:
        return jsonify({"status": "unauthorized"}), 401

    flag_key = f"flag{number}"
    hint_info = HINTS.get(flag_key)
    
    if not hint_info:
        return jsonify({"status": "not_found", "message": "Petunjuk tidak ditemukan."}), 404

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT score, used_hints, answers FROM leaderboard WHERE username = ?", (session['username'],))
        row = cursor.fetchone()
        
        if not row: return jsonify({"status": "error", "message": "User tidak ditemukan."}), 404
        
        score, used_hints_json, answers_json = row
        used_hints = json.loads(used_hints_json or '{}')
        answers = json.loads(answers_json or '{}')

        if flag_key in answers:
            return jsonify({"status": "already_solved", "message": "Soal sudah diselesaikan."})
        
        if flag_key in used_hints:
            return jsonify({"status": "already_taken", "hint": hint_info['text']})

        penalty = hint_info.get('penalty', 5)
        if score < penalty:
            return jsonify({"status": "insufficient_score", "message": f"Skor tidak cukup! Butuh {penalty} poin."}), 400
            
        new_score = score - penalty
        used_hints[flag_key] = True
        
        cursor.execute("UPDATE leaderboard SET score = ?, used_hints = ? WHERE username = ?",
                       (new_score, json.dumps(used_hints), session['username']))

    return jsonify({"status": "success", "hint": hint_info['text'], "new_score": new_score})

# --- HALAMAN ADMIN & LAINNYA ---
@app.route('/leaderboard')
def leaderboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, score, last_submit FROM leaderboard ORDER BY score DESC, last_submit ASC")
        data = cursor.fetchall()
    return render_template('leaderboard.html', data=data, name=session.get('name'), role=session.get('role'))

@app.route('/admin/responses')
def view_responses():
    if session.get('role') != 'admin': return redirect(url_for('flags'))
    
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, score, answers, active_times, used_hints FROM leaderboard ORDER BY score DESC, last_submit ASC")
        responses = []
        for row in cursor.fetchall():
            name, score, answers_json, active_times_json, used_hints_json = row
            answers_data = json.loads(answers_json or '{}')
            active_times_data = json.loads(active_times_json or '{}')
            
            for flag_key, answer_info in answers_data.items():
                active_seconds = active_times_data.get(flag_key, 0)
                answer_info['active_time'] = str(timedelta(seconds=int(active_seconds)))
            
            responses.append({
                "name": name, "score": score, 
                "answers": answers_data, 
                "used_hints": json.loads(used_hints_json or '{}')
            })

    return render_template('admin_responses.html', responses=responses, name=session.get('name'))

@app.route('/admin/stats')
def admin_stats():
    if session.get('role') != 'admin': return redirect(url_for('flags'))

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT answers FROM leaderboard")
        all_answers = [json.loads(row[0] or '{}') for row in cursor.fetchall()]

    solve_counts = {f"flag{c['id']}": 0 for c in CHALLENGES}
    for user_answers in all_answers:
        for flag_key in user_answers:
            if flag_key in solve_counts:
                solve_counts[flag_key] += 1
    
    stats_data = []
    for c in CHALLENGES:
        flag_key = f"flag{c['id']}"
        solves = solve_counts.get(flag_key, 0)
        stats_data.append({
            "id": c['id'],
            "title": c['title'],
            "solves": solves
        })

    return render_template('admin_stats.html', stats=stats_data, name=session.get('name'))

@app.route('/reset_leaderboard')
def reset_leaderboard():
    if session.get('role') != 'admin': return redirect(url_for('flags'))
    db_path = os.path.join(os.path.dirname(__file__), DB_NAME)
    if os.path.exists(db_path): os.remove(db_path)
    init_db()
    session.clear()
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- MENJALANKAN APLIKASI ---
if __name__ == '__main__':
    if not os.path.exists(DB_NAME):
        init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)