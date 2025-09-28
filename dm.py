# dm.py

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_
from flask_socketio import emit, join_room, leave_room
from datetime import datetime

# dm_bpルートの定義
dm_bp = Blueprint('dm', __name__, url_prefix='/dm')

# SocketIOインスタンスをグローバル変数として保持
socketio = None

def init_dm_socketio(sio_instance):
    """DM機能のSocketIOイベントハンドラを登録する関数"""
    global socketio
    socketio = sio_instance
    
    @socketio.on('connect', namespace='/')
    def handle_connect():
        # 💡Import models here to break the circular import loop💡
        from models import DirectMessageConversation, DirectMessage, User, db
        
        if not current_user.is_authenticated:
            return False
        join_room(f'user_{current_user.id}')
    
    @socketio.on('join_dm_room', namespace='/')
    def handle_join_dm_room(data):
        # 💡Import models here💡
        from models import DirectMessageConversation, DirectMessage, User, db
        
        if not current_user.is_authenticated:
            return
        conversation_id = data.get('conversation_id')
        if conversation_id:
            room_name = f'conversation_{conversation_id}'
            join_room(room_name)
            print(f'User {current_user.id} joined room: {room_name}')

    @socketio.on('send_dm', namespace='/')
    def handle_send_dm(data):
        # 💡Import models here💡
        from models import DirectMessageConversation, DirectMessage, User, db
        
        if not current_user.is_authenticated:
            return
        
        recipient_id = data.get('recipient_id')
        content = data.get('content')
        
        if not recipient_id or not content:
            return
            
        conv = DirectMessageConversation.query.filter(
            or_(
                (DirectMessageConversation.user1_id == current_user.id) & (DirectMessageConversation.user2_id == recipient_id),
                (DirectMessageConversation.user1_id == recipient_id) & (DirectMessageConversation.user2_id == current_user.id)
            )
        ).first()

        if not conv:
            conv = DirectMessageConversation(user1_id=current_user.id, user2_id=recipient_id)
            db.session.add(conv)
            db.session.commit()

        new_message = DirectMessage(
            conversation_id=conv.id,
            sender_id=current_user.id,
            recipient_id=recipient_id,
            content=content
        )
        db.session.add(new_message)
        db.session.commit()
        
        message_data = {
            'sender_id': current_user.id,
            'content': new_message.content,
            'timestamp': new_message.timestamp.isoformat()
        }
        
        # 参加しているルームにメッセージを送信
        emit('receive_dm', message_data, room=f'conversation_{conv.id}', namespace='/')

# DMリストを取得するAPI
@dm_bp.route('/api/dms', methods=['GET'])
@login_required
def get_dm_list():
    # 💡Import models here💡
    from models import DirectMessageConversation, DirectMessage, User
    
    conversations = DirectMessageConversation.query.filter(
        or_(
            DirectMessageConversation.user1_id == current_user.id,
            DirectMessageConversation.user2_id == current_user.id
        )
    ).all()

    dm_list = []
    for conv in conversations:
        other_user_id = conv.user1_id if conv.user2_id == current_user.id else conv.user2_id
        other_user = User.query.get(other_user_id)
        last_message = DirectMessage.query.filter_by(conversation_id=conv.id).order_by(DirectMessage.timestamp.desc()).first()

        if other_user:
            dm_list.append({
                'conversation_id': conv.id,
                'user_id': other_user.id,
                'username': other_user.username,
                'profile_picture_url': other_user.profile_picture_url,
                'last_message': last_message.content if last_message else '新しいDM',
                'last_message_timestamp': last_message.timestamp.strftime('%Y/%m/%d %H:%M') if last_message else None
            })
    return jsonify(dms=dm_list)

# 特定ユーザーとのチャット履歴のAPIエンドポイント
@dm_bp.route('/api/dms/<int:user_id>/messages', methods=['GET'])
@login_required
def get_dm_history(user_id):
    # 💡Import models here💡
    from models import DirectMessageConversation, DirectMessage
    
    conv = DirectMessageConversation.query.filter(
        or_(
            (DirectMessageConversation.user1_id == current_user.id) & (DirectMessageConversation.user2_id == user_id),
            (DirectMessageConversation.user1_id == user_id) & (DirectMessageConversation.user2_id == current_user.id)
        )
    ).first()

    if not conv:
        return jsonify(messages=[], conversation_id=None)

    messages = DirectMessage.query.filter_by(conversation_id=conv.id).order_by(DirectMessage.timestamp.asc()).all()
    messages_list = [{
        'sender_id': msg.sender_id,
        'content': msg.content,
        'timestamp': msg.timestamp.strftime('%Y/%m/%d %H:%M')
    } for msg in messages]

    return jsonify(messages=messages_list, conversation_id=conv.id)


# 特定ユーザーとのチャットページ
@dm_bp.route('/<int:user_id>', methods=['GET'])
@login_required
def get_chat_page(user_id):
    # 💡Import models here💡
    from models import DirectMessageConversation, User
    
    other_user = User.query.get_or_404(user_id)
    
    # 会話IDをテンプレートに渡す
    conversation = DirectMessageConversation.query.filter(
        or_(
            (DirectMessageConversation.user1_id == current_user.id) & (DirectMessageConversation.user2_id == user_id),
            (DirectMessageConversation.user1_id == user_id) & (DirectMessageConversation.user2_id == current_user.id)
        )
    ).first()
    
    conversation_id = conversation.id if conversation else None
    
    return render_template('chat_page.html', other_user=other_user, current_user_id=current_user.id, conversation_id=conversation_id)