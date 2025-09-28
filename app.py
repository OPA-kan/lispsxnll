from flask import flash, Flask, request, jsonify, render_template, send_file, redirect, url_for, session, Blueprint
from datetime import datetime, timezone, timedelta, date
# 以下のAI関連のインポートは、必要に応じてModelsファイルに移動または統合
from DREGING_AI_Calender_API import parse_schedule, create_ai_calendar_event, create_timetable_calendar_event, get_google_calendar_events
from api_handler import generate_report_with_data, summarize_file, create_test_from_file, analyze_essay_with_gemini, check_plagiarism_with_db
import io
import base64
import os
from werkzeug.utils import secure_filename
import weasyprint
from sqlalchemy import or_
from google_auth_oauthlib.flow import Flow
import google.oauth2.credentials
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import json
import collections
import pytz
import matplotlib as mpl
import matplotlib.pyplot as plt
mpl.rcParams['font.family'] = 'Noto Sans JP'
mpl.rcParams['font.sans-serif'] = ['Noto Sans JP', 'IPAexGothic']
import pandas as pd
from dotenv import load_dotenv
load_dotenv()
import secrets
import time
from email.mime.text import MIMEText
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from university_list import university_data
#デバック
import smtplib
from flask_socketio import SocketIO, emit, join_room, leave_room

# SNS機能のBlueprintをインポート
from community import community_bp, circle_management_bp, init_socketio
from dm import dm_bp, init_dm_socketio

# 循環インポートを解消するため、extensions.pyからdbをインポート
from extensions import db

# models.pyはdbオブジェクトをインポートするようになったので、
# app.pyからはdbオブジェクト以外のモデルをインポート
from models import User, CourseUniversityMapping, Submission, SummaryHistory, TestHistory, Question, Course, Grade, Query, Announcement, Semester, Timetable, UniversitySettings, Post, Comment, Circle, Event, DirectMessage, DirectMessageConversation

# Flaskアプリケーションの初期化
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
app.config['REMEMBER_COOKIE_SECURE'] = os.environ.get('REMEMBER_COOKIE_SECURE', 'false').lower() == 'true'

# Flask-SocketIOのインスタンスを作成
socketio = SocketIO(app)

# Simple in-memory cache (single-process)
_SIMPLE_CACHE = {}
def _cache_set(key, value, ttl=300):
    _SIMPLE_CACHE[key] = (value, time.time() + ttl)
def _cache_get(key):
    v = _SIMPLE_CACHE.get(key)
    if not v:
        return None
    value, exp = v
    if exp < time.time():
        _SIMPLE_CACHE.pop(key, None)
        return None
    return value

# Flask-Login の設定
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# CSRF: lightweight token for form posts
def generate_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token

@app.context_processor
def inject_csrf():
    return {'csrf_token': generate_csrf_token}

# Google credentials with refresh
def get_google_credentials_or_redirect():
    if 'google_credentials' not in session:
        return {"success": False, "redirect_url": url_for('login')}
    creds_dict = session['google_credentials']
    credentials = google.oauth2.credentials.Credentials.from_authorized_user_info(
        info=creds_dict,
        scopes=creds_dict.get('scopes')
    )
    try:
        if credentials and getattr(credentials, 'expired', False) and credentials.refresh_token:
            credentials.refresh(GoogleAuthRequest())
            session['google_credentials'] = {
                'token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_uri': credentials.token_uri,
                'client_id': credentials.client_id,
                'client_secret': credentials.client_secret,
                'scopes': credentials.scopes,
                'expiry': credentials.expiry.isoformat() if credentials.expiry else None
            }
    except Exception:
        return {"success": False, "redirect_url": url_for('login')}
    return credentials

# Google OAuth2 の設定
CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI')

if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables must be set.")

AUTH_URI = 'https://accounts.google.com/o/oauth2/auth'
TOKEN_URI = 'https://accounts.google.com/o/oauth2/token'
SCOPE = [
    'https://www.googleapis.com/auth/userinfo.email',
    'openid',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.send'
]

if 'FLASK_ENV' in os.environ and os.environ['FLASK_ENV'] == 'development':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# データベース設定
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# extensions.pyで定義されたdbオブジェクトをFlaskアプリに紐づける
db.init_app(app)

# メール設定
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.environ.get("MAIL_PASSWORD")
mail = Mail(app)

# SNS機能のBlueprintをアプリケーションに登録
from community import community_bp, circle_management_bp
app.register_blueprint(community_bp)
app.register_blueprint(circle_management_bp, url_prefix='/community/circles')
app.register_blueprint(dm_bp, url_prefix='/dm')

# SocketIOインスタンスをコミュニティBlueprintに渡す
init_socketio(socketio)
init_dm_socketio(socketio)

# ==================== Flask-Login関連 ====================
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ==================== ルーティングとAPIエンドポイント ====================
@app.route('/university_settings', methods=['GET', 'POST'])
@login_required
def university_settings():
    if request.method == 'POST':
        data = request.json
        spring_timetable = data.get('spring_timetable_map')
        fall_timetable = data.get('fall_timetable_map')
        spring_start_str = data.get('spring_start')
        spring_end_str = data.get('spring_end')
        fall_start_str = data.get('fall_start')
        fall_end_str = data.get('fall_end')
        
        if not all([spring_timetable, fall_timetable, spring_start_str, spring_end_str, fall_start_str, fall_end_str]):
            return jsonify({"success": False, "error": "すべての情報を入力してください。"}), 400
        
        try:
            settings = UniversitySettings.query.filter_by(university_id=current_user.university_id).first()
            if settings:
                settings.spring_timetable_map = spring_timetable
                settings.fall_timetable_map = fall_timetable
                settings.spring_start_date = datetime.strptime(spring_start_str, '%Y-%m-%d').date()
                settings.spring_end_date = datetime.strptime(spring_end_str, '%Y-%m-%d').date()
                settings.fall_start_date = datetime.strptime(fall_start_str, '%Y-%m-%d').date()
                settings.fall_end_date = datetime.strptime(fall_end_str, '%Y-%m-%d').date()
            else:
                new_settings = UniversitySettings(
                    university_id=current_user.university_id,
                    spring_timetable_map=spring_timetable,
                    fall_timetable_map=fall_timetable,
                    spring_start_date=datetime.strptime(spring_start_str, '%Y-%m-%d').date(),
                    spring_end_date=datetime.strptime(spring_end_str, '%Y-%m-%d').date(),
                    fall_start_date=datetime.strptime(fall_start_str, '%Y-%m-%d').date(),
                    fall_end_date=datetime.strptime(fall_end_str, '%Y-%m-%d').date()
                )
                db.session.add(new_settings)
            
            db.session.commit()
            return jsonify({"success": True, "message": "大学設定が正常に更新されました。"}), 200
        
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": str(e)}), 500

    settings = UniversitySettings.query.filter_by(university_id=current_user.university_id).first()
    
    if settings:
        settings_dict = settings.to_dict()
    else:
        settings_dict = None
        
    return render_template('university_settings.html', settings=settings_dict)

@app.route('/timetable_google', methods=['GET'])
@login_required
def get_user_timetable_from_google():
    if 'google_credentials' not in session:
        return jsonify({"success": False, "redirect_url": url_for('login')}), 401
    
    try:
        credentials = get_google_credentials_or_redirect()
        if isinstance(credentials, dict):
            return jsonify(credentials), 401

        user_tz = pytz.timezone(current_user.timezone or 'Asia/Tokyo')
        now_tz = datetime.now(user_tz)
        start_of_day = user_tz.localize(datetime(now_tz.year, now_tz.month, now_tz.day, 0, 0, 0))
        end_of_day = start_of_day + timedelta(days=31)

        google_events = get_google_calendar_events(credentials, start_of_day, end_of_day)
        
        return jsonify({"success": True, "events": google_events})

    except Exception as e:
        return jsonify({"error": f"予定の取得に失敗しました: {e}"}), 500
    
@app.route('/add_course_from_timetable', methods=['POST'])
@login_required
def add_course_from_timetable():
    data = request.json
    course_name = data.get('course_name')
    professor_name = data.get('professor_name')
    
    if not course_name:
        return jsonify({"success": False, "error": "授業名がありません。"}), 400
    
    try:
        existing_course = Course.query.filter_by(
            course_name=course_name,
            professor_name=professor_name,
            university_id=current_user.university_id
        ).first()

        if not existing_course:
            new_course = Course(
                course_name=course_name,
                professor_name=professor_name,
                user_id=current_user.id,
                university_id=current_user.university_id,
                review="時間割から自動で追加",
                year=datetime.utcnow().year,
                credit=0,
                evaluation="-"
            )
            db.session.add(new_course)
            db.session.commit()
        
        return jsonify({"success": True, "message": "時間割から授業を追加しました。"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": f"時間割からの授業追加中にエラーが発生しました: {str(e)}"}), 500

@app.route('/add_timetable_entry', methods=['POST'])
@login_required
def add_timetable_entry():
    data = request.json
    course_name = data.get('course_name')
    professor_name = data.get('professor_name')
    day_of_week = data.get('day_of_week')
    period = data.get('period')
    classroom = data.get('classroom')
    selected_semester = data.get('semester')
    
    if not all([course_name, day_of_week, period, selected_semester]):
        return jsonify({"success": False, "error": "すべての項目は必須です。"}), 400
    
    try:
        settings = UniversitySettings.query.filter_by(university_id=current_user.university_id).first()
        if not settings:
            return jsonify({"success": False, "error": "大学設定が登録されていません。設定ページで入力してください。"}), 400
            
        if selected_semester == 'spring':
            timetable_map = settings.spring_timetable_map
            semester_start_date = settings.spring_start_date
            semester_end_date = settings.spring_end_date
        elif selected_semester == 'fall':
            timetable_map = settings.fall_timetable_map
            semester_start_date = settings.fall_start_date
            semester_end_date = settings.fall_end_date
        else:
            return jsonify({"success": False, "error": "無効な学期が選択されました。"}), 400
        
        period = str(period)
        if period not in timetable_map:
            return jsonify({"success": False, "error": "無効な時限です。大学設定を確認してください。"}), 400

        time_info = timetable_map[period]
        
        existing_course = Course.query.filter_by(
            course_name=course_name,
            professor_name=professor_name,
            university_id=current_user.university_id
        ).first()
        if not existing_course:
            new_course = Course(
                course_name=course_name,
                professor_name=professor_name,
                user_id=current_user.id,
                university_id=current_user.university_id,
                review="時間割から自動で追加",
                year=datetime.utcnow().year,
                credit=0,
                evaluation="-"
            )
            db.session.add(new_course)
            db.session.flush()

        existing_timetable_entry = Timetable.query.filter_by(
            user_id=current_user.id,
            day_of_week=day_of_week,
            period=period
        ).first()
        
        if existing_timetable_entry:
            existing_timetable_entry.course_name = course_name
            existing_timetable_entry.professor_name = professor_name
            existing_timetable_entry.classroom = classroom
        else:
            new_timetable_entry = Timetable(
                user_id=current_user.id,
                course_name=course_name,
                professor_name=professor_name,
                day_of_week=day_of_week,
                period=period,
                classroom=classroom
            )
            db.session.add(new_timetable_entry)
        
        day_mapping = {
            'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Saturday': 5, 'Sunday': 6
        }
        
        if not semester_start_date:
            return jsonify({"success": False, "error": "学期開始日が設定されていません。大学設定ページを確認してください。"}), 400
        
        start_date_obj = semester_start_date
        current_weekday = start_date_obj.weekday()
        target_weekday = day_mapping[day_of_week]
        days_until_first_class = (target_weekday - current_weekday + 7) % 7
        first_class_date = start_date_obj + timedelta(days=days_until_first_class)
        
        tz = pytz.timezone(current_user.timezone or 'Asia/Tokyo')
        
        start_time = tz.localize(datetime(
            year=first_class_date.year,
            month=first_class_date.month,
            day=first_class_date.day,
            hour=time_info['start_hour'],
            minute=time_info['start_minute']
        ))
        
        end_time = tz.localize(datetime(
            year=first_class_date.year,
            month=first_class_date.month,
            day=first_class_date.day,
            hour=time_info['end_hour'],
            minute=time_info['end_minute']
        ))
        
        utc_end_date = datetime.combine(semester_end_date, datetime.max.time(), tzinfo=tz).astimezone(pytz.utc)
        recurrence = [
            f"RRULE:FREQ=WEEKLY;BYDAY={day_of_week.upper()[:2]};UNTIL={utc_end_date.strftime('%Y%m%dT%H%M%SZ')}"
        ]
        
        credentials = get_google_credentials_or_redirect()
        if isinstance(credentials, dict):
            return jsonify(credentials), 401
        
        event_body = {
            'summary': f'{course_name} ({period}時限)',
            'location': classroom,
            'description': 'DREGING: 時間割から自動登録',
            'start': {'dateTime': start_time.isoformat(), 'timeZone': (current_user.timezone or 'Asia/Tokyo')},
            'end': {'dateTime': end_time.isoformat(), 'timeZone': (current_user.timezone or 'Asia/Tokyo')},
            'recurrence': recurrence,
            'extendedProperties': {
                'private': {'source': 'timetable'}
            }
        }
        
        created_events = create_timetable_calendar_event(event_body, credentials)
        
        db.session.commit()
        return jsonify({"success": True, "message": "時間割とGoogleカレンダーに登録しました。"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": f"時間割登録中に予期せぬエラーが発生しました: {str(e)}"}), 500

@app.route('/report_creator_page', methods=['GET'])
@login_required
def report_creator_page():
    return render_template('Essay_Creator.html')

@app.route('/view_report/<int:submission_id>')
@login_required
def view_report_page(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if not submission.is_ai_generated:
        return jsonify({"success": False, "error": "このレポートはAIによって生成されたものではありません。"}), 403
    
    report_data = json.loads(submission.text)
    
    uploads_dir = "uploads"
    os.makedirs(uploads_dir, exist_ok=True)
    chart_paths = []
    try:
        for i, table in enumerate(report_data.get("data_tables", [])):
            if len(table.get("rows", [])) > 0 and len(table.get("headers", [])) > 1:
                df = pd.DataFrame(table["rows"], columns=table["headers"])
                df = df.apply(pd.to_numeric, errors='ignore')
                
                plt.figure(figsize=(8, 6))
                df.set_index(table["headers"][0]).plot(kind='bar', ax=plt.gca())
                plt.title(table["title"], pad=20)
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                
                chart_filename = f"chart_{submission_id}_{i}.png"
                chart_path = os.path.join(uploads_dir, chart_filename)
                plt.savefig(chart_path)
                plt.close()
                chart_paths.append(chart_filename)
    except Exception as e:
        report_data['chart_error'] = "グラフの生成に失敗しました。"

    return render_template('report_viewer.html', report=report_data, charts=chart_paths, submission_id=submission_id)

@app.route('/download_report_pdf/<int:submission_id>')
@login_required
def download_report_pdf(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if not submission.is_ai_generated:
        return jsonify({"success": False, "error": "このレポートはAIによって生成されたものではありません。"}), 403
    
    report_data = json.loads(submission.text)

    uploads_dir = "uploads"
    os.makedirs(uploads_dir, exist_ok=True)
    chart_paths = []
    try:
        for i, table in enumerate(report_data.get("data_tables", [])):
            if len(table.get("rows", [])) > 0 and len(table.get("headers", [])) > 1:
                df = pd.DataFrame(table["rows"], columns=table["headers"])
                df = df.apply(pd.to_numeric, errors='ignore')
                plt.figure(figsize=(8, 6))
                df.set_index(table["headers"][0]).plot(kind='bar', ax=plt.gca())
                plt.title(table["title"], pad=20)
                plt.xticks(rotation=45, ha='right')
                plt.tight_layout()
                chart_filename = f"chart_{submission_id}_{i}_pdf.png"
                chart_path = os.path.join(uploads_dir, chart_filename)
                plt.savefig(chart_path)
                plt.close()
                chart_paths.append(chart_path)
    except Exception as e:
        report_data['chart_error'] = "グラフの生成に失敗しました。"

    rendered_html = render_template('report_template.html', report=report_data, charts=[os.path.join('uploads', p) for p in chart_paths])
    
    html = weasyprint.HTML(string=rendered_html, base_url=request.url_root)
    pdf_file = html.write_pdf()
    
    for path in chart_paths:
        if os.path.exists(path):
            os.remove(path)
    
    return send_file(io.BytesIO(pdf_file),
                     as_attachment=True,
                     download_name=f"{report_data.get('title', '調査報告書')}.pdf",
                     mimetype='application/pdf')

@app.route('/create_report', methods=['POST'])
@login_required
def create_report():
    course_id = request.form.get('course_id')
    topic = request.form.get('topic')
    word_count_str = request.form.get('word_count')
    source_files = request.files.getlist('source_file')
    
    if not all([topic, word_count_str, course_id]):
        return jsonify({"success": False, "error": "エッセイのテーマ、文字数、関連授業をすべて入力してください。"}), 400
    if len(source_files) > 3:
        return jsonify({"success": False, "error": "アップロードできるファイルは3個までです。"}), 400
        
    try:
        word_count = int(word_count_str)
    except ValueError:
        return jsonify({"success": False, "error": "文字数は半角数字で入力してください。"}), 400
    
    uploads_dir = "uploads"
    os.makedirs(uploads_dir, exist_ok=True)
    file_paths = []
    
    for source_file in source_files:
        if source_file and source_file.filename != '':
            file_path = os.path.join(uploads_dir, source_file.filename)
            source_file.save(file_path)
            file_paths.append(file_path)
    
    try:
        report_data = generate_report_with_data(topic, word_count, file_paths)
    except Exception as e:
        return jsonify({"success": False, "error": "AIがレポートを生成できませんでした。時間を置いて再度お試しください。"}), 500
    
    if "error" in report_data:
        return jsonify({"success": False, "error": report_data["error"]})
    
    try:
        new_submission = Submission(
            user_id=current_user.id,
            course_id=course_id,
            text=json.dumps(report_data),
            is_ai_generated=True,
            date_submitted=datetime.now(pytz.utc)
        )
        db.session.add(new_submission)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": "レポートの保存中にエラーが発生しました。"}), 500
    finally:
        for path in file_paths:
            if os.path.exists(path):
                os.remove(path)

    return jsonify({
        'success': True,
        'message': 'レポートが正常に生成され、保存されました。',
        'report_id': new_submission.id
    })

@app.route('/admin/university_settings', methods=['GET'])
@login_required
def admin_university_settings():
    if not getattr(current_user, 'is_admin', False):
        return redirect(url_for('dashboard'))

    settings = UniversitySettings.query.filter_by(university_id=current_user.university_id).first()
    
    if settings:
        settings_dict = settings.to_dict()
    else:
        settings_dict = None
        
    return render_template('admin_university_settings.html', settings=settings_dict)

@app.route('/api/courses')
@login_required
def get_courses():
    if not current_user.university_id:
        return jsonify([])

    cached = _cache_get(f"courses:{current_user.university_id}")
    if cached is not None:
        return jsonify(cached)

    courses = Course.query.filter_by(university_id=current_user.university_id).all()
    unique_courses = {}
    for course in courses:
        key = (course.course_name, course.professor_name)
        if key not in unique_courses:
            unique_courses[key] = {
                'id': course.id,
                'course_name': course.course_name,
                'professor_name': course.professor_name if course.professor_name else '不明',
                'year': course.year
            }
    result = list(unique_courses.values())
    _cache_set(f"courses:{current_user.university_id}", result, ttl=300)
    return jsonify(result)

@app.route('/')
def dashboard():
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    Announcement.query.filter(Announcement.date_posted < thirty_days_ago).delete()
    all_announcements = Announcement.query.order_by(Announcement.date_posted.desc()).all()
    if len(all_announcements) > 5:
        announcements_to_delete = all_announcements[5:]
        for ann in announcements_to_delete:
            db.session.delete(ann)
    db.session.commit()
    announcements_to_display = Announcement.query.order_by(Announcement.date_posted.desc()).all()
    announcements_data = [
        {'date': ann.date_posted.strftime('%Y/%m/%d'), 'message': ann.message}
        for ann in announcements_to_display
    ]
    return render_template('Dashboard_design.html', announcements=announcements_data)

@app.route('/generate_document', methods=['POST'])
@login_required
def generate_document():
    if 'summary' not in request.json:
        return jsonify({"error": "No summary provided"}), 400
    summary = request.json.get('summary')
    file_stream = io.BytesIO(summary.encode('utf-8'))
    return send_file(
        file_stream,
        as_attachment=True,
        download_name='summary.md',
        mimetype='text/markdown'
    )

@app.route('/login')
def login():
    flow = Flow.from_client_config(
        client_config={
            'web': {
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'auth_uri': AUTH_URI,
                'token_uri': TOKEN_URI
            }
        },
        scopes=SCOPE,
        redirect_uri=os.getenv('GOOGLE_REDIRECT_URI')
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent' # この行を追加
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    state = session.pop('state', None)
    if not state or state != request.args.get('state'):
        return "CSRF Error: State mismatch.", 400

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
            }
        },
        scopes=SCOPE,
        state=state,
        redirect_uri=REDIRECT_URI
    )
    
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    
    expiry_str = credentials.expiry.isoformat() if credentials.expiry else None
    
    session['google_credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes,
        'expiry': expiry_str
    }
    
    authed_session = flow.authorized_session()
    user_info_response = authed_session.get('https://www.googleapis.com/oauth2/v1/userinfo')
    user_info = user_info_response.json()
    email = user_info['email']
    
    user = User.query.filter_by(email=email).first()
    
    if not user:
     user = User(email=email, username=email.split('@')[0])
     user.password_hash = generate_password_hash("dummy_password_for_oauth")
     db.session.add(user)
     db.session.commit()
        
    login_user(user, remember=True)
    
    if user.university:
        return redirect(url_for('dashboard'))
    
    return redirect(url_for('register_profile'))

@app.route('/register_profile', methods=['GET', 'POST'])
@login_required
def register_profile():
    if request.method == 'POST':
        university_name = request.form.get('university')
        year = request.form.get('year')

        if not university_name or not year:
            return render_template('register_profile.html',
                                   error_message="大学名と学年をすべて選択してください。",
                                   university_data=university_data)
        
        uni_map = CourseUniversityMapping.query.filter_by(university_name=university_name).first()
        if not uni_map:
            uni_map = CourseUniversityMapping(university_name=university_name)
            db.session.add(uni_map)
            db.session.commit()
        
        current_user.university = university_name
        current_user.year = year
        current_user.university_id = uni_map.id
        db.session.commit()
        
        return redirect(url_for('dashboard'))

    return render_template('register_profile.html', university_data=university_data)

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            timezone = request.form.get('timezone')
            bio = request.form.get('bio')
            profile_picture = request.files.get('profile_picture')

            updated_fields = []

            if username and username != current_user.username:
                current_user.username = username
                updated_fields.append('ユーザー名')

            if timezone and timezone != current_user.timezone:
                current_user.timezone = timezone
                updated_fields.append('タイムゾーン')

            if bio and bio != current_user.bio:
                current_user.bio = bio
                updated_fields.append('自己紹介文')

            # --- プロフィール画像のアップロード処理 ---
            if profile_picture and profile_picture.filename != '':
                uploads_dir = os.path.join('static', 'profile_pictures')
                os.makedirs(uploads_dir, exist_ok=True)
                # ユーザーIDと元のファイル名を組み合わせ、一意で安全なファイル名を生成
                filename = secure_filename(f"{current_user.id}-{profile_picture.filename}")
                file_path = os.path.join(uploads_dir, filename)
                profile_picture.save(file_path)
                # データベースに保存するURLを更新
                current_user.profile_picture_url = url_for('static', filename=f'profile_pictures/{filename}')
                updated_fields.append('プロフィール画像')
            
            if not updated_fields:
                return jsonify({"success": False, "error": "更新対象がありません。"})

            db.session.commit()
            return jsonify({"success": True, "message": "、".join(updated_fields) + "を更新しました。"})
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": f"プロフィール更新中にエラーが発生しました: {str(e)}"})
    
    # GETリクエストの場合
    return render_template('profile.html', user=current_user)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('dashboard'))

@app.route('/calendar')
@login_required
def ai_calendar():
    return render_template('TESTER_Calender_design.html')

@app.route('/delete_event', methods=['POST'])
@login_required
def delete_event():
    creds = get_google_credentials_or_redirect()
    if isinstance(creds, dict):
        return jsonify(creds), 401

    data = request.json or {}
    event_id = data.get('event_id')
    if not event_id:
        return jsonify({"success": False, "error": "event_id が必要です。"}), 400

    try:
        from googleapiclient.discovery import build
        service = build('calendar', 'v3', credentials=creds)
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": f"削除に失敗しました: {str(e)}"}), 500

@app.route('/create_simple_event', methods=['POST'])
@login_required
def create_simple_event():
    creds = get_google_credentials_or_redirect()
    if isinstance(creds, dict):
        return jsonify(creds), 401

    data = request.json or {}
    date_str = data.get('date')
    start_time_str = data.get('start_time')
    end_time_str = data.get('end_time')
    duration_min = data.get('duration_minutes')
    title = data.get('title') or '予定'
    memo = data.get('memo')
    location = data.get('location')

    if not date_str or not start_time_str:
        return jsonify({"success": False, "error": "date と start_time は必須です。"}), 400

    try:
        tz = pytz.timezone(current_user.timezone or 'Asia/Tokyo')
        y, m, d = [int(x) for x in date_str.split('-')]
        sh, sm = [int(x) for x in start_time_str.split(':')]
        start_dt = tz.localize(datetime(y, m, d, sh, sm))
        if end_time_str:
            eh, em = [int(x) for x in end_time_str.split(':')]
            end_dt = tz.localize(datetime(y, m, d, eh, em))
        else:
            minutes = int(duration_min or 60)
            end_dt = start_dt + timedelta(minutes=minutes)

        event_body = {
            'summary': title,
            'description': memo,
            'location': location,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': current_user.timezone or 'Asia/Tokyo'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': current_user.timezone or 'Asia/Tokyo'},
            'extendedProperties': {
                'private': {'source': 'quick_add'}
            }
        }
        created = create_timetable_calendar_event(event_body, creds)
        return jsonify({"success": True, "event": created})
    except Exception as e:
        return jsonify({"success": False, "error": f"作成に失敗しました: {str(e)}"}), 500

@app.route('/update_event', methods=['POST'])
@login_required
def update_event():
    creds = get_google_credentials_or_redirect()
    if isinstance(creds, dict):
        return jsonify(creds), 401

    data = request.json or {}
    event_id = data.get('event_id')
    title = data.get('title') or '予定'
    memo = data.get('memo')
    location = data.get('location')
    date_str = data.get('date')
    start_time_str = data.get('start_time')
    end_time_str = data.get('end_time')

    if not event_id or not date_str or not start_time_str or not end_time_str:
        return jsonify({"success": False, "error": "必須項目が不足しています。"}), 400

    try:
        tz = pytz.timezone(current_user.timezone or 'Asia/Tokyo')
        y, m, d = [int(x) for x in date_str.split('-')]
        sh, sm = [int(x) for x in start_time_str.split(':')]
        eh, em = [int(x) for x in end_time_str.split(':')]
        start_dt = tz.localize(datetime(y, m, d, sh, sm))
        end_dt = tz.localize(datetime(y, m, d, eh, em))

        from googleapiclient.discovery import build
        service = build('calendar', 'v3', credentials=creds)
        body = {
            'summary': title,
            'description': memo,
            'location': location,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': current_user.timezone or 'Asia/Tokyo'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': current_user.timezone or 'Asia/Tokyo'},
        }
        updated = service.events().patch(calendarId='primary', eventId=event_id, body=body).execute()
        return jsonify({"success": True, "event": {
            'id': updated.get('id'),
            'title': updated.get('summary'),
            'start_time': updated.get('start', {}).get('dateTime'),
            'end_time': updated.get('end', {}).get('dateTime'),
            'location': updated.get('location')
        }})
    except Exception as e:
        return jsonify({"success": False, "error": f"更新に失敗しました: {str(e)}"}), 500

@app.route('/create_event', methods=['POST'])
@login_required
def create_event():
    creds = get_google_credentials_or_redirect()
    if isinstance(creds, dict):
        return jsonify(creds), 401
    
    user_text = request.json.get('text')
    user_memo = request.json.get('memo')
    user_location = request.json.get('location')
    if not user_text:
        return jsonify({"error": "No text provided"}), 400
    
    try:
        tz = pytz.timezone(current_user.timezone or 'Asia/Tokyo')
        current_date = datetime.now(tz).strftime("%Y年%m月%d日")
        full_text = f"現在の日付は{current_date}です。以下の予定を解析してください: {user_text}"
        
        parsed_data = parse_schedule(full_text)
        for ev in parsed_data:
            if user_memo:
                ev['description'] = user_memo
            if user_location:
                ev['location'] = user_location
            ev['source'] = 'ai_text'
        
        created_events = create_ai_calendar_event(parsed_data, creds)
        
        if created_events:
            return jsonify({"success": True, "calendar_links": created_events})
        else:
            return jsonify({"success": False, "error": "Failed to create any calendar events."}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/summary')
@login_required
def slide_summary():
    all_courses = Course.query.filter_by(university_id=current_user.university_id).all()
    unique_courses = []
    seen = set()
    for course in all_courses:
        course_key = (course.course_name, course.year)
        if course_key not in seen:
            unique_courses.append(course)
            seen.add(course_key)
    return render_template('Slide_design.html', courses=unique_courses)

@app.route('/upload_and_summarize', methods=['POST'])
@login_required
def upload_and_summarize():
    course_id = request.form.get('course_id')
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "ファイルがアップロードされていません。"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "ファイルが選択されていません。"}), 400

    allowed_extensions = {'.pdf', '.pptx', '.pptm', '.ppsx', '.ppsm', '.potx', '.potm', '.docx'}
    file_extension = os.path.splitext(file.filename)[1].lower()
    if file_extension in allowed_extensions:
        uploads_dir = "uploads"
        if not os.path.exists(uploads_dir):
            os.makedirs(uploads_dir)
        file_path = os.path.join(uploads_dir, file.filename)
        file.save(file_path)
        try:
            summary_result = summarize_file(file_path)
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
            if not summary_result or not summary_result.get('success'):
                err = summary_result.get('error', '要約に失敗しました。') if isinstance(summary_result, dict) else '要約に失敗しました。'
                return jsonify({"success": False, "error": err}), 400

            summary_text = summary_result.get('summary', '')
            try:
                course_id_int = int(course_id) if course_id else None
            except Exception:
                course_id_int = None
            new_summary_history = SummaryHistory(
                user_id=current_user.id,
                source_filename=file.filename,
                summary_text=summary_text,
                course_id=course_id_int
            )
            db.session.add(new_summary_history)
            db.session.commit()

            return jsonify({
                "success": True,
                "summary": summary_text,
                "summary_id": new_summary_history.id,
                "redirect_url": url_for('summary_result_page', summary_id=new_summary_history.id)
            })
        except Exception as e:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
            return jsonify({"success": False, "error": f"要約処理中にエラーが発生しました: {str(e)}"}), 500
    else:
        return jsonify({"success": False, "error": "PDF, PPTX, DOCXファイルのみアップロードできます。"}), 400

@app.route('/summary_result/<int:summary_id>')
@login_required
def summary_result_page(summary_id):
    s = SummaryHistory.query.get_or_404(summary_id)
    if s.user_id != current_user.id:
        return redirect(url_for('slide_summary'))
    summary_text = s.summary_text or ''
    items = [line.strip() for line in summary_text.split('\n') if line.strip()]
    course_name = s.course.course_name if s.course else None
    created = s.created_at.strftime('%Y/%m/%d') if hasattr(s, 'created_at') and s.created_at else ''
    return render_template('summary_result_page.html',
                           summary_items=items,
                           raw_summary=summary_text,
                           source_filename=s.source_filename,
                           created_at=created,
                           course_name=course_name)

@app.route('/essay')
@login_required
def essay_checker():
    all_courses = Course.query.filter_by(university_id=current_user.university_id).all()
    unique_courses = []
    seen = set()
    for course in all_courses:
        course_key = (course.course_name, course.year)
        if course_key not in seen:
            unique_courses.append(course)
            seen.add(course_key)
    return render_template('Essay_AI.html', courses=unique_courses)

@app.route('/check_essay', methods=['POST'])
@login_required
def check_essay():
    data = request.json
    topic = data.get('topic')
    text = data.get('text')
    course_id = data.get('course_id')
    if not all([topic, text, course_id]):
        return jsonify({"success": False, "error": "エッセイのテーマ、文章、関連授業をすべて入力してください。"}), 400
    
    plagiarism_check_result = check_plagiarism_with_db(text, db, Submission)
    if plagiarism_check_result["is_plagiarized"]:
        analysis_result = analyze_essay_with_gemini(topic, text)
        result = {"success": True, "analysis": analysis_result}
    else:
        result = {"success": True, "analysis": f"AI作成文との類似性は低いと判断されました。類似度スコア: {plagiarism_check_result['similarity_score']:.2f}"}
    
    new_submission = Submission(user_id=current_user.id, text=text, analysis=result['analysis'], course_id=course_id)
    db.session.add(new_submission)
    db.session.commit()
    return jsonify(result)

@app.route('/contact')
@login_required
def contact():
    csrf_token = secrets.token_hex(16)
    session['csrf_token'] = csrf_token
    return render_template('Contact.html', csrf_token=csrf_token)

@app.route('/submit_contact', methods=['POST'])
@login_required
def submit_contact():
    csrf_token = request.form.get('csrf_token')
    if csrf_token != session.get('csrf_token'):
        flash("不正なリクエストです。", 'error')
        return redirect(url_for('contact'))
    del session['csrf_token']

    name = request.form.get('name')
    email = request.form.get('email')
    message_text = request.form.get('message')

    creds = None
    if 'google_credentials' in session:
        creds_dict = session['google_credentials']
        creds = google.oauth2.credentials.Credentials.from_authorized_user_info(info=creds_dict)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            session['google_credentials'] = creds.to_json()
        else:
            flash("認証情報が無効です。再度ログインしてください。", 'error')
            return redirect(url_for('login'))
    
    try:
        service = build('gmail', 'v1', credentials=creds)

        admin_email = os.getenv('MAIL_USERNAME')
        message_to_admin = MIMEText(f"名前: {name}\nメールアドレス: {email}\n\nお問い合わせ内容:\n{message_text}", 'plain', 'utf-8')
        message_to_admin['to'] = admin_email
        message_to_admin['from'] = os.getenv('MAIL_USERNAME')  
        message_to_admin['subject'] = f"ウェブサイトからのお問い合わせ（{name}様より）"

        raw_message_to_admin = base64.urlsafe_b64encode(message_to_admin.as_bytes()).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw_message_to_admin}).execute()

        message_to_user = MIMEText(f"""
{name} 様

お問い合わせいただきありがとうございます。
確認後、改めて担当者からご連絡差し上げますので、しばらくお待ちください。

DREGININGチームより
        """, 'plain', 'utf-8')
        message_to_user['to'] = email
        message_to_user['from'] = admin_email
        message_to_user['subject'] = 'お問い合わせありがとうございます'
        
        raw_message_to_user = base64.urlsafe_b64encode(message_to_user.as_bytes()).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw_message_to_user}).execute()

        flash("お問い合わせありがとうございます。Gmailをご確認ください。", 'success_message')
        return redirect(url_for('contact'))

    except Exception as e:
        print(f"メール送信エラー: {e}")
        flash("申し訳ありません。メールの送信中にエラーが発生しました。時間をおいて再度お試しください。", 'error_message')
        return redirect(url_for('contact'))

@app.route('/share')
@login_required
def course_share():
    if not current_user.university:
        return redirect(url_for('register_profile'))
    query = request.args.get('query')
    if query:
        courses = Course.query.filter(
            Course.university_id == current_user.university_id,
            or_(
                Course.course_name.like(f'%{query}%'),
                Course.professor_name.like(f'%{query}%')
            )
        ).all()
    else:
        courses = Course.query.filter_by(university_id=current_user.university_id).all()
    grouped_courses = collections.defaultdict(list)
    for course in courses:
        key = (course.course_name, course.professor_name if course.professor_name else "不明")
        grouped_courses[key].append(course)
    return render_template('Tani.html', grouped_courses=grouped_courses)

@app.route('/add_course', methods=['POST'])
@login_required
def add_course():
    course_name = request.form.get('course_name')
    credit = request.form.get('credit')
    evaluation_method = request.form.get('evaluation_method')
    user_grade = request.form.get('user_grade')
    evaluation = request.form.get('evaluation')
    review = request.form.get('review')
    professor_name = request.form.get('professor_name')
    year = request.form.get('year')

    if not all([course_name, credit, evaluation, review, year]):
        return jsonify({"success": False, "error": "授業名、単位数、授業の雰囲気、レビュー、年度は必須項目です。"}), 400

    try:
        new_course = Course(
            course_name=course_name,
            credit=int(credit),
            evaluation=evaluation,
            review=review,
            user_id=current_user.id,
            university_id=current_user.university_id,
            professor_name=professor_name if professor_name else None,
            evaluation_method=evaluation_method if evaluation_method else None,
            user_grade=user_grade if user_grade else None,
            year=int(year)
        )
        db.session.add(new_course)
        db.session.commit()
        if user_grade and user_grade != "選択しない":
            new_grade = Grade(
                user_id=current_user.id,
                course_id=new_course.id,
                grade=user_grade
            )
            db.session.add(new_grade)
            db.session.commit()
        return jsonify({"success": True, "message": "投稿が完了しました。"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": "投稿中にエラーが発生しました。"}), 500

@app.route('/edit_course/<int:course_id>', methods=['POST'])
@login_required
def edit_course(course_id):
    course = Course.query.get_or_404(course_id)
    if course.user_id != current_user.id:
        return jsonify({"success": False, "error": "この投稿を編集する権限がありません。"}), 403
    course_name = request.form.get('course_name')
    credit = request.form.get('credit')
    evaluation_method = request.form.get('evaluation_method')
    user_grade = request.form.get('user_grade')
    evaluation = request.form.get('evaluation')
    review = request.form.get('review')
    professor_name = request.form.get('professor_name')
    year = request.form.get('year')
    if not all([course_name, credit, evaluation, review, year]):
        return jsonify({"success": False, "error": "すべての必須項目を入力してください。"}), 400
    try:
        course.course_name = course_name
        course.credit = int(credit)
        course.evaluation = evaluation
        course.review = review
        course.professor_name = professor_name if professor_name else None
        course.evaluation_method = evaluation_method if evaluation_method else None
        course.user_grade = user_grade if user_grade else None
        course.year = int(year)
        db.session.commit()
        if user_grade and user_grade != "選択しない":
            existing_grade = Grade.query.filter_by(user_id=current_user.id, course_id=course_id).first()
            if existing_grade:
                existing_grade.grade = user_grade
            else:
                new_grade = Grade(user_id=current_user.id, course_id=course_id, grade=user_grade)
                db.session.add(new_grade)
            db.session.commit()
        else:
            existing_grade = Grade.query.filter_by(user_id=current_user.id, course_id=course_id).first()
            if existing_grade:
                db.session.delete(existing_grade)
                db.session.commit()
        return jsonify({"success": True, "message": "投稿が更新されました。"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": "投稿の更新中にエラーが発生しました。"}), 500

@app.route('/delete_course/<int:course_id>', methods=['POST'])
@login_required
def delete_course(course_id):
    course = Course.query.get_or_404(course_id)
    allowed_to_delete = (course.user_id == current_user.id) or getattr(current_user, 'is_admin', False)
    if not allowed_to_delete:
        return jsonify({"success": False, "error": "この投稿を削除する権限がありません。"}), 403
    try:
        db.session.delete(course)
        db.session.commit()
        return jsonify({"success": True, "message": "投稿を削除しました。"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": "削除中にエラーが発生しました。"}), 500

@app.route('/gpa_distribution/<int:course_id>')
@login_required
def gpa_distribution(course_id):
    course = Course.query.get_or_404(course_id)
    if course.university_id != current_user.university_id:
        return jsonify({"success": False, "error": "この授業のGPA分布を閲覧する権限がありません。"}), 403
    reviews = Course.query.filter_by(course_name=course.course_name, university_id=current_user.university_id).all()
    course_ids = [r.id for r in reviews]
    grades = Grade.query.filter(Grade.course_id.in_(course_ids)).all()
    distribution = {
        'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0
    }
    for grade in grades:
        if grade.grade in distribution:
            distribution[grade.grade] += 1
    return jsonify({"success": True, "distribution": distribution}), 200

def get_course_details_data(course_id):
    course_base = Course.query.get_or_404(course_id)
    if not course_base:
        return None, "授業が見つかりません"
    reviews = Course.query.filter_by(course_name=course_base.course_name, university_id=current_user.university_id).all()
    reviews_list = []
    for r in reviews:
        reviews_list.append({
            'id': r.id,
            'user_id': r.user_id,
            'user_name': r.user.username,
            'course_name': r.course_name,
            'credit': r.credit,
            'professor_name': r.professor_name,
            'evaluation_method': r.evaluation_method,
            'user_grade': r.user_grade,
            'evaluation': r.evaluation,
            'review': r.review,
            'year': r.year
        })
    course_ids = [r.id for r in reviews]
    grades = Grade.query.filter(Grade.course_id.in_(course_ids)).all()
    distribution = {
        'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0
    }
    for grade in grades:
        if grade.grade in distribution:
            distribution[grade.grade] += 1
    submissions = Submission.query.filter(Submission.course_id.in_(course_ids)).all()
    submissions_list = [{'id': s.id, 'text': s.text} for s in submissions]
    tests = TestHistory.query.filter(TestHistory.course_id.in_(course_ids)).all()
    tests_list = [{'id': t.id, 'topic': t.topic, 'difficulty': t.difficulty} for t in tests]
    
    return {
        "reviews": reviews_list,
        "gpa_distribution": distribution,
        "ai_submissions": submissions_list,
        "ai_tests": tests_list
    }, None

@app.route('/course_details_page/<int:course_id>')
@login_required
def course_details_page(course_id):
    data, error = get_course_details_data(course_id)
    if error:
        return jsonify({"success": False, "error": error}), 404
    return render_template('course_details.html', **data)

@app.route('/course_details/<int:course_id>')
@login_required
def course_details(course_id):
    data, error = get_course_details_data(course_id)
    if error:
        return jsonify({"success": False, "error": error}), 404
    return jsonify({"success": True, **data})

@app.route('/submission/<int:submission_id>')
@login_required
def view_submission(submission_id):
    submission = Submission.query.get_or_404(submission_id)
    if submission.course.university_id != current_user.university_id:
        return redirect(url_for('dashboard'))
    return render_template('submission_detail.html', submission=submission)

@app.route('/test_history/<int:test_id>')
@login_required
def view_test_history(test_id):
    test_history = TestHistory.query.get_or_404(test_id)
    if test_history.course.university_id != current_user.university_id:
        return redirect(url_for('dashboard'))
    questions = Question.query.filter_by(test_id=test_history.id).all()
    return render_template('test_history_detail.html', test_history=test_history, questions=questions)

@app.route('/test')
@login_required
def test_maker():
    all_courses = Course.query.filter_by(university_id=current_user.university_id).all()
    unique_courses = []
    seen = set()
    for course in all_courses:
        course_key = (course.course_name, course.year)
        if course_key not in seen:
            unique_courses.append(course)
            seen.add(course_key)
    return render_template('TEST_MAKER.html', courses=unique_courses)

@app.route('/test/create', methods=['POST'])
@login_required
def create_test():
    course_id = request.form.get('course_id')
    topic = request.form.get('topic')
    difficulty = request.form.get('difficulty')
    files = request.files.getlist('file')
    question_type = request.form.get('question_type')
    
    if not all([course_id, topic]):
        return jsonify({"success": False, "error": "関連授業、テストのテーマをすべて入力してください。"}), 400
    if not files or files[0].filename == '':
        return jsonify({"success": False, "error": "教材マテリアルをアップロードしてください。"}), 400
    if len(files) > 3:
        return jsonify({"success": False, "error": "アップロードできるファイルは3個までです。"}), 400

    uploads_dir = "uploads"
    os.makedirs(uploads_dir, exist_ok=True)
    file_paths = []
    
    for file in files:
        if file and file.filename:
            filename = file.filename
            filepath = os.path.join(uploads_dir, file.filename)
            file.save(filepath)
            file_paths.append(filepath)

    try:
        test_data = create_test_from_file(file_paths, topic, difficulty, question_type)
        if isinstance(test_data, dict) and test_data.get('success') is False:
            raise Exception(test_data.get('error', 'AIからの応答で不明なエラーが発生しました。'))
        questions_to_save = test_data.get('questions', [])
        if not questions_to_save:
            raise ValueError("AIがテスト問題を生成しませんでした。")
        new_test_history = TestHistory(
            user_id=current_user.id,
            course_id=course_id,
            topic=topic,
            difficulty=difficulty,
            question_type=question_type,
            source_filename=files[0].filename
        )
        db.session.add(new_test_history)
        db.session.commit()
        for q_data in questions_to_save:
            question_text = q_data.get('question')
            answer_text = None
            q_type = q_data.get('type')
            if q_type == 'multiple_choice':
                answer_index = q_data.get('answer_index')
                options = q_data.get('options', [])
                if answer_index is not None and len(options) > answer_index:
                    answer_text = options[answer_index]
            elif q_type == 'fill_in_the_blank':
                answer_text = q_data.get('answer')
            elif q_type == 'essay':
                answer_text = q_data.get('explanation')
            if not question_text:
                raise ValueError("AIが質問文を生成できませんでした。")
            new_question = Question(
                test_id=new_test_history.id,
                question_text=question_text,
                answer_text=answer_text,
                options_json=json.dumps(q_data.get('options'))
            )
            db.session.add(new_question)
        db.session.commit()
        return jsonify({"success": True, "questions": questions_to_save})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": f"テスト生成中に予期せぬエラーが発生しました: {str(e)}"}), 500
    finally:
        for path in file_paths:
            if os.path.exists(path):
                os.remove(path)

@app.route('/generate_pdf', methods=['POST'])
@login_required
def generate_pdf():
    data = request.json
    questions = data.get('questions')
    if not questions:
        return jsonify({"error": "No questions data provided"}), 400
    rendered_html = render_template('TEST_result.html', questions=questions)
    html = weasyprint.HTML(string=rendered_html)
    pdf_file = html.write_pdf()
    return send_file(io.BytesIO(pdf_file),
                     as_attachment=True,
                     download_name='test_report.pdf',
                     mimetype='application/pdf')

@app.route('/add_announcement', methods=['GET', 'POST'])
@login_required
def add_announcement_page():
    if not getattr(current_user, 'is_admin', False):
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        token_form = request.form.get('csrf_token')
        if token_form != session.get('csrf_token'):
            return redirect(url_for('add_announcement_page'))
        message_text = request.form.get('message')
        if message_text:
            new_announcement = Announcement(message=message_text)
            db.session.add(new_announcement)
            db.session.commit()
            return redirect(url_for('add_announcement_page'))
    announcements = Announcement.query.order_by(Announcement.date_posted.desc()).all()
    return render_template('add_announcement.html', announcements=announcements)

@app.route('/delete_announcement/<int:announcement_id>', methods=['POST'])
@login_required
def delete_announcement(announcement_id):
    if not getattr(current_user, 'is_admin', False):
        return redirect(url_for('dashboard'))
    token_form = request.form.get('csrf_token')
    if token_form != session.get('csrf_token'):
        return redirect(url_for('add_announcement_page'))
    announcement = Announcement.query.get_or_404(announcement_id)
    db.session.delete(announcement)
    db.session.commit()
    return redirect(url_for('add_announcement_page'))

if __name__ == '__main__':
    with app.app_context():
        try:
            eng = db.engine
            if eng.url.drivername.startswith('sqlite'):
                with eng.connect() as con:
                    cols = [row[1] for row in con.exec_driver_sql("PRAGMA table_info(user)")]
                    if 'is_admin' not in cols:
                        con.exec_driver_sql("ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
                    if 'timezone' not in cols:
                        con.exec_driver_sql("ALTER TABLE user ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Tokyo'")
                    idxs = [row[1] for row in con.exec_driver_sql("PRAGMA index_list('course')")]
                    if 'idx_course_univ_name_prof' not in idxs:
                        con.exec_driver_sql("CREATE INDEX idx_course_univ_name_prof ON course(university_id, course_name, professor_name)")
        except Exception as _e:
            print(f"Auto-migration warning: {_e}")
        db.create_all()

        # 💡ここから新しいコードを追加💡

        # 1. デフォルトサークルの存在を確認し、なければ作成する
        from models import Channel, Circle, User
        default_circle = Circle.query.filter_by(name="デフォルトサークル").first()
        if not default_circle:
            # 必須項目を満たすために、仮のユーザーを取得（例：IDが最小のユーザー）
            # もしユーザーが存在しない場合は、サークルを作成できません
            first_user = User.query.order_by(User.id).first()
            if first_user:
                default_circle = Circle(name="デフォルトサークル", description="システム用サークル", leader_id=first_user.id)
                db.session.add(default_circle)
                db.session.commit()
                print("「デフォルトサークル」を作成しました。")
            else:
                print("サークルを作成するためのユーザーが存在しません。")
                
        # 2. デフォルトサークルが存在する場合のみ、「公開チャンネル」を作成する
        if default_circle:
            public_channel = Channel.query.filter_by(name="公開チャンネル").first()
            if not public_channel:
                new_public_channel = Channel(
                    name="公開チャンネル",
                    description="すべてのユーザーがアクセスできるデフォルトのチャンネルです。",
                    circle_id=default_circle.id
                )
                db.session.add(new_public_channel)
                db.session.commit()
                print("「公開チャンネル」を作成しました。")

        # ... (既存の大学データ初期化コード) ...
        for prefecture, uni_types in university_data.items():
            for uni_type, uni_list in uni_types.items():
                for uni_name in uni_list:
                    if not CourseUniversityMapping.query.filter_by(university_name=uni_name).first():
                        db.session.add(CourseUniversityMapping(university_name=uni_name))
        db.session.commit()
    
    
    # Flask-SocketIOでアプリケーションを実行
    socketio.run(app, debug=True)
