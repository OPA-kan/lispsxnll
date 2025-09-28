# community.py
from flask import Blueprint, jsonify, request, redirect, url_for, render_template, current_app
from flask_login import current_user
from models import Post, Comment, User, Circle, Channel, Reaction, PrivateTL, Course
from flask_login import login_required, AnonymousUserMixin
from sqlalchemy import or_
import re
import io
from werkzeug.utils import secure_filename
import os
from flask_socketio import emit, join_room, leave_room
from datetime import datetime
import requests
import json
from bs4 import BeautifulSoup

# Blueprintの定義
community_bp = Blueprint('community', __name__, url_prefix='/community')

# SocketIOインスタンスをグローバル変数として保持
socketio = None

def init_socketio(sio_instance):
    """
    SocketIOインスタンスを初期化し、イベントハンドラを登録する
    """
    global socketio
    socketio = sio_instance

    @socketio.on('connect', namespace='/')
    def handle_connect():
        if not current_user.is_authenticated:
            print('Anonymous client attempted to connect.')
            return False
            
        print(f'Client {current_user.id} connected')
        join_room(f'user_{current_user.id}')
        
    @socketio.on('join_channel', namespace='/')
    def handle_join_channel(data):
        if not current_user.is_authenticated:
            print('Anonymous client attempted to join channel.')
            return
            
        channel_id = data.get('channel_id')
        circle_id = data.get('circle_id')
        tl_id = data.get('tl_id')

        if channel_id:
            room_name = f'channel_{channel_id}'
            join_room(room_name)
            print(f'User {current_user.id} joined room: {room_name}')

        if circle_id and tl_id is not None and tl_id != '0':
             room_name = f'circle_{circle_id}_tl_{tl_id}'
             join_room(room_name)
             print(f'User {current_user.id} joined private TL room: {room_name}')
    
    @socketio.on('join_tl_room', namespace='/')
    def handle_join_tl_room(data):
        """サイドバーからTLルームに参加するためのカスタムイベント"""
        if not current_user.is_authenticated: return
        room_name = data.get('room_name')
        if room_name:
            join_room(room_name)
            print(f'User {current_user.id} joined TL room via sidebar: {room_name}')
    
    # 投稿作成時のハンドラ
    @socketio.on('create_post', namespace='/')
    def handle_create_post(data):
        if not current_user.is_authenticated: return
        emit('new_post', data, room=f'channel_{data["channel_id"]}')
    
    # コメント作成時のハンドラ
    @socketio.on('new_comment', namespace='/')
    def handle_new_comment(data):
        if not current_user.is_authenticated: return
        emit('new_comment', data, room=f'channel_{data["channel_id"]}')

def _serialize_post(post):
    is_liked = current_user.is_authenticated and current_user.id in (post.likes or [])
    reaction_counts = {}
    reactions = post.reactions.all()
    for reaction in reactions:
        reaction_counts[reaction.emoji] = reaction_counts.get(reaction.emoji, 0) + 1
    
    comments_list = []
    for comment in post.comments.order_by(Comment.created_at.asc()).all():
        is_comment_liked = current_user.is_authenticated and current_user.id in (comment.likes or [])
        comments_list.append({
            "id": comment.id,
            "username": comment.user.username,
            "user_id": comment.user.id,
            "content": comment.content,
            "created_at": comment.created_at.strftime('%Y/%m/%d %H:%M'),
            "user_profile_picture_url": comment.user.profile_picture_url,
            "is_liked": is_comment_liked,
            "likes_count": len(comment.likes or [])
        })
        
    circle_name = None
    tl_name = None
    if post.circle:
        circle_name = post.circle.name
        if post.private_tl:
            tl_name = post.private_tl.name
        else:
            tl_name = 'Default'
    course_info = None
    if post.course:
        course_info = {
            "id": post.course.id,
            "course_name": post.course.course_name,
            "professor_name": post.course.professor_name,
        }

    return {
        "id": post.id,
        "content": post.content,
        "user_id": post.user.id,
        "username": post.user.username,
        "user_profile_picture": post.user.profile_picture_url,
        "likes_count": post.likes_count,
        "comments_count": post.comments.count(),
        "is_liked": is_liked,
        "created_at": post.created_at.strftime('%Y年%m月%d日 %H:%M'),
        "media_url": post.media_url,
        "media_type": post.media_type,
        "reaction_counts": reaction_counts,
        "channel_id": post.channel_id,
        "link_url": post.link_url,
        "link_title": post.link_title,
        "link_description": post.link_description,
        "link_thumbnail_url": post.link_thumbnail_url,
        "comments": comments_list,
        "circle_name": circle_name,
        "tl_name": tl_name,
        "course_info": course_info
    }

# -------------------- ここからサークル機能 --------------------

circle_management_bp = Blueprint('circle_management_bp', __name__, url_prefix='/circles')

def save_image_file(file, folder_name):
    if not file:
        return None
    
    filename = secure_filename(file.filename)
    upload_folder = os.path.join(current_app.root_path, 'static', 'uploads', folder_name)
    os.makedirs(upload_folder, exist_ok=True)
    filepath = os.path.join(upload_folder, filename)
    file.save(filepath)
    
    return url_for('static', filename=f'uploads/{folder_name}/{filename}')

@circle_management_bp.route('/')
@login_required
def circle_list():
    my_circles = current_user.circles_as_member.all()
    
    for circle in my_circles:
        circle.is_executive = current_user.id in (circle.executives or [])
        circle.is_leader = current_user.id == circle.leader_id
        circle.leader_username = circle.leader_user.username if circle.leader_user else '不明'
        circle.member_count = circle.members.count()
    
    public_circles = Circle.query.filter_by(is_public=True).all()
    my_circle_ids = [c.id for c in my_circles]
    for circle in public_circles:
        circle.is_member = circle.id in my_circle_ids
    
    return render_template('circle_list.html', my_circles=my_circles, public_circles=public_circles)

@circle_management_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_circle():
    circle = None
    if 'circle_id' in request.args:
        circle = Circle.query.get(request.args['circle_id'])
    
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        is_public = 'is_public' in request.form
        image_file = request.files.get('background_image')
        
        if not name:
            return render_template('create_circle.html', error="サークル名を入力してください。", circle=circle)
        
        image_url = save_image_file(image_file, 'circles')
        
        if circle:
            if current_user.id not in (circle.executives or []):
                return redirect(url_for('circle_management_bp.circle_list'))
                
            circle.name = name
            circle.description = description
            circle.is_public = is_public
            if image_url:
                circle.background_image_url = image_url
            db.session.commit()
            return redirect(url_for('circle_management_bp.circle_list'))
        else:
            new_circle = Circle(
                name=name,
                description=description,
                leader_id=current_user.id,
                is_public=is_public,
                executives=[current_user.id],
                executives_titles={str(current_user.id): 'リーダー'},
                background_image_url=image_url
            )
            
            db.session.add(new_circle)
            db.session.commit()
            
            new_circle.members.append(current_user)
            db.session.commit()
            
            return redirect(url_for('circle_management_bp.circle_list'))
        
    return render_template('create_circle.html', circle=circle)

@circle_management_bp.route('/<int:circle_id>/join', methods=['POST'])
@login_required
def join_circle(circle_id):
    circle = Circle.query.get_or_404(circle_id)
    
    if current_user in circle.members:
        return jsonify({"message": "すでに参加しています", "status": "already_member"}), 400
    
    circle.members.append(current_user)
    db.session.commit()
    
    return jsonify({"message": "サークルに参加しました", "status": "joined"}), 200

@circle_management_bp.route('/<int:circle_id>/leave', methods=['POST'])
@login_required
def leave_circle(circle_id):
    circle = Circle.query.get_or_404(circle_id)
    
    if current_user not in circle.members:
        return jsonify({"message": "サークルに所属していません", "status": "not_member"}), 400
    
    confirm_deletion = request.args.get('confirm', type=int)
    
    if circle.members.count() == 1 and not confirm_deletion:
        return render_template('circle_leave_warning.html', circle_id=circle_id), 200

    circle.members.remove(current_user)
    
    if current_user.id in (circle.executives or []):
        circle.executives.remove(current_user.id)
    if str(current_user.id) in (circle.executives_titles or {}):
        del circle.executives_titles[str(current_user.id)]
    
    db.session.commit()
    
    if circle.members.count() == 0:
        try:
            for channel in circle.channels.all():
                for post in channel.posts.all():
                    db.session.query(Comment).filter_by(post_id=post.id).delete(synchronize_session=False)
                    db.session.query(Reaction).filter_by(post_id=post.id).delete(synchronize_session=False)
                    db.session.delete(post)
                db.session.delete(channel)
            
            db.session.delete(circle)
            db.session.commit()
            
            return jsonify({"message": "サークルを脱退し、サークルと関連データが完全に削除されました", "status": "deleted"}), 200
        
        except Exception as e:
            db.session.rollback()
            print(f"FATAL ERROR: Circle deletion failed for Circle ID {circle_id}. Error: {e}")
            return jsonify({"message": "脱退と削除の処理中に致命的なエラーが発生しました。", "status": "deletion_failed"}), 500

    return jsonify({"message": "サークルを脱退しました", "status": "left"}), 200

@circle_management_bp.route('/<int:circle_id>/private_tls', methods=['POST'])
@login_required
def create_private_tl(circle_id):
    circle = Circle.query.get_or_404(circle_id)

    if current_user.id != circle.leader_id and current_user.id not in (circle.executives or []):
        return jsonify({"error": "リーダーまたは幹部のみがTLを作成できます"}), 403

    data = request.json
    name = data.get('name', '').strip()
    member_ids = data.get('member_ids', [])

    if not name or not member_ids:
        return jsonify({"error": "TL名とメンバーリストが必要です"}), 400
    
    valid_member_ids = []
    circle_member_ids = [member.id for member in circle.members]
    
    for member_id in member_ids:
        try:
            member_id_int = int(member_id)
            if member_id_int in circle_member_ids:
                valid_member_ids.append(member_id_int)
        except ValueError:
            continue

    if not valid_member_ids:
        return jsonify({"error": "選択されたメンバーがサークルに所属していないか、無効です"}), 400
    
    new_tl = PrivateTL(
        circle_id=circle_id,
        name=name,
        creator_id=current_user.id,
        member_ids=valid_member_ids
    )

    db.session.add(new_tl)
    db.session.commit()

    return jsonify({"message": f"プライベートTL '{name}' が作成されました", "id": new_tl.id}), 201

@circle_management_bp.route('/<int:circle_id>/private_tls/<int:tl_id>', methods=['DELETE'])
@login_required
def delete_private_tl(circle_id, tl_id):
    circle = Circle.query.get_or_404(circle_id)
    tl = PrivateTL.query.filter_by(id=tl_id, circle_id=circle_id).first_or_404()

    if current_user.id != circle.leader_id and current_user.id != tl.creator_id:
        return jsonify({"error": "リーダーまたはTL作成者のみが削除できます"}), 403

    try:
        Post.query.filter_by(private_tl_id=tl_id).delete(synchronize_session=False)
        db.session.delete(tl)
        db.session.commit()
        return jsonify({"message": f"プライベートTL '{tl.name}' と関連投稿を削除しました"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting PrivateTL {tl_id}: {e}")
        return jsonify({"error": "TL削除中にエラーが発生しました"}), 500

@circle_management_bp.route('/<int:circle_id>/announcements', methods=['POST'])
@login_required
def create_announcement(circle_id):
    circle = Circle.query.get_or_404(circle_id)
    
    if current_user.id != circle.leader_id and current_user.id not in (circle.executives or []):
        return jsonify({"error": "リーダーまたは幹部のみが告知を投稿できます"}), 403

    data = request.json
    content = data.get('content')

    if not content:
        return jsonify({"error": "告知内容を入力してください"}), 400
    
    return jsonify({"message": "告知投稿のロジックはバックエンドに追加されました (DB保存処理は未実装)", "status": "success"}), 201


@circle_management_bp.route('/<int:circle_id>/announcements/<int:announcement_id>', methods=['DELETE'])
@login_required
def delete_announcement(circle_id, announcement_id):
    circle = Circle.query.get_or_404(circle_id)

    if current_user.id != circle.leader_id and current_user.id not in (circle.executives or []):
        return jsonify({"error": "リーダーまたは幹部のみが告知を削除できます"}), 403
    
    return jsonify({"message": f"告知 ID:{announcement_id} の削除ロジックはバックエンドに追加されました (DB削除処理は未実装)", "status": "success"}), 200

@circle_management_bp.route('/<int:circle_id>/executive_title', methods=['POST'])
@login_required
def set_executive_title(circle_id):
    circle = Circle.query.get_or_404(circle_id)
    if current_user.id not in (circle.executives or []):
        return jsonify({"error": "幹部のみが役職名を設定できます"}), 403
        
    data = request.json
    title = data.get('title', '').strip()
    
    if not title:
        return jsonify({"error": "役職名を入力してください"}), 400
        
    titles = circle.executives_titles.copy()
    titles[str(current_user.id)] = title
    circle.executives_titles = titles
    
    db.session.commit()
    return jsonify({"message": "役職名が更新されました", "title": title}), 200


@circle_management_bp.route('/<int:circle_id>/invite', methods=['POST'])
@login_required
def invite_user_to_circle(circle_id):
    circle = Circle.query.get_or_404(circle_id)
    
    if current_user.id not in (circle.executives or []) and current_user.id != circle.leader_id:
        return jsonify({"error": "幹部またはリーダーのみが招待できます"}), 403

    data = request.json
    user_id_to_invite = data.get('user_id')
    
    if not user_id_to_invite:
        return jsonify({"error": "招待するユーザーIDを指定してください"}), 400
        
    try:
        user_id_to_invite = int(user_id_to_invite)
    except ValueError:
        return jsonify({"error": "無効なユーザーIDです"}), 400
        
    user_to_invite = User.query.get(user_id_to_invite)
    
    if not user_to_invite:
        return jsonify({"error": "ユーザーが見つかりません"}), 404
    
    if user_to_invite in circle.members:
        return jsonify({"message": "ユーザーはすでにサークルに所属しています", "status": "already_member"}), 400
        
    circle.members.append(user_to_invite)
    db.session.commit()

    return jsonify({"message": f"{user_to_invite.username}をサークルに招待（参加）させました", "status": "invited"}), 200

@circle_management_bp.route('/<int:circle_id>/members')
@login_required
def manage_circle_members(circle_id):
    circle = Circle.query.get_or_404(circle_id)
    
    if current_user.id not in (circle.executives or []) and current_user.id != circle.leader_id:
        return redirect(url_for('circle_management_bp.circle_list'))
        
    members = []
    for member in circle.members.all():
        members.append({
            'id': member.id,
            'username': member.username,
            'profile_picture_url': member.profile_picture_url,
            'is_executive': member.id in (circle.executives or []),
            'is_leader': member.id == circle.leader_id,
            'title': circle.executives_titles.get(str(member.id), '幹部') if member.id in (circle.executives or []) else None
        })
        
    search_api_url = url_for('community.search_users')
        
    private_tls = PrivateTL.query.filter_by(circle_id=circle_id).all()
    for tl in private_tls:
        tl.creator_name = User.query.get(tl.creator_id).username if User.query.get(tl.creator_id) else '不明'
        tl.member_count = len(tl.member_ids)
        
    return render_template('manage_circle_members.html', 
                           circle=circle, 
                           members=members, 
                           search_api_url=search_api_url,
                           private_tls=private_tls
                           )


@circle_management_bp.route('/<int:circle_id>/members/<int:member_id>/executive', methods=['POST'])
@login_required
def toggle_executive_status(circle_id, member_id):
    circle = Circle.query.get_or_404(circle_id)
    if current_user.id != circle.leader_id:
        return jsonify({"error": "リーダーのみがこの操作を行えます"}), 403
        
    member = User.query.get_or_404(member_id)
    if member not in circle.members:
        return jsonify({"error": "メンバーが見つかりません"}), 404
        
    data = request.json
    action = data.get('action')
    
    if action == 'promote':
        if member.id not in (circle.executives or []):
            circle.executives.append(member.id)
            titles = circle.executives_titles.copy()
            titles[str(member.id)] = '幹部' 
            circle.executives_titles = titles
            db.session.commit()
            return jsonify({"message": "メンバーを幹部に設定しました", "status": "promoted"}), 200
        return jsonify({"error": "すでに幹部です"}), 400
    elif action == 'demote':
        if member.id in (circle.executives or []):
            if member.id == circle.leader_id:
                return jsonify({"error": "リーダーの幹部ステータスは解除できません"}), 400
                
            circle.executives.remove(member.id)
            if str(member.id) in (circle.executives_titles or {}):
                titles = circle.executives_titles.copy()
                del titles[str(member.id)]
                circle.executives_titles = titles
                
            db.session.commit()
            return jsonify({"message": "メンバーの幹部ステータスを解除しました", "status": "demoted"}), 200
        return jsonify({"error": "幹部ではありません"}), 400

    return jsonify({"error": "Invalid action"}), 400

# -------------------- ここまでサークル機能 --------------------
# --- 新規追加: サイドバー用TL API ---
@community_bp.route('/api/user_tls', methods=['GET'])
@login_required
def get_user_tls():
    """
    ユーザーが参加している全てのサークルの、全てのTLリストを返す (サイドバー用)
    """
    my_circles = current_user.circles_as_member.all()
    all_tls = []

    for circle in my_circles:
        # 1. デフォルトTL (全員) を追加
        all_tls.append({
            'circle_id': circle.id,
            'circle_name': circle.name,
            'circle_image_url': circle.background_image_url,
            'tl_id': 0,
            'tl_name': 'Default TL (全員)',
            'member_count': circle.members.count()
        })

        # 2. 自分がメンバーになっているプライベートTLを追加
        private_tls = PrivateTL.query.filter_by(circle_id=circle.id).all()
        for tl in private_tls:
            if current_user.id in tl.member_ids:
                all_tls.append({
                    'circle_id': circle.id,
                    'circle_name': circle.name,
                    'circle_image_url': circle.background_image_url,
                    'tl_id': tl.id,
                    'tl_name': tl.name,
                    'member_count': len(tl.member_ids)
                })

    return jsonify(tls=all_tls)

@community_bp.route('/api/circles/<int:circle_id>/tls/<int:tl_id>/posts', methods=['GET'])
@login_required
def get_tl_posts(circle_id, tl_id):
    """
    特定のサークル/TLの投稿を取得するAPI (サイドバー用)
    """
    circle = Circle.query.get_or_404(circle_id)
    
    if current_user not in circle.members:
        return jsonify({"error": "アクセス権限がありません"}), 403

    if tl_id == 0:
        posts = Post.query.filter(
            Post.circle_id == circle_id, 
            Post.private_tl_id == None
        ).order_by(Post.created_at.desc()).limit(50).all()
    else:
        tl = PrivateTL.query.get_or_404(tl_id)
        if current_user.id not in tl.member_ids:
             return jsonify({"error": "このTLへのアクセス権限がありません"}), 403
             
        posts = Post.query.filter(
            Post.circle_id == circle_id,
            Post.private_tl_id == tl_id
        ).order_by(Post.created_at.desc()).limit(50).all()

    posts_list = [_serialize_post(post) for post in posts]
    
    return jsonify(posts=posts_list)

@community_bp.route('/')
@community_bp.route('/<string:feed_type>')
@login_required
def community_feed(feed_type='recommended'):
    posts = []
    channel_id = None
    circle_id = request.args.get('circle_id', type=int)
    current_tl_id = request.args.get('tl_id', type=int) or 0
    circle_info = None
    announcements = []
    
    try:
        if feed_type == 'circle' and circle_id:
            circle = Circle.query.get(circle_id)
            if not circle or current_user not in circle.members:
                return redirect(url_for('circle_management_bp.circle_list'))

            if current_tl_id != 0:
                tl = PrivateTL.query.get(current_tl_id)
                if tl and current_user.id not in tl.member_ids:
                    return redirect(url_for('community.community_feed', feed_type='circle', circle_id=circle_id, tl_id=0))

            circle_info = {
                'id': circle.id,
                'name': circle.name,
                'description': circle.description,
                'member_count': circle.members.count(),
                'is_executive': current_user.id == circle.leader_id or current_user.id in (circle.executives or []),
                'private_tls': [
                    {'id': tl.id, 'name': tl.name, 'member_count': len(tl.member_ids)}
                    for tl in PrivateTL.query.filter_by(circle_id=circle_id).all()
                    if current_user.id in tl.member_ids
                ]
            }

            if current_tl_id == 0:
                announcements = [
                    {'id': 1, 'content': 'サークルの活動時間が来週から変更になります。詳細は別途連絡します。', 'author_name': 'リーダー', 'created_at': '2025/09/25 10:00'},
                    {'id': 2, 'content': '新しいプライベートTL「企画チーム」が作成されました！', 'author_name': '幹部A', 'created_at': '2025/09/24 18:30'}
                ]

            if current_tl_id == 0:
                posts = Post.query.filter(
                    Post.circle_id == circle_id, 
                    Post.private_tl_id == None
                ).order_by(Post.created_at.desc()).all()
            else:
                posts = Post.query.filter(
                    Post.circle_id == circle_id,
                    Post.private_tl_id == current_tl_id
                ).order_by(Post.created_at.desc()).all()
            
            channel_id = circle_id 

        elif feed_type == 'recommended':
            posts = Post.query.filter_by(is_public=True).order_by(Post.created_at.desc()).all()
            channel = Channel.query.filter_by(name="公開チャンネル").first()
            if channel:
                channel_id = channel.id
        elif feed_type == 'following':
            following_ids = current_user.following_ids or []
            following_ids.append(current_user.id)
            
            posts = Post.query.filter(
                Post.user_id.in_(following_ids)
            ).order_by(Post.created_at.desc()).all()
            
            channel = Channel.query.filter_by(name="フォローチャンネル").first()
            if channel:
                channel_id = channel.id
        
        posts_list = [_serialize_post(post) for post in posts]
            
        return render_template('community_feed.html', 
                               posts=posts_list, 
                               feed_type=feed_type, 
                               channel_id=channel_id,
                               circle_id=circle_id,
                               current_tl_id=current_tl_id,
                               circle_info=circle_info,
                               announcements=announcements
                               )
        
    except Exception as e:
        print(f"Error in community_feed: {e}")
        return render_template('community_feed.html', posts=[], feed_type=request.args.get('type', 'recommended'), channel_id=None, circle_id=None, current_tl_id=0, circle_info=None, announcements=[])


@community_bp.route('/user/<int:user_id>')
@login_required
def user_profile(user_id):
    user = User.query.get_or_404(user_id)
    is_following = user.id in (current_user.following_ids or [])
    posts = Post.query.filter_by(user_id=user.id).order_by(Post.created_at.desc()).all()
    
    posts_list = [_serialize_post(post) for post in posts]
    
    return render_template('user_profile.html', user=user, posts=posts_list, is_following=is_following)


@community_bp.route('/posts', methods=['POST'])
@login_required
def create_post():
    content = request.form.get('content')
    channel_id_str = request.form.get('channel_id')
    attachment = request.files.get('attachment')
    course_id = request.form.get('course_id')

    if not content and not attachment:
        return jsonify({"error": "投稿内容を入力するか、ファイルを添付してください。"}), 400
    
    url_pattern = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')
    match = url_pattern.search(content)
    
    link_url = None
    link_title = None
    link_description = None
    link_thumbnail_url = None

    if match:
        link_url = match.group(0)
        try:
            youtube_url_pattern = r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/(watch\?v=|embed/|v/|shorts/)?([a-zA-Z0-9_-]{11})"
            if re.match(youtube_url_pattern, link_url):
                oembed_url = f'https://www.youtube.com/oembed?url={link_url}&format=json'
                response = requests.get(oembed_url, timeout=5)
                response.raise_for_status()
                oembed_data = response.json()
                
                link_title = oembed_data.get('title')
                link_description = oembed_data.get('author_name', 'YouTube Video')
                link_thumbnail_url = oembed_data.get('thumbnail_url')
            else:
                response = requests.get(link_url, timeout=5)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                
                link_title = soup.find('meta', property='og:title')
                if link_title: link_title = link_title.get('content')
                else: link_title = soup.title.string if soup.title else None
                
                link_description = soup.find('meta', property='og:description')
                if link_description: link_description = link_description.get('content')
                
                link_thumbnail_url = soup.find('meta', property='og:image')
                if link_thumbnail_url: link_thumbnail_url = link_thumbnail_url.get('content')

        except requests.exceptions.RequestException as e:
            print(f"Error fetching link metadata: {e}")
            link_url = None
        except Exception as e:
            print(f"Error processing link metadata: {e}")
            link_url = None
        
        if link_url:
            pass

    channel_id = None
    if channel_id_str:
        try:
            channel_id = int(channel_id_str)
        except (ValueError, TypeError):
            pass
    
    if not channel_id:
        public_channel = Channel.query.filter_by(name="公開チャンネル").first()
        if public_channel:
            channel_id = public_channel.id
        else:
            return jsonify({"error": "公開チャンネルが見つかりません。データベースを確認してください。"}), 500

    media_url = None
    media_type = None
    if attachment:
        filename = secure_filename(attachment.filename)
        upload_folder = os.path.join(current_app.root_path, 'static', 'uploads', 'posts')
        os.makedirs(upload_folder, exist_ok=True)
        filepath = os.path.join(upload_folder, filename)
        attachment.save(filepath)
        media_url = url_for('static', filename=f'uploads/posts/{filename}')
        
        mimetype = attachment.mimetype
        if mimetype and mimetype.startswith('image/'):
            media_type = 'image'
        elif mimetype and mimetype.startswith('video/'):
            media_type = 'video'
        else:
            ext = filename.rsplit('.', 1)[-1].lower()
            if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
                media_type = 'image'
            elif ext in ['mp4', 'mov', 'avi', 'mkv', 'webm']:
                media_type = 'video'
            else:
                media_type = 'other'
            
    new_post = Post(
        content=content,
        user_id=current_user.id,
        channel_id=channel_id,
        media_url=media_url,
        media_type=media_type,
        likes_count=0,
        link_url=link_url,
        link_title=link_title,
        link_description=link_description,
        link_thumbnail_url=link_thumbnail_url,
        circle_id=None,
        private_tl_id=None,
        course_id=int(course_id) if course_id else None
    )
    db.session.add(new_post)
    db.session.commit()

    post_data = {
        'id': new_post.id,
        'user_id': new_post.user.id,
        'username': new_post.user.username,
        'user_profile_picture': new_post.user.profile_picture_url,
        'content': new_post.content,
        'created_at': new_post.created_at.strftime('%Y年%m月%d日 %H:%M'),
        'likes_count': new_post.likes_count,
        'comments_count': new_post.comments.count(),
        'is_liked': current_user.id in (new_post.likes or []),
        'media_url': new_post.media_url,
        'media_type': new_post.media_type,
        'channel_id': new_post.channel_id,
        'link_url': new_post.link_url,
        'link_title': new_post.link_title,
        'link_description': new_post.link_description,
        'link_thumbnail_url': new_post.link_thumbnail_url
    }
    
    print(f"DEBUG: Emitting 'new_post' to room channel_{channel_id} with data: {post_data}")
    socketio.emit('new_post', post_data, room=f'channel_{channel_id}', namespace='/')

    return jsonify({"message": "投稿が成功しました"}), 201

@community_bp.route('/circles/<int:circle_id>/posts', methods=['POST'])
@login_required
def create_circle_post(circle_id):
    circle = Circle.query.get_or_404(circle_id)
    
    if current_user not in circle.members:
        return jsonify({"error": "このサークルのメンバーではありません"}), 403

    content = request.form.get('content')
    tl_id = request.form.get('tl_id', type=int)
    attachment = request.files.get('attachment')
    course_id = request.form.get('course_id')

    if not content and not attachment:
        return jsonify({"error": "投稿内容を入力するか、ファイルを添付してください。"}), 400

    private_tl = None
    if tl_id and tl_id != 0:
        private_tl = PrivateTL.query.get(tl_id)
        if not private_tl or current_user.id not in private_tl.member_ids:
            return jsonify({"error": "このTLに投稿する権限がありません"}), 403

    url_pattern = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')
    match = url_pattern.search(content)
    
    link_url = None
    link_title = None
    link_description = None
    link_thumbnail_url = None

    if match:
        link_url = match.group(0)
        try:
            youtube_url_pattern = r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/(watch\?v=|embed/|v/|shorts/)?([a-zA-Z0-9_-]{11})"
            if re.match(youtube_url_pattern, link_url):
                oembed_url = f'https://www.youtube.com/oembed?url={link_url}&format=json'
                response = requests.get(oembed_url, timeout=5)
                response.raise_for_status()
                oembed_data = response.json()
                
                link_title = oembed_data.get('title')
                link_description = oembed_data.get('author_name', 'YouTube Video')
                link_thumbnail_url = oembed_data.get('thumbnail_url')
            else:
                response = requests.get(link_url, timeout=5)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                
                link_title = soup.find('meta', property='og:title')
                if link_title: link_title = link_title.get('content')
                else: link_title = soup.title.string if soup.title else None
                
                link_description = soup.find('meta', property='og:description')
                if link_description: link_description = link_description.get('content')
                
                link_thumbnail_url = soup.find('meta', property='og:image')
                if link_thumbnail_url: link_thumbnail_url = link_thumbnail_url.get('content')

        except requests.exceptions.RequestException as e:
            print(f"Error fetching link metadata: {e}")
            link_url = None
        except Exception as e:
            print(f"Error processing link metadata: {e}")
            link_url = None
        
        if link_url:
            pass

    media_url = None
    media_type = None
    if attachment:
        filename = secure_filename(attachment.filename)
        upload_folder = os.path.join(current_app.root_path, 'static', 'uploads', 'posts')
        os.makedirs(upload_folder, exist_ok=True)
        filepath = os.path.join(upload_folder, filename)
        attachment.save(filepath)
        media_url = url_for('static', filename=f'uploads/posts/{filename}')
        
        mimetype = attachment.mimetype
        if mimetype and mimetype.startswith('image/'):
            media_type = 'image'
        elif mimetype and mimetype.startswith('video/'):
            media_type = 'video'
        else:
            ext = filename.rsplit('.', 1)[-1].lower()
            if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
                media_type = 'image'
            elif ext in ['mp4', 'mov', 'avi', 'mkv', 'webm']:
                media_type = 'video'
            else:
                media_type = 'other'

    new_post = Post(
        content=content,
        user_id=current_user.id,
        circle_id=circle_id,
        private_tl_id=tl_id if tl_id != 0 else None,
        channel_id=circle_id,
        media_url=media_url,
        media_type=media_type,
        likes_count=0,
        link_url=link_url,
        link_title=link_title,
        link_description=link_description,
        link_thumbnail_url=link_thumbnail_url,
        is_public=False,
        course_id=int(course_id) if course_id else None
    )
    db.session.add(new_post)
    db.session.commit()
    
    post_data = _serialize_post(new_post)
    
    room_name = f'circle_{circle_id}_tl_{tl_id}' if tl_id != 0 else f'channel_{circle_id}'
    
    print(f"DEBUG: Emitting 'new_post' to room {room_name} with data: {post_data}")
    socketio.emit('new_post', post_data, room=room_name, namespace='/')

    return jsonify({"message": "サークルに投稿が成功しました", "post": post_data}), 201

@community_bp.route('/posts/<int:post_id>', methods=['DELETE'])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.user_id != current_user.id:
        return jsonify({"error": "Unauthorized"}), 403

    db.session.delete(post)
    db.session.commit()

    if post.circle_id:
        tl_id = post.private_tl_id or 0
        room_name = f'circle_{post.circle_id}_tl_{tl_id}' if tl_id != 0 else f'channel_{post.circle_id}'
        socketio.emit('post_deleted', {'post_id': post_id}, room=room_name, namespace='/')
    elif post.channel_id:
        socketio.emit('post_deleted', {'post_id': post_id}, room=f'channel_{post.channel_id}', namespace='/')
    
    return jsonify({"message": "Post deleted successfully"})

@community_bp.route('/posts/<int:post_id>/like', methods=['POST'])
@login_required
def toggle_like(post_id):
    post = Post.query.get_or_404(post_id)
    
    likes_list = post.likes or []
    
    if current_user.id in likes_list:
        likes_list.remove(current_user.id)
        is_liked = False
    else:
        likes_list.append(current_user.id)
        is_liked = True
    
    post.likes = likes_list
    post.likes_count = len(likes_list)
    
    db.session.add(post)
    db.session.commit()

    data_to_emit = {
        'post_id': post.id,
        'likes_count': post.likes_count,
        'is_liked': is_liked
    }
    
    if post.circle_id:
        tl_id = post.private_tl_id or 0
        room_name = f'circle_{post.circle_id}_tl_{tl_id}' if tl_id != 0 else f'channel_{post.circle_id}'
        print(f"DEBUG: Emitting 'likes_updated' for post_id {post.id} to room {room_name}")
        socketio.emit('likes_updated', data_to_emit, room=room_name, namespace='/')
    elif post.channel_id:
        print(f"DEBUG: Emitting 'likes_updated' for post_id {post.id} to room channel_{post.channel_id}")
        socketio.emit('likes_updated', data_to_emit, room=f'channel_{post.channel_id}', namespace='/')
    else:
        print(f"DEBUG: Not emitting 'likes_updated' for post_id {post.id}. No associated room.")
    
    return jsonify({"message": "Like toggled successfully", "likes_count": post.likes_count, "is_liked": is_liked})

@community_bp.route('/posts/<int:post_id>/comments', methods=['GET', 'POST'])
@login_required
def handle_comments(post_id):
    post = Post.query.get_or_404(post_id)
    
    if request.method == 'GET':
        comments = [{'username': c.user.username, 'content': c.content, 'created_at': c.created_at.strftime('%Y/%m/%d %H:%M'), 'user_id': c.user.id} for c in post.comments.order_by(Comment.created_at.asc()).all()]
        return jsonify(comments)
    
    if request.method == 'POST':
        data = request.json
        content = data.get('content')
        
        if not content:
            return jsonify({"error": "Content is required"})
            
        new_comment = Comment(content=content, post_id=post.id, user_id=current_user.id)
        db.session.add(new_comment)
        
        post.comments_count += 1
        db.session.commit()
        
        comment_data = {
            "post_id": post.id,
            "comment": {
                "id": new_comment.id,
                "username": new_comment.user.username,
                "content": new_comment.content,
                "created_at": new_comment.created_at.strftime('%Y/%m/%d %H:%M'),
                "user_id": new_comment.user.id,
                "user_profile_picture_url": new_comment.user.profile_picture_url,
                "is_liked": current_user.id in (new_comment.likes or []),
                "likes_count": len(new_comment.likes or [])
            },
            "comments_count": post.comments_count
        }
        
        if post.circle_id:
            tl_id = post.private_tl_id or 0
            room_name = f'circle_{post.circle_id}_tl_{tl_id}' if tl_id != 0 else f'channel_{post.circle_id}'
            print(f"DEBUG: Emitting 'new_comment' to room {room_name} with data: {comment_data}")
            socketio.emit('new_comment', comment_data, room=room_name, namespace='/')
        elif post.channel_id:
            print(f"DEBUG: Emitting 'new_comment' to room channel_{post.channel_id} with data: {comment_data}")
            socketio.emit('new_comment', comment_data, room=f'channel_{post.channel_id}', namespace='/')
        else:
            print(f"DEBUG: Not emitting 'new_comment' for post_id {post.id}. No associated room.")

        return jsonify({"message": "Comment added successfully", "comment_id": new_comment.id})

@community_bp.route('/comments/<int:comment_id>/like', methods=['POST'])
@login_required
def toggle_comment_like(comment_id):
    post = Post.query.get_or_404(comment_id)
    
    likes_list = post.likes or []
    
    if current_user.id in likes_list:
        likes_list.remove(current_user.id)
        is_liked = False
    else:
        likes_list.append(current_user.id)
        is_liked = True
    
    post.likes = likes_list
    post.likes_count = len(likes_list)
    
    db.session.add(post)
    db.session.commit()

    data_to_emit = {
        'comment_id': comment.id,
        'likes_count': len(likes_list),
        'is_liked': is_liked
    }
    
    if comment.post.circle_id:
        post = comment.post
        tl_id = post.private_tl_id or 0
        room_name = f'circle_{post.circle_id}_tl_{tl_id}' if tl_id != 0 else f'channel_{post.circle_id}'
        print(f"DEBUG: Emitting 'comment_likes_updated' for comment_id {comment.id} to room {room_name}")
        socketio.emit('comment_likes_updated', data_to_emit, room=room_name, namespace='/')
    elif comment.post.channel_id:
        print(f"DEBUG: Emitting 'comment_likes_updated' for comment_id {comment.id} to room channel_{comment.post.channel_id}")
        socketio.emit('comment_likes_updated', data_to_emit, room=f'channel_{comment.post.channel_id}', namespace='/')
    
    return jsonify({"message": "Comment like toggled successfully", "likes_count": len(likes_list), "is_liked": is_liked})

@community_bp.route('/posts/<int:post_id>/react', methods=['POST'])
@login_required
def toggle_reaction(post_id):
    post = Post.query.get_or_404(post_id)
    data = request.json
    emoji = data.get('emoji')

    existing_reaction = Reaction.query.filter_by(user_id=current_user.id, post_id=post.id).first()

    if existing_reaction:
        if existing_reaction.emoji == emoji:
            db.session.delete(existing_reaction)
            action = 'removed'
        else:
            existing_reaction.emoji = emoji
            action = 'updated'
    else:
        new_reaction = Reaction(user_id=current_user.id, post_id=post.id, emoji=emoji)
        db.session.add(new_reaction)
        action = 'added'

    db.session.commit()

    reaction_counts = {}
    for reaction in post.reactions:
        reaction_counts[reaction.emoji] = reaction_counts.get(reaction.emoji, 0) + 1
        
    data_to_emit = {
        'post_id': post.id,
        'reaction_counts': reaction_counts,
        'user_id': current_user.id,
        'emoji': emoji,
        'action': action
    }
    
    if post.circle_id:
        tl_id = post.private_tl_id or 0
        room_name = f'circle_{post.circle_id}_tl_{tl_id}' if tl_id != 0 else f'channel_{post.circle_id}'
        print(f"DEBUG: Emitting 'reaction_updated' to room {room_name} with data: {data_to_emit}")
        socketio.emit('reaction_updated', data_to_emit, room=room_name, namespace='/')
    elif post.channel_id:
        print(f"DEBUG: Emitting 'reaction_updated' to room channel_{post.channel_id} with data: {data_to_emit}")
        socketio.emit('reaction_updated', data_to_emit, room=f'channel_{post.channel_id}', namespace='/')
    else:
        print(f"DEBUG: Not emitting 'reaction_updated' for post_id {post.id}. No associated room.")

    return jsonify({"message": f"Reaction {action} successfully", "reaction_counts": reaction_counts})

@community_bp.route('/user/<int:user_id>/follow', methods=['POST'])
@login_required
def follow_user(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "自分自身をフォローすることはできません。"})

    user_to_follow = User.query.get_or_404(user_id)
    
    if user_to_follow.id not in (current_user.following_ids or []):
        if current_user.following_ids is None:
            current_user.following_ids = []
        current_user.following_ids.append(user_to_follow.id)
        if user_to_follow.follower_ids is None:
            user_to_follow.follower_ids = []
        user_to_follow.follower_ids.append(current_user.id)
        db.session.commit()
        return jsonify({"message": "フォローしました", "status": "followed"}), 200
    else:
        current_user.following_ids.remove(user_to_follow.id)
        if current_user.id in (user_to_follow.follower_ids or []):
            user_to_follow.follower_ids.remove(current_user.id)
        db.session.commit()
        return jsonify({"message": "フォローを解除しました", "status": "unfollowed"}), 200

@community_bp.route('/api/users/search', methods=['GET'])
@login_required
def search_users():
    query = request.args.get('q', '')
    if not query:
        return jsonify(users=[])

    users = User.query.filter(User.username.ilike(f'%{query}%')).limit(10).all()
    
    users_list = [{
        'id': user.id,
        'username': user.username,
        'profile_picture_url': user.profile_picture_url
    } for user in users]

    return jsonify(users=users_list)