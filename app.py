import os
import sys
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

# --- KONFIGURASI APLIKASI ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')

# ================== BLOK YANG DIPERBAIKI ==================
# Memeriksa argumen untuk generate hash SEBELUM melakukan hal lain
if '--generate-hash' in sys.argv:
    try:
        # Mengimpor library database hanya jika diperlukan di sini
        password_to_hash = sys.argv[sys.argv.index('--generate-hash') + 1]
        print(f"\nPassword: {password_to_hash}")
        print(f"Hash: {generate_password_hash(password_to_hash)}\n")
    except IndexError:
        print("Usage: python app.py --generate-hash <password>")
    sys.exit(0) # Keluar dari skrip setelah hash dibuat
# ==========================================================

# --- KONEKSI DATABASE DAN INISIALISASI (HANYA JIKA TIDAK GENERATE HASH) ---
# Kode ini sekarang aman karena tidak akan dieksekusi jika --generate-hash ada
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("FATAL: DATABASE_URL tidak ditemukan di environment variables.")

engine = create_engine(DATABASE_URL)

def init_db():
    try:
        with engine.connect() as conn:
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS leaderboard (
                    username TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    score INTEGER DEFAULT 0,
                    last_submit TIMESTAMP,
                    answers JSONB DEFAULT '{}'::jsonb,
                    used_hints JSONB DEFAULT '{}'::jsonb,
                    active_times JSONB DEFAULT '{}'::jsonb
                );
            '''))
            conn.commit()
        print("Database initialized successfully.")
    except Exception as e:
        print(f"Error initializing database: {e}")

# --- MEMUAT DATA KONFIGURASI DARI ENV ---
try:
    USERS = json.loads(os.getenv('USERS_JSON'))
    CORRECT_FLAGS = json.loads(os.getenv('CORRECT_FLAGS_JSON'))
    HINTS = json.loads(os.getenv('HINTS_JSON'))
except (TypeError, json.JSONDecodeError) as e:
    raise RuntimeError(f"FATAL ERROR: Format JSON salah di environment variables. Error: {e}")

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

# --- HALAMAN & LOGIKA APLIKASI ---

@app.route('/', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        return redirect(url_for('flags'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user_data = USERS.get(username)
        
        if user_data and check_password_hash(user_data['password_hash'], password):
            session.clear()
            session['username'] = username
            session['name'] = user_data['name']
            session['role'] = user_data['role']
            
            with engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO leaderboard (username, name) VALUES (:username, :name)
                    ON CONFLICT (username) DO NOTHING
                """), {"username": username, "name": user_data['name']})
                conn.commit()
            return redirect(url_for('flags'))
        return render_template('login.html', error='User ID atau Access Key salah.')
    return render_template('login.html')

@app.route('/flags')
def flags():
    if 'username' not in session: return redirect(url_for('login'))
    
    solved_flags = []
    with engine.connect() as conn:
        result = conn.execute(text("SELECT answers FROM leaderboard WHERE username = :username"), 
                              {"username": session['username']}).fetchone()
        if result and result[0]:
            solved_flags = result[0].keys()

    return render_template('flags.html', 
                           name=session.get('name'), 
                           challenges=CHALLENGES,
                           solved_flags=list(solved_flags),
                           role=session.get('role'))

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

    with engine.connect() as conn:
        result = conn.execute(text("SELECT score, answers, used_hints FROM leaderboard WHERE username=:username"), 
                              {"username": session['username']}).fetchone()
        
    current_score, answers, used_hints = result if result else (0, {}, {})
    answers = answers or {}
    used_hints = used_hints or {}

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
            
            score_to_add = challenge.get('points', 10)
            
            if flag_key not in answers:
                current_score += score_to_add
                answers[flag_key] = {
                    "answer": user_flag, "timestamp": timestamp, "duration": total_duration
                }
                feedback = f'✅ Flag Benar! +{score_to_add} Poin'
                
                with engine.connect() as conn:
                    conn.execute(text("""
                        UPDATE leaderboard SET score=:score, last_submit=:last_submit, answers=:answers 
                        WHERE username=:username
                    """), {
                        "score": current_score, "last_submit": submit_time, 
                        "answers": json.dumps(answers), "username": session['username']
                    })
                    conn.commit()
        else:
            feedback = '❌ Flag Salah. Coba lagi!'

    with engine.connect() as conn:
        ranks_result = conn.execute(text("SELECT username FROM leaderboard ORDER BY score DESC, last_submit ASC")).fetchall()
        ranks = {row[0]: i + 1 for i, row in enumerate(ranks_result)}
        current_rank = ranks.get(session['username'], '-')

    return render_template('question.html',
                           challenge=challenge, feedback=feedback, correct=correct, 
                           current_score=current_score, rank=current_rank, 
                           name=session.get('name'), hint_taken=flag_key in used_hints, 
                           HINTS=HINTS)

@app.route('/api/update_time', methods=['POST'])
def update_time():
    if 'username' not in session: return jsonify({"status": "unauthorized"}), 401
    
    data = request.json
    question_number = data.get('question_number')
    time_spent = data.get('time_spent')
    flag_key = f"flag{question_number}"

    if not all([question_number, isinstance(time_spent, (int, float))]):
        return jsonify({"status": "bad request"}), 400

    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE leaderboard 
            SET active_times = jsonb_set(
                active_times, 
                ARRAY[:flag_key], 
                (COALESCE(active_times->>:flag_key, '0')::numeric + :time_spent)::text::jsonb
            )
            WHERE username = :username
        """), {
            "flag_key": flag_key, "time_spent": time_spent, "username": session['username']
        })
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/api/get_hint/<int:number>', methods=['POST'])
def get_hint(number):
    if 'username' not in session: return jsonify({"status": "unauthorized"}), 401

    flag_key = f"flag{number}"
    hint_info = HINTS.get(flag_key)
    
    if not hint_info: return jsonify({"status": "not_found", "message": "Petunjuk tidak ditemukan."}), 404

    with engine.connect() as conn:
        result = conn.execute(text("SELECT score, used_hints, answers FROM leaderboard WHERE username = :username"), 
                              {"username": session['username']}).fetchone()
        
    if not result: return jsonify({"status": "error", "message": "User tidak ditemukan."}), 404
        
    score, used_hints, answers = result
    used_hints = used_hints or {}
    answers = answers or {}

    if flag_key in answers: return jsonify({"status": "already_solved", "message": "Soal sudah diselesaikan."})
    if flag_key in used_hints: return jsonify({"status": "already_taken", "hint": hint_info['text']})

    penalty = hint_info.get('penalty', 5)
    if score < penalty:
        return jsonify({"status": "insufficient_score", "message": f"Skor tidak cukup! Butuh {penalty} poin."}), 400
            
    new_score = score - penalty
    used_hints[flag_key] = True
    
    with engine.connect() as conn:
        conn.execute(text("UPDATE leaderboard SET score = :score, used_hints = :used_hints WHERE username = :username"),
                     {"score": new_score, "used_hints": json.dumps(used_hints), "username": session['username']})
        conn.commit()

    return jsonify({"status": "success", "hint": hint_info['text'], "new_score": new_score})

# --- RUTE ADMIN ---
@app.route('/leaderboard')
def leaderboard():
    if 'username' not in session: return redirect(url_for('login'))
    with engine.connect() as conn:
        data = conn.execute(text("SELECT name, score, last_submit FROM leaderboard ORDER BY score DESC, last_submit ASC")).fetchall()
    return render_template('leaderboard.html', data=data, name=session.get('name'), role=session.get('role'))

@app.route('/admin/responses')
def view_responses():
    if session.get('role') != 'admin': return redirect(url_for('flags'))
    
    with engine.connect() as conn:
        results = conn.execute(text("SELECT name, score, answers, active_times, used_hints FROM leaderboard ORDER BY score DESC, last_submit ASC")).fetchall()

    responses = []
    for row in results:
        name, score, answers_data, active_times_data, used_hints_data = row
        answers_data = answers_data or {}
        active_times_data = active_times_data or {}
        
        for flag_key, answer_info in answers_data.items():
            active_seconds = active_times_data.get(flag_key, 0)
            answer_info['active_time'] = str(timedelta(seconds=int(active_seconds)))
            
        responses.append({
            "name": name, "score": score, "answers": answers_data, 
            "used_hints": used_hints_data or {}
        })

    return render_template('admin_responses.html', responses=responses, name=session.get('name'))

@app.route('/admin/stats')
def admin_stats():
    if session.get('role') != 'admin': return redirect(url_for('flags'))

    with engine.connect() as conn:
        all_answers_results = conn.execute(text("SELECT answers FROM leaderboard")).fetchall()

    all_answers = [row[0] for row in all_answers_results if row[0]]
    solve_counts = {f"flag{c['id']}": 0 for c in CHALLENGES}
    
    for user_answers in all_answers:
        for flag_key in user_answers.keys():
            if flag_key in solve_counts:
                solve_counts[flag_key] += 1
    
    stats_data = [{"id": c['id'], "title": c['title'], "solves": solve_counts.get(f"flag{c['id']}", 0)} for c in CHALLENGES]

    return render_template('admin_stats.html', stats=stats_data, name=session.get('name'))

@app.route('/reset_leaderboard')
def reset_leaderboard():
    if session.get('role') != 'admin': return redirect(url_for('flags'))
    with engine.connect() as conn:
        # Perintah TRUNCATE lebih efisien untuk mengosongkan tabel besar
        conn.execute(text("TRUNCATE TABLE leaderboard RESTART IDENTITY;"))
        conn.commit()
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- MENJALANKAN INISIALISASI DATABASE SAAT STARTUP ---
with app.app_context():
    init_db()