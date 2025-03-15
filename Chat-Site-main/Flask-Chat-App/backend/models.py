from datetime import datetime
from extensions import db

class ChatRoom(db.Model):
    __tablename__ = 'chat_rooms'
    
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(100))
    description = db.Column(db.Text)
    password = db.Column(db.String(100))
    category = db.Column(db.String(50))
    is_private = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow)
    member_limit = db.Column(db.Integer, default=50)
    messages = db.relationship('Message', backref='room', lazy=True)
    users = db.relationship('User', backref='room', lazy=True)
    announcements = db.relationship('Announcement', backref='room', lazy=True)
    pinned_messages = db.relationship('PinnedMessage', backref='room', lazy=True)

class Message(db.Model):
    __tablename__ = 'messages'
    
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    edited_at = db.Column(db.DateTime)
    is_deleted = db.Column(db.Boolean, default=False)
    room_id = db.Column(db.String(36), db.ForeignKey('chat_rooms.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    parent_id = db.Column(db.Integer, db.ForeignKey('messages.id'))
    file_attachment = db.Column(db.String(255))
    file_type = db.Column(db.String(50))
    reactions = db.relationship('MessageReaction', backref='message', lazy=True)
    replies = db.relationship('Message', backref=db.backref('parent', remote_side=[id]))

    def __repr__(self):
        return f'<Message {self.id}>'

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100))
    avatar_url = db.Column(db.String(255))
    status_message = db.Column(db.String(100))
    is_online = db.Column(db.Boolean, default=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    is_banned = db.Column(db.Boolean, default=False)
    ban_reason = db.Column(db.String(255))
    room_id = db.Column(db.String(36), db.ForeignKey('chat_rooms.id'))
    is_owner = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    messages = db.relationship('Message', backref='author', lazy=True)
    reactions = db.relationship('MessageReaction', backref='user', lazy=True)

class MessageReaction(db.Model):
    __tablename__ = 'message_reactions'
    id = db.Column(db.Integer, primary_key=True)
    emoji = db.Column(db.String(20))
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Announcement(db.Model):
    __tablename__ = 'announcements'
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text)
    room_id = db.Column(db.String(36), db.ForeignKey('chat_rooms.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)

class PinnedMessage(db.Model):
    __tablename__ = 'pinned_messages'
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'))
    room_id = db.Column(db.String(36), db.ForeignKey('chat_rooms.id'))
    pinned_at = db.Column(db.DateTime, default=datetime.utcnow)
    pinned_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))

class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(36), db.ForeignKey('chat_rooms.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(50))
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class UserTyping(db.Model):
    __tablename__ = 'user_typing'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    room_id = db.Column(db.String(36), db.ForeignKey('chat_rooms.id'))
    last_typed = db.Column(db.DateTime, default=datetime.utcnow) 