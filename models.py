from flask_login import UserMixin
from sqlalchemy import JSON, Table, Column, Integer, String, Date, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from extensions import db

# ==================== データベースモデルの定義 ====================

# 多対多のリレーションシップのための補助テーブル
circle_members = db.Table('circle_members',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('circle_id', db.Integer, db.ForeignKey('circle.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True, nullable=False)
    email = db.Column(db.String(120), index=True, unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    university = db.Column(db.String(256), nullable=True)
    year = db.Column(db.String(50), nullable=True)
    university_id = db.Column(db.Integer, db.ForeignKey('course_university_mapping.id'), nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    timezone = db.Column(db.String(64), default='Asia/Tokyo', nullable=False)
    following_ids = db.Column(JSON, default=list)
    follower_ids = db.Column(JSON, default=list)
    bio = db.Column(db.Text)
    profile_picture_url = db.Column(db.String(256))
    google_creds_json = db.Column(db.Text)
    reset_token = db.Column(db.String(64), unique=True)
    reset_token_expiration = db.Column(db.DateTime)
    
    # サークル機能の新しい関連
    led_circles = relationship('Circle', backref='leader_user', lazy=True, foreign_keys='Circle.leader_id')
    circles_as_member = relationship('Circle', secondary='circle_members', lazy='dynamic')
    
    # SNS機能の新しい関連
    posts = relationship('Post', back_populates='user', lazy='dynamic')
    comments = relationship('Comment', back_populates='user', lazy='dynamic')
    reactions = relationship('Reaction', back_populates='user', lazy='dynamic')
    
    # DM機能の新しい関連
    conversations1 = relationship('DirectMessageConversation', backref='user1', lazy=True, foreign_keys='DirectMessageConversation.user1_id')
    conversations2 = relationship('DirectMessageConversation', backref='user2', lazy=True, foreign_keys='DirectMessageConversation.user2_id')
    sent_messages = relationship('DirectMessage', backref='sender', lazy='dynamic', foreign_keys='DirectMessage.sender_id')
    received_messages = relationship('DirectMessage', backref='recipient', lazy='dynamic', foreign_keys='DirectMessage.recipient_id')


class CourseUniversityMapping(db.Model):
    __tablename__ = 'course_university_mapping'
    id = db.Column(db.Integer, primary_key=True)
    university_name = db.Column(db.String(256), unique=True, nullable=False)
    users = relationship('User', backref='university_mapping', lazy=True)
    courses = relationship('Course', backref='university_mapping', lazy=True)

class Submission(db.Model):
    __tablename__ = 'submission'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    analysis = db.Column(db.Text)
    date_submitted = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    is_ai_generated = db.Column(db.Boolean, default=False, nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=True)
    user = relationship('User', backref='submissions')
    course = relationship('Course', backref='submissions')

class SummaryHistory(db.Model):
    __tablename__ = 'summary_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    source_filename = db.Column(db.String(256), nullable=False)
    summary_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=True)
    user = relationship('User', backref='summary_histories')
    course = relationship('Course', backref='summary_histories')

class TestHistory(db.Model):
    __tablename__ = 'test_history'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(256), nullable=False)
    difficulty = db.Column(db.String(50), nullable=False)
    question_type = db.Column(db.String(50), nullable=False)
    source_filename = db.Column(db.String(256), nullable=False)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=True)
    user = relationship('User', backref='test_histories')
    course = relationship('Course', backref='test_histories')

class Question(db.Model):
    __tablename__ = 'question'
    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey('test_history.id'), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    answer_text = db.Column(db.Text)
    options_json = db.Column(db.Text)
    test_history = relationship('TestHistory', backref='questions')

class Course(db.Model):
    __tablename__ = 'course'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_name = db.Column(db.String(256), nullable=False)
    credit = db.Column(db.Integer)
    evaluation = db.Column(db.String(64))
    review = db.Column(db.Text)
    professor_name = db.Column(db.String(256), nullable=True)
    evaluation_method = db.Column(db.String(64), nullable=True)
    user_grade = db.Column(db.String(10), nullable=True)
    year = db.Column(db.Integer)
    university_id = db.Column(db.Integer, db.ForeignKey('course_university_mapping.id'))
    user = relationship('User', backref='courses', foreign_keys=[user_id])
    __table_args__ = (UniqueConstraint('university_id', 'course_name', 'professor_name', name='uq_course_university_course_prof'),)

class Grade(db.Model):
    __tablename__ = 'grade'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    grade = db.Column(db.String(10), nullable=False)
    user = relationship('User', backref='grades')
    course = relationship('Course', backref='grades')

class Query(db.Model):
    __tablename__ = 'query'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128))
    email = db.Column(db.String(128))
    message = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)

class Announcement(db.Model):
    __tablename__ = 'announcement'
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

class Semester(db.Model):
    __tablename__ = 'semester'
    id = db.Column(db.Integer, primary_key=True)
    university_id = db.Column(db.Integer, db.ForeignKey('course_university_mapping.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    semester_type = db.Column(db.String(10), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    university_mapping = relationship('CourseUniversityMapping', backref='semesters')

class Timetable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_name = db.Column(db.String(256), nullable=False)
    professor_name = db.Column(db.String(256), nullable=True)
    day_of_week = db.Column(db.String(20), nullable=False)
    period = db.Column(db.Integer, nullable=False)
    classroom = db.Column(db.String(100), nullable=True)
    user = relationship('User', backref='timetables')

class UniversitySettings(db.Model):
    __tablename__ = 'university_settings'
    id = db.Column(db.Integer, primary_key=True)
    university_id = db.Column(db.Integer, db.ForeignKey('course_university_mapping.id'), unique=True, nullable=False)
    spring_timetable_map = db.Column(JSON, nullable=False, default=dict)
    fall_timetable_map = db.Column(JSON, nullable=False, default=dict)
    spring_start_date = db.Column(db.Date, nullable=True)
    spring_end_date = db.Column(db.Date, nullable=True)
    fall_start_date = db.Column(db.Date, nullable=True)
    fall_end_date = db.Column(db.Date, nullable=True)

# SNS機能の新しいモデル
class Post(db.Model):
    __tablename__ = 'post'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_public = db.Column(db.Boolean, default=True)
    likes = db.Column(JSON, default=list)
    likes_count = db.Column(db.Integer, default=0)
    comments_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    media_url = db.Column(db.String(256), nullable=True)
    media_type = db.Column(db.String(50), nullable=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channel.id'), nullable=True)
    
    # サークル投稿機能の外部キー (修正/追加)
    circle_id = db.Column(db.Integer, db.ForeignKey('circle.id'), nullable=True)
    private_tl_id = db.Column(db.Integer, db.ForeignKey('private_tl.id'), nullable=True)
    
    # 新しく追加されたリンクプレビュー用のカラム
    link_url = db.Column(db.String(255))
    link_title = db.Column(db.String(255))
    link_description = db.Column(db.Text)
    link_thumbnail_url = db.Column(db.String(255))
    
    # リレーションシップの定義
    user = relationship('User', back_populates='posts')
    comments = relationship('Comment', back_populates='post', lazy='dynamic')
    reactions = relationship('Reaction', back_populates='post', lazy='dynamic')
    channel = relationship('Channel', back_populates='posts')
    circle = relationship('Circle', backref='posts')
    private_tl = relationship('PrivateTL', backref='posts')

class Comment(db.Model):
    __tablename__ = 'comment'
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    likes = db.Column(JSON, default=list)
    
    # リレーションシップの定義
    post = relationship('Post', back_populates='comments')
    user = relationship('User', back_populates='comments')

# サークル機能の新しいモデル
class Circle(db.Model):
    __tablename__ = 'circle'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)
    leader_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    is_public = db.Column(db.Boolean, default=True)
    members = relationship('User', secondary=circle_members, lazy='dynamic', backref='circles')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    executives = db.Column(JSON, default=list) # 幹部のIDを格納するカラム
    
    # ****************** 修正/追加された部分 ******************
    background_image_url = db.Column(db.String(256), nullable=True) # 背景画像URL
    executives_titles = db.Column(JSON, default=dict) # 幹部IDと役職名 (例: {"1": "広報部長"})
    # *********************************************************
    
    # リレーションシップの定義
    channels = relationship('Channel', back_populates='circle', lazy='dynamic')

# --- 新規追加: プライベートTLモデル ---
class PrivateTL(db.Model):
    __tablename__ = 'private_tl'
    id = db.Column(db.Integer, primary_key=True)
    circle_id = db.Column(db.Integer, db.ForeignKey('circle.id'), nullable=False)
    name = db.Column(db.String(256), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    member_ids = db.Column(JSON, default=list) # TLメンバーのIDリスト
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # リレーションシップ
    circle = relationship('Circle', backref='private_tls')
    creator = relationship('User', backref='created_private_tls')
# --- 新規追加 終了 ---


# チャンネル機能の新しいモデル
class Channel(db.Model):
    __tablename__ = 'channel'
    id = db.Column(db.Integer, primary_key=True)
    circle_id = db.Column(db.Integer, db.ForeignKey('circle.id'), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(255))
    type = db.Column(db.String(50), default='text')
    
    # リレーションシップの定義
    circle = relationship('Circle', back_populates='channels')
    posts = relationship('Post', back_populates='channel', lazy='dynamic')

# リアクション機能の新しいモデル
class Reaction(db.Model):
    __tablename__ = 'reaction'
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    emoji = db.Column(db.String(50), nullable=False)
    
    # リレーションシップの定義
    user = relationship('User', back_populates='reactions')
    post = relationship('Post', back_populates='reactions')
    comment = relationship('Comment', backref='reactions')
    
    __table_args__ = (UniqueConstraint('post_id', 'user_id', 'emoji', name='_post_user_emoji_uc'),
                      UniqueConstraint('comment_id', 'user_id', 'emoji', name='_comment_user_emoji_uc'))

# DM機能の新しいモデル
class DirectMessageConversation(db.Model):
    __tablename__ = 'direct_message_conversation'
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    messages = relationship('DirectMessage', backref='conversation', lazy='dynamic')

    __table_args__ = (
        UniqueConstraint('user1_id', 'user2_id', name='_user_pair_uc'),
    )

class DirectMessage(db.Model):
    __tablename__ = 'direct_message'
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('direct_message_conversation.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Follow(db.Model):
    __tablename__ = 'follow'
    follower_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    followed_id = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    follower = relationship('User', foreign_keys=[follower_id], backref=db.backref('following', lazy='dynamic'))
    followed = relationship('User', foreign_keys=[followed_id], backref=db.backref('followers', lazy='dynamic'))

    def __repr__(self):
        return f'<Follow {self.follower_id} -> {self.followed_id}>'

class Event(db.Model):
    __tablename__ = 'event'
    id = db.Column(db.Integer, primary_key=True)
    circle_id = db.Column(db.Integer, db.ForeignKey('circle.id'), nullable=False)
    organizer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(256))
    attendees = db.Column(JSON, default=list)

    circle = relationship('Circle', backref='events')
    organizer = relationship('User', backref='organized_events')
