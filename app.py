import os
import sys
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from werkzeug.security import check_password_hash, generate_password_hash

# --- KONFIGURASI APLIKASI ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')

# --- SCRIPT BANTU UNTUK MEMBUAT HASH PASSWORD ---
if '--generate-hash' in sys.argv:
    try:
        password_to_hash = sys.argv[sys.argv.index('--generate-hash') + 1]
        print(f"\nPassword: {password_to_hash}")
        print(f"Hash: {generate_password_hash(password_to_hash)}\n")
    except IndexError:
        print("Usage: python app.py --generate-hash <password>")
    sys.exit(0)

# --- KONEKSI DATABASE POSTGRESQL ---
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("FATAL: DATABASE_URL tidak ditemukan di environment variables.")
engine = create_engine(DATABASE_URL)

# --- HALAMAN & LOGIKA APLIKASI ---

@app.route('/', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        return redirect(url_for('flags'))
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        with engine.connect() as conn:
            result = conn.execute(text("SELECT * FROM users WHERE username = :username"), {"username": username}).fetchone()
        
        if result and check_password_hash(result._mapping['password_hash'], password):
            user_data = result._mapping
            session.clear()
            session['username'] = user_data['username']
            session['name'] = user_data['name']
            session['role'] = user_data['role']
            
            with engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO leaderboard (username, name) VALUES (:username, :name)
                    ON CONFLICT (username) DO NOTHING
                """), {"username": session['username'], "name": session['name']})
                conn.commit()
            return redirect(url_for('flags'))
            
        return render_template('login.html', error='User ID atau Access Key salah.')
    return render_template('login.html')

@app.route('/flags')
def flags():
    if 'username' not in session: return redirect(url_for('login'))
    
    with engine.connect() as conn:
        challenges_result = conn.execute(text("SELECT id, title, points, difficulty FROM challenges ORDER BY id")).fetchall()
        challenges = [row._mapping for row in challenges_result]
        
        result = conn.execute(text("SELECT answers FROM leaderboard WHERE username = :username"), 
                              {"username": session['username']}).fetchone()
    
    solved_flags = result[0].keys() if result and result[0] else []

    return render_template('flags.html', 
                           name=session.get('name'), 
                           challenges=challenges,
                           solved_flags=list(solved_flags),
                           role=session.get('role'))

@app.route('/question/<int:number>', methods=['GET', 'POST'])
def question(number):
    if 'username' not in session: return redirect(url_for('login'))

    with engine.connect() as conn:
        challenge_result = conn.execute(text("SELECT * FROM challenges WHERE id = :id"), {"id": number}).fetchone()
        if not challenge_result: return redirect(url_for('flags'))
        challenge = challenge_result._mapping
        
        lb_result = conn.execute(text("SELECT score, answers, used_hints FROM leaderboard WHERE username=:username"), 
                                 {"username": session['username']}).fetchone()
    
    current_score, answers, used_hints = (lb_result._mapping['score'], lb_result._mapping['answers'], lb_result._mapping['used_hints']) if lb_result else (0, {}, {})
    answers = answers or {}
    used_hints = used_hints or {}

    flag_key = f"flag{number}"
    start_time_key = f'start_time_{flag_key}'

    if start_time_key not in session:
        session[start_time_key] = datetime.now().isoformat()

    feedback, correct = (None, False)
    if flag_key in answers:
        correct, feedback = (True, '✅ Tantangan ini telah Anda selesaikan.')

    if request.method == 'POST' and not correct:
        user_flag = request.form['flag'].strip()
        
        if user_flag == challenge['correct_flag']:
            correct = True
            submit_time = datetime.now()
            start_time = datetime.fromisoformat(session.get(start_time_key))
            total_duration = str(timedelta(seconds=int((submit_time - start_time).total_seconds())))
            
            score_to_add = challenge.get('points', 10)
            current_score += score_to_add
            answers[flag_key] = {
                "answer": user_flag, 
                "timestamp": submit_time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": total_duration
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
    ranks = {row._mapping['username']: i + 1 for i, row in enumerate(ranks_result)}
    current_rank = ranks.get(session['username'], '-')

    return render_template('question.html',
                           challenge=challenge, feedback=feedback, correct=correct,
                           current_score=current_score, rank=current_rank, 
                           name=session.get('name'), hint_taken=flag_key in used_hints)

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
                COALESCE(active_times, '{}'::jsonb), 
                ARRAY[:flag_key], 
                (COALESCE(active_times->>:flag_key, '0')::numeric + :time_spent)::text::jsonb,
                true
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

    with engine.connect() as conn:
        challenge_result = conn.execute(text("SELECT hint_text, hint_penalty FROM challenges WHERE id = :id"), {"id": number}).fetchone()
        if not challenge_result or not challenge_result._mapping['hint_text']:
            return jsonify({"status": "not_found", "message": "Petunjuk tidak ditemukan."}), 404
        hint_info = challenge_result._mapping
        
        lb_result = conn.execute(text("SELECT score, used_hints, answers FROM leaderboard WHERE username = :username"), 
                              {"username": session['username']}).fetchone()
    
    if not lb_result: return jsonify({"status": "error", "message": "User tidak ditemukan."}), 404
    
    score, used_hints, answers = lb_result._mapping['score'], lb_result._mapping['used_hints'], lb_result._mapping['answers']
    used_hints = used_hints or {}
    answers = answers or {}
    flag_key = f"flag{number}"

    if flag_key in answers: return jsonify({"status": "already_solved", "message": "Soal sudah diselesaikan."})
    if flag_key in used_hints: return jsonify({"status": "already_taken", "hint": hint_info['hint_text']})

    penalty = hint_info.get('hint_penalty', 5)
    if score < penalty:
        return jsonify({"status": "insufficient_score", "message": f"Skor tidak cukup! Butuh {penalty} poin."}), 400
            
    new_score = score - penalty
    used_hints[flag_key] = True
    
    with engine.connect() as conn:
        conn.execute(text("UPDATE leaderboard SET score = :score, used_hints = :used_hints WHERE username = :username"),
                     {"score": new_score, "used_hints": json.dumps(used_hints), "username": session['username']})
        conn.commit()

    return jsonify({"status": "success", "hint": hint_info['hint_text'], "new_score": new_score})

@app.route('/leaderboard')
def leaderboard():
    if 'username' not in session: return redirect(url_for('login'))
    with engine.connect() as conn:
        data = conn.execute(text("SELECT name, score, last_submit FROM leaderboard ORDER BY score DESC, last_submit ASC")).fetchall()
    return render_template('leaderboard.html', data=[row._mapping for row in data], name=session.get('name'), role=session.get('role'))

@app.route('/admin/responses')
def view_responses():
    if session.get('role') != 'admin': return redirect(url_for('flags'))
    
    with engine.connect() as conn:
        results = conn.execute(text("SELECT name, score, answers, active_times, used_hints FROM leaderboard ORDER BY score DESC, last_submit ASC")).fetchall()

    responses = []
    for row in results:
        res_map = row._mapping
        answers_data = res_map.get('answers') or {}
        active_times_data = res_map.get('active_times') or {}
        
        for flag_key, answer_info in answers_data.items():
            active_seconds = active_times_data.get(flag_key, 0)
            answer_info['active_time'] = str(timedelta(seconds=int(active_seconds)))
            
        responses.append({
            "name": res_map.get('name'), "score": res_map.get('score'), "answers": answers_data, 
            "used_hints": res_map.get('used_hints') or {}
        })

    return render_template('admin_responses.html', responses=responses, name=session.get('name'))

@app.route('/admin/stats')
def admin_stats():
    if session.get('role') != 'admin': return redirect(url_for('flags'))

    with engine.connect() as conn:
        all_answers_results = conn.execute(text("SELECT answers FROM leaderboard")).fetchall()
        challenges_results = conn.execute(text("SELECT id, title FROM challenges ORDER BY id")).fetchall()

    challenges = [row._mapping for row in challenges_results]
    all_answers = [row._mapping['answers'] for row in all_answers_results if row._mapping['answers']]
    solve_counts = {f"flag{c['id']}": 0 for c in challenges}
    
    for user_answers in all_answers:
        for flag_key in user_answers.keys():
            if flag_key in solve_counts:
                solve_counts[flag_key] += 1
    
    stats_data = [{"id": c['id'], "title": c['title'], "solves": solve_counts.get(f"flag{c['id']}", 0)} for c in challenges]

    return render_template('admin_stats.html', stats=stats_data, name=session.get('name'))

@app.route('/reset_leaderboard')
def reset_leaderboard():
    if session.get('role') != 'admin': return redirect(url_for('flags'))
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE leaderboard;"))
        conn.commit()
    session.clear()
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))