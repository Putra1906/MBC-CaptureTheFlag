import os
import sys
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash, check_password_hash # Komentar ini untuk development

# --- KONFIGURASI APLIKASI ---
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')

# --- KONEKSI DATABASE POSTGRESQL ---
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("FATAL: DATABASE_URL tidak ditemukan di environment variables.")
engine = create_engine(DATABASE_URL)

# --- HELPER FUNCTIONS ---
def admin_required(f):
    """Decorator to ensure only admin users can access a route."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Anda harus login untuk mengakses halaman ini.', 'error')
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Akses ditolak: Anda tidak memiliki izin admin.', 'error')
            return redirect(url_for('flags')) # Redirect non-admins to flags page
        return f(*args, **kwargs)
    return decorated_function

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

        # --- PERINGATAN: INI UNTUK PENGEMBANGAN SAJA. JANGAN GUNAKAN DI PRODUKSI TANPA HASHING SANDI! ---
        # Membandingkan sandi langsung (tanpa hashing)
        if result and password == result._mapping['password_hash']:
            user_data = result._mapping
            session.clear()
            session['username'] = user_data['username']
            session['name'] = user_data['name']
            session['role'] = user_data['role']

            with engine.connect() as conn:
                # Ensure user exists in leaderboard or create them
                conn.execute(text("""
                    INSERT INTO leaderboard (username, name) VALUES (:username, :name)
                    ON CONFLICT (username) DO NOTHING
                """), {"username": session['username'], "name": session['name']})
                conn.commit()
            return redirect(url_for('flags'))
        else:
            flash('User ID atau Access Key salah.', 'error')
            
        return render_template('login.html', error='User ID atau Access Key salah.')
    return render_template('login.html')

@app.route('/flags', methods=['GET', 'POST'])
def flags():
    if 'username' not in session: return redirect(url_for('login'))

    feedback = None

    if request.method == 'POST':
        challenge_id = request.form.get('challenge_id')
        user_flag = request.form.get('flag', '').strip()

        with engine.connect() as conn:
            challenge = conn.execute(
                text("SELECT id, flag, points FROM challenges WHERE id = :id"),
                {"id": challenge_id}
            ).fetchone()
            lb_result = conn.execute(
                text("SELECT score, answers FROM leaderboard WHERE username = :username"),
                {"username": session['username']}
            ).fetchone()

        if not challenge or not lb_result:
            feedback = {"challenge_id": int(challenge_id), "correct": False, "message": "Tantangan tidak ditemukan."}
        else:
            answers = lb_result._mapping['answers'] or {}
            flag_key = f"flag{challenge_id}"
            if flag_key in answers:
                feedback = {"challenge_id": int(challenge_id), "correct": True, "message": "Sudah pernah dijawab."}
            elif user_flag == challenge._mapping['flag']:
                current_score = lb_result._mapping['score'] + challenge._mapping['points']
                answers[flag_key] = {
                    "answer": user_flag,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                with engine.connect() as conn:
                    conn.execute(
                        text("""
                            UPDATE leaderboard
                            SET score = :score,
                                answers = :answers,
                                last_submit = :last_submit
                            WHERE username = :username
                        """),
                        {
                            "score": current_score,
                            "answers": json.dumps(answers),
                            "last_submit": datetime.now(),
                            "username": session['username']
                        }
                    )
                    conn.commit()
                feedback = {"challenge_id": int(challenge_id), "correct": True, "message": f"✅ Flag benar! +{challenge._mapping['points']} poin."}
            else:
                feedback = {"challenge_id": int(challenge_id), "correct": False, "message": "❌ Flag salah. Coba lagi!"}

        # Jika AJAX, return JSON
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(feedback)

    with engine.connect() as conn:
        challenges_result = conn.execute(
            text("SELECT id, vm_name, vm_ip, points FROM challenges ORDER BY id")
        ).fetchall()
        challenges = [row._mapping for row in challenges_result]
        result = conn.execute(
            text("SELECT answers FROM leaderboard WHERE username = :username"),
            {"username": session['username']}
        ).fetchone()

    solved_flags = result[0].keys() if result and result[0] else []

    return render_template('flags.html',
                           name=session.get('name'),
                           challenges=challenges,
                           solved_flags=list(solved_flags),
                           role=session.get('role'),
                           feedback=feedback)

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

@app.route('/leaderboard')
def leaderboard():
    if 'username' not in session: return redirect(url_for('login'))

    with engine.connect() as conn:
        results = conn.execute(text("SELECT name, score, last_submit FROM leaderboard ORDER BY score DESC, last_submit ASC")).fetchall()

    data = []
    for row in results:
        row_data = dict(row._mapping)
        if row_data.get('last_submit'):
            # Ensure last_submit is a datetime object before adding timedelta
            # It might be stored as a string if inserted directly from Python's datetime.now().strftime
            if isinstance(row_data['last_submit'], str):
                try:
                    row_data['last_submit'] = datetime.strptime(row_data['last_submit'], "%Y-%m-%d %H:%M:%S.%f")
                except ValueError: # Handle cases where microseconds might not be present
                    row_data['last_submit'] = datetime.strptime(row_data['last_submit'], "%Y-%m-%d %H:%M:%S")
            row_data['last_submit'] = row_data['last_submit'] + timedelta(hours=7)
        data.append(row_data)

    return render_template('leaderboard.html', data=data, name=session.get('name'), role=session.get('role'))

@app.route('/admin/console')
@admin_required
def admin_console():
    total_users = 0
    total_challenges = 0
    solved_challenges_count = 0
    average_score = 0.0

    with engine.connect() as conn:
        # Total Users
        total_users_result = conn.execute(text("SELECT COUNT(*) FROM users")).fetchone()
        if total_users_result:
            total_users = total_users_result[0]

        # Total Challenges
        total_challenges_result = conn.execute(text("SELECT COUNT(*) FROM challenges")).fetchone()
        if total_challenges_result:
            total_challenges = total_challenges_result[0]

        # Solved Challenges (overall, count distinct flags solved across all users)
        all_answers_results = conn.execute(text("SELECT answers FROM leaderboard")).fetchall()
        unique_solved_flags = set()
        
        for row in all_answers_results:
            answers = row._mapping['answers']
            if answers:
                unique_solved_flags.update(answers.keys())
        solved_challenges_count = len(unique_solved_flags)

        # Average Score
        score_results = conn.execute(text("SELECT score FROM leaderboard")).fetchall()
        if score_results:
            scores = [row[0] for row in score_results]
            if scores:
                average_score = sum(scores) / len(scores)

    return render_template('admin_console.html',
                           name=session.get('name'),
                           role=session.get('role'),
                           total_users=total_users,
                           total_challenges=total_challenges,
                           solved_challenges=solved_challenges_count,
                           average_score=average_score)

@app.route('/admin/responses')
@admin_required
def view_submission_logs(): # Renamed from view_responses
    with engine.connect() as conn:
        results = conn.execute(text("SELECT name, score, answers, active_times FROM leaderboard ORDER BY score DESC, last_submit ASC")).fetchall()

    responses = []
    for row in results:
        res_map = row._mapping
        answers_data = res_map.get('answers') or {}
        active_times_data = res_map.get('active_times') or {}

        for flag_key, answer_info in answers_data.items():
            utc_time = datetime.strptime(answer_info['timestamp'], "%Y-%m-%d %H:%M:%S")
            wib_time = utc_time + timedelta(hours=7)
            answer_info['timestamp'] = wib_time.strftime("%Y-%m-%d %H:%M:%S")
            active_seconds = active_times_data.get(flag_key, 0)
            answer_info['active_time'] = str(timedelta(seconds=int(active_seconds)))

        responses.append({
            "name": res_map.get('name'), "score": res_map.get('score'), "answers": answers_data
        })

    return render_template('submission_logs.html', responses=responses, name=session.get('name'), role=session.get('role')) # Render new HTML

@app.route('/admin/stats')
@admin_required
def admin_stats():
    with engine.connect() as conn:
        all_answers_results = conn.execute(text("SELECT answers FROM leaderboard")).fetchall()
        challenges_results = conn.execute(text("SELECT id, vm_name FROM challenges ORDER BY id")).fetchall()

    challenges = [row._mapping for row in challenges_results]
    all_answers = [row._mapping['answers'] for row in all_answers_results if row._mapping['answers']]
    solve_counts = {f"flag{c['id']}": 0 for c in challenges}

    for user_answers in all_answers:
        for flag_key in user_answers.keys():
            if flag_key in solve_counts:
                solve_counts[flag_key] += 1

    stats_data = [{"id": c['id'], "title": c['vm_name'], "solves": solve_counts.get(f"flag{c['id']}", 0)} for c in challenges]

    return render_template('admin_stats.html', stats=stats_data, name=session.get('name'), role=session.get('role'))

@app.route('/reset_leaderboard')
@admin_required
def reset_leaderboard():
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE leaderboard;"))
        conn.commit()
    flash('Leaderboard berhasil direset!', 'success')
    return redirect(url_for('admin_console'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Anda telah berhasil logout.', 'success')
    return redirect(url_for('login'))

# --- USER MANAGEMENT ROUTES ---

@app.route('/admin/users/add', methods=['GET', 'POST'])
@admin_required
def admin_add_user():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        name = request.form['name'].strip()
        role = request.form.get('role', 'user') # Default to 'user'

        if not username or not password or not name:
            flash('Semua kolom harus diisi.', 'error')
            return render_template('admin_add_user.html', name=session.get('name'), role=session.get('role'))

        # --- PERINGATAN: INI UNTUK PENGEMBANGAN SAJA. JANGAN GUNAKAN DI PRODUKSI TANPA HASHING SANDI! ---
        # hashed_password = generate_password_hash(password) # Dikomentari
        plain_password = password # Menggunakan sandi plain text untuk pengembangan

        with engine.connect() as conn:
            try:
                # Insert into users table
                conn.execute(
                    text("INSERT INTO users (username, password_hash, name, role) VALUES (:username, :password_hash, :name, :role)"),
                    {"username": username, "password_hash": plain_password, "name": name, "role": role}
                )
                # Insert into leaderboard table (score 0, empty answers/hints/active_times)
                conn.execute(
                    text("INSERT INTO leaderboard (username, name, score, answers, used_hints, active_times) VALUES (:username, :name, 0, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb)"),
                    {"username": username, "name": name}
                )
                conn.commit()
                flash(f'Pengguna "{username}" berhasil ditambahkan!', 'success')
                return redirect(url_for('admin_manage_users'))
            except Exception as e:
                conn.rollback()
                if "duplicate key value violates unique constraint" in str(e):
                    flash(f'Gagal menambahkan pengguna: Username "{username}" sudah ada.', 'error')
                else:
                    flash(f'Terjadi kesalahan: {e}', 'error')
    return render_template('admin_add_user.html', name=session.get('name'), role=session.get('role'))

@app.route('/admin/users/manage', methods=['GET'])
@admin_required
def admin_manage_users():
    users_data = []
    with engine.connect() as conn:
        # Join users and leaderboard to get comprehensive data
        results = conn.execute(text("""
            SELECT u.username, u.name, u.role, l.score, l.last_submit
            FROM users u LEFT JOIN leaderboard l ON u.username = l.username
            ORDER BY u.username
        """)).fetchall()
        users_data = [dict(row._mapping) for row in results]
    return render_template('admin_manage_users.html', users=users_data, name=session.get('name'), role=session.get('role'))

@app.route('/admin/users/edit/<username>', methods=['GET', 'POST'])
@admin_required
def admin_edit_user(username):
    user = None
    with engine.connect() as conn:
        user_result = conn.execute(text("SELECT username, name, role, password_hash FROM users WHERE username = :username"), {"username": username}).fetchone()
        if user_result:
            user = dict(user_result._mapping)
        else:
            flash('Pengguna tidak ditemukan.', 'error')
            return redirect(url_for('admin_manage_users'))

    if request.method == 'POST':
        new_name = request.form['name'].strip()
        new_role = request.form.get('role', 'user')
        new_password = request.form['password'].strip() # Optional password change

        if not new_name:
            flash('Nama tidak boleh kosong.', 'error')
            return render_template('admin_edit_user.html', user=user, name=session.get('name'), role=session.get('role'))

        with engine.connect() as conn:
            try:
                # Update users table
                update_query_users = text("UPDATE users SET name = :name, role = :role WHERE username = :username")
                params_users = {"name": new_name, "role": new_role, "username": username}
                conn.execute(update_query_users, params_users)

                # Update password if provided
                if new_password:
                    # --- PERINGATAN: INI UNTUK PENGEMBANGAN SAJA. JANGAN GUNAKAN DI PRODUKSI TANPA HASHING SANDI! ---
                    # hashed_password = generate_password_hash(new_password) # Dikomentari
                    plain_password = new_password # Menggunakan sandi plain text untuk pengembangan
                    conn.execute(text("UPDATE users SET password_hash = :password_hash WHERE username = :username"),
                                 {"password_hash": plain_password, "username": username}) # Menggunakan plain_password

                # Update leaderboard table (only name, other fields are managed elsewhere)
                update_query_leaderboard = text("UPDATE leaderboard SET name = :name WHERE username = :username")
                params_leaderboard = {"name": new_name, "username": username}
                conn.execute(update_query_leaderboard, params_leaderboard)
                
                conn.commit()
                flash(f'Pengguna "{username}" berhasil diperbarui!', 'success')
                return redirect(url_for('admin_manage_users'))
            except Exception as e:
                conn.rollback()
                flash(f'Terjadi kesalahan saat memperbarui pengguna: {e}', 'error')

    return render_template('admin_edit_user.html', user=user, name=session.get('name'), role=session.get('role'))

@app.route('/admin/users/delete/<username>', methods=['POST'])
@admin_required
def admin_delete_user(username):
    with engine.connect() as conn:
        try:
            conn.execute(text("DELETE FROM users WHERE username = :username"), {"username": username})
            conn.commit()
            flash(f'Pengguna "{username}" berhasil dihapus.', 'success')
        except Exception as e:
            conn.rollback()
            flash(f'Terjadi kesalahan saat menghapus pengguna: {e}', 'error')
    return redirect(url_for('admin_manage_users'))

@app.route('/admin/users/reset_all_user_progress', methods=['POST'])
@admin_required
def admin_reset_all_user_progress():
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                UPDATE leaderboard
                SET score = 0,
                    answers = '{}'::jsonb,
                    used_hints = '{}'::jsonb,
                    active_times = '{}'::jsonb;
            """))
            conn.commit()
            flash('Semua progres pengguna berhasil direset!', 'success')
        except Exception as e:
            conn.rollback()
            flash(f'Terjadi kesalahan saat mereset progres pengguna: {e}', 'error')
    return redirect(url_for('admin_console'))


# --- CHALLENGE MANAGEMENT ROUTES ---

@app.route('/admin/challenges/add', methods=['GET', 'POST'])
@admin_required
def admin_add_challenge():
    if request.method == 'POST':
        vm_name = request.form['vm_name'].strip()
        vm_ip = request.form['vm_ip'].strip()
        flag = request.form['flag'].strip()
        points = request.form.get('points', type=int)

        if not all([vm_name, vm_ip, flag, points]):
            flash('Semua kolom harus diisi.', 'error')
            return render_template('admin_add_challenge.html', name=session.get('name'), role=session.get('role'))

        with engine.connect() as conn:
            try:
                conn.execute(
                    text("INSERT INTO challenges (vm_name, vm_ip, flag, points) VALUES (:vm_name, :vm_ip, :flag, :points)"),
                    {"vm_name": vm_name, "vm_ip": vm_ip, "flag": flag, "points": points}
                )
                conn.commit()
                flash(f'Tantangan "{vm_name}" berhasil ditambahkan!', 'success')
                return redirect(url_for('admin_manage_challenges'))
            except Exception as e:
                conn.rollback()
                flash(f'Terjadi kesalahan saat menambahkan tantangan: {e}', 'error')
    return render_template('admin_add_challenge.html', name=session.get('name'), role=session.get('role'))

@app.route('/admin/challenges/manage', methods=['GET'])
@admin_required
def admin_manage_challenges():
    challenges_data = []
    with engine.connect() as conn:
        results = conn.execute(text("SELECT id, vm_name, vm_ip, flag, points FROM challenges ORDER BY id")).fetchall()
        challenges_data = [dict(row._mapping) for row in results]
    return render_template('admin_manage_challenges.html', challenges=challenges_data, name=session.get('name'), role=session.get('role'))

@app.route('/admin/challenges/edit/<int:challenge_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_challenge(challenge_id):
    challenge = None
    with engine.connect() as conn:
        challenge_result = conn.execute(text("SELECT id, vm_name, vm_ip, flag, points FROM challenges WHERE id = :id"), {"id": challenge_id}).fetchone()
        if challenge_result:
            challenge = dict(challenge_result._mapping)
        else:
            flash('Tantangan tidak ditemukan.', 'error')
            return redirect(url_for('admin_manage_challenges'))

    if request.method == 'POST':
        new_vm_name = request.form['vm_name'].strip()
        new_vm_ip = request.form['vm_ip'].strip()
        new_flag = request.form['flag'].strip()
        new_points = request.form.get('points', type=int)

        if not all([new_vm_name, new_vm_ip, new_flag, new_points]):
            flash('Semua kolom harus diisi.', 'error')
            return render_template('admin_edit_challenge.html', challenge=challenge, name=session.get('name'), role=session.get('role'))

        with engine.connect() as conn:
            try:
                conn.execute(
                    text("""
                        UPDATE challenges
                        SET vm_name = :vm_name, vm_ip = :vm_ip, flag = :flag, points = :points
                        WHERE id = :id
                    """),
                    {"vm_name": new_vm_name, "vm_ip": new_vm_ip, "flag": new_flag, "points": new_points, "id": challenge_id}
                )
                conn.commit()
                flash(f'Tantangan "{new_vm_name}" berhasil diperbarui!', 'success')
                return redirect(url_for('admin_manage_challenges'))
            except Exception as e:
                conn.rollback()
                flash(f'Terjadi kesalahan saat memperbarui tantangan: {e}', 'error')

    return render_template('admin_edit_challenge.html', challenge=challenge, name=session.get('name'), role=session.get('role'))

@app.route('/admin/challenges/delete/<int:challenge_id>', methods=['POST'])
@admin_required
def admin_delete_challenge(challenge_id):
    with engine.connect() as conn:
        try:
            conn.execute(text("DELETE FROM challenges WHERE id = :id"), {"id": challenge_id})
            conn.commit()
            flash(f'Tantangan ID {challenge_id} berhasil dihapus.', 'success')
        except Exception as e:
            conn.rollback()
            flash(f'Terjadi kesalahan saat menghapus tantangan: {e}', 'error')
    return redirect(url_for('admin_manage_challenges'))


if __name__ == "__main__":
    app.run(debug=True)
