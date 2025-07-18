import sqlite3
import json
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv

# --- KONFIGURASI APLIKASI ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')
DB_NAME = 'leaderboard.db'

# --- MEMUAT DATA SENSITIF DARI .ENV ---
try:
    USERS = json.loads(os.getenv('USERS_JSON'))
    CORRECT_FLAGS = json.loads(os.getenv('CORRECT_FLAGS_JSON'))
except (TypeError, json.JSONDecodeError):
    print("FATAL ERROR: Pastikan variabel USERS_JSON dan CORRECT_FLAGS_JSON di file .env sudah benar formatnya (JSON satu baris).")
    exit()

# --- FUNGSI DATABASE (DENGAN KOLOM BARU) ---
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS leaderboard (
                username TEXT PRIMARY KEY,
                name TEXT,
                score INTEGER DEFAULT 0,
                last_submit TEXT,
                answers TEXT,
                active_times TEXT DEFAULT '{}'  -- KOLOM BARU
            )
        ''')

# --- HALAMAN LOGIN (DENGAN PENYESUAIAN) ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        return redirect(url_for('flags'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = USERS.get(username)
        
        if user and user['password'] == password:
            session['username'] = username
            session['name'] = user['name']
            session['role'] = user['role']
            
            # Pastikan user ada di DB untuk tracking waktu
            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT username FROM leaderboard WHERE username = ?", (username,))
                if cursor.fetchone() is None:
                    cursor.execute("INSERT INTO leaderboard (username, name) VALUES (?, ?)", (username, user['name']))

            return redirect(url_for('flags'))
        return render_template('login.html', error='Username atau password salah')
    return render_template('login.html')

# --- HALAMAN UTAMA (DAFTAR SOAL) ---
@app.route('/flags')
def flags():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('flags.html', name=session.get('name'))

# --- HALAMAN PERTANYAAN CTF ---
@app.route('/question/<int:number>', methods=['GET', 'POST'])
def question(number):
    if 'username' not in session:
        return redirect(url_for('login'))

    questions = [
        "IP mana yang bertanggung jawab melakukan aktivitas pemindaian port?",
        "Berapa jumlah port terbuka yang terdeteksi?",
        "Apa nama tools yang digunakan untuk scanning?",
        "IP mana yang bertanggung jawab melakukan aktivitas pemindaian port?",
        "IP mana yang mencoba login SSH berulang kali?",
        "Apa username yang digunakan untuk brute force SSH?",
        "Waktu (timestamp) serangan terjadi pada jam berapa?",
        "Apakah ada file mencurigakan yang di-upload? (jawab: ya/tidak)",
        "Apa nama file mencurigakan tersebut?",
        "Apa hash SHA256 dari file mencurigakan?"
    ]
    flag_key = f"flag{number}"
    
    start_time_key = f'start_time_q{number}'
    if start_time_key not in session:
        session[start_time_key] = datetime.now().isoformat()

    feedback = None
    correct = False

    if request.method == 'POST':
        user_flag = request.form['flag'].strip()
        
        start_time = datetime.fromisoformat(session.get(start_time_key))
        submit_time = datetime.now()
        duration = str(timedelta(seconds=int((submit_time - start_time).total_seconds())))

        if user_flag == CORRECT_FLAGS.get(flag_key):
            correct = True
            timestamp = submit_time.strftime("%Y-%m-%d %H:%M:%S")
            
            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT score, answers FROM leaderboard WHERE username = ?", (session['username'],))
                row = cursor.fetchone()
                score, answers = (row[0], json.loads(row[1] or '{}')) if row else (0, {})

                if flag_key not in answers:
                    score += 10
                    # Simpan durasi total saat jawaban benar
                    answers[flag_key] = {"answer": user_flag, "duration": duration, "timestamp": timestamp}
                    feedback = f'✅ Jawaban benar! +10 Poin'
                    
                    if row:
                        cursor.execute("UPDATE leaderboard SET score=?, last_submit=?, answers=? WHERE username=?",
                                       (score, timestamp, json.dumps(answers), session['username']))
                    else:
                        cursor.execute("INSERT INTO leaderboard (username, name, score, last_submit, answers) VALUES (?,?,?,?,?)",
                                       (session['username'], session['name'], score, timestamp, json.dumps(answers)))
                else:
                    feedback = '✅ Soal ini sudah pernah Anda jawab dengan benar.'
        else:
            feedback = '❌ Jawaban salah. Coba lagi!'

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT score FROM leaderboard WHERE username=?", (session['username'],))
        current_score = (row[0] if (row := cursor.fetchone()) else 0)
        
        cursor.execute("SELECT username FROM leaderboard ORDER BY score DESC, last_submit ASC")
        ranks = {user[0]: i + 1 for i, user in enumerate(cursor.fetchall())}
        current_rank = ranks.get(session['username'], '-')

    return render_template('question.html',
                           number=number,
                           question_text=questions[number-1],
                           feedback=feedback,
                           correct=correct,
                           current_score=current_score,
                           rank=current_rank,
                           name=session.get('name'))

# --- API ENDPOINT BARU UNTUK TRACKING WAKTU AKTIF ---
@app.route('/api/update_time', methods=['POST'])
def update_time():
    if 'username' not in session:
        return jsonify({"status": "unauthorized"}), 401
    
    data = request.json
    question_number = data.get('question_number')
    time_spent = data.get('time_spent')
    flag_key = f"flag{question_number}"

    if not all([question_number, time_spent]):
        return jsonify({"status": "bad request"}), 400

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT active_times FROM leaderboard WHERE username = ?", (session['username'],))
        row = cursor.fetchone()
        
        if not row:
            return jsonify({"status": "user not found"}), 404

        active_times = json.loads(row[0] or '{}')
        # Tambahkan waktu sesi ini ke total waktu aktif soal
        active_times[flag_key] = active_times.get(flag_key, 0) + time_spent
        
        cursor.execute("UPDATE leaderboard SET active_times = ? WHERE username = ?",
                       (json.dumps(active_times), session['username']))

    return jsonify({"status": "success"})

# --- HALAMAN LEADERBOARD ---
@app.route('/leaderboard')
def leaderboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, score, last_submit FROM leaderboard ORDER BY score DESC, last_submit ASC")
        data = cursor.fetchall()
    return render_template('leaderboard.html', data=data, name=session.get('name'), role=session.get('role'))

# --- HALAMAN ADMIN (DENGAN DATA WAKTU AKTIF) ---
@app.route('/admin/responses')
def view_responses():
    if session.get('role') != 'admin':
        return redirect(url_for('flags'))
    
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # Ambil juga kolom active_times
        cursor.execute("SELECT name, answers, active_times FROM leaderboard ORDER BY score DESC, last_submit ASC")
        responses = []
        for row in cursor.fetchall():
            name, answers_json, active_times_json = row
            answers = json.loads(answers_json or '{}')
            active_times = json.loads(active_times_json or '{}')
            
            # Gabungkan data waktu aktif ke dalam data jawaban
            for flag_key, answer_data in answers.items():
                active_seconds = active_times.get(flag_key, 0)
                # Format waktu aktif menjadi HH:MM:SS
                answer_data['active_time'] = str(timedelta(seconds=int(active_seconds)))
            
            responses.append({"name": name, "answers": answers})

    return render_template('admin_responses.html', responses=responses, name=session.get('name'))

# --- RESET DAN LOGOUT ---
@app.route('/reset_leaderboard')
def reset_leaderboard():
    if session.get('role') != 'admin':
        return redirect(url_for('flags'))
    
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME) # Hapus file DB untuk reset total
        init_db()          # Buat ulang dari awal
        
    for key in list(session.keys()):
        session.pop(key)
            
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