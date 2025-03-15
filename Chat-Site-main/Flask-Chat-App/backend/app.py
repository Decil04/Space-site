import os
import logging
import uuid
import random
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, session, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from proxy import ProxyMiddleware
from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from dotenv import load_dotenv
from extensions import db
from models import ChatRoom, Message, User, MessageReaction, Announcement, PinnedMessage, UserTyping, ActivityLog
from sqlalchemy import text
from werkzeug.utils import secure_filename
from config import config
import sqlite3
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG)

load_dotenv()

# Initialize Flask app with configuration
app = Flask(__name__)
app.config.from_object(config[os.getenv('FLASK_ENV', 'default')])
config[os.getenv('FLASK_ENV', 'default')].init_app(app)

# Set a secret key for session management
app.secret_key = os.getenv('SECRET_KEY', 'dev_key_for_testing_only')

# Configure app for redirects
app.config['PREFERRED_URL_SCHEME'] = 'http'
app.config['APPLICATION_ROOT'] = '/'

# Initialize extensions
db.init_app(app)
socketio = SocketIO(app)

# Proxy middleware
app.wsgi_app = ProxyMiddleware(app.wsgi_app)

# Store active users per room
active_users = {}
# Store soldiers with permission to speak
soldiers_with_permission = {}

def get_db_connection():
    """
    Create a connection to the SQLite database
    """
    conn = sqlite3.connect('chat.db')
    conn.row_factory = sqlite3.Row
    return conn

def generate_room_id():
    return str(uuid.uuid4())[:8]

def cleanup_old_messages():
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    Message.query.filter(Message.timestamp < cutoff_time).delete()
    db.session.commit()

def init_db():
    """
    Initialize the database with tables
    """
    print("Initializing database...")
    try:
        # Check if database file exists
        db_exists = os.path.exists('chat.db')
        print(f"Database file exists: {db_exists}")
        
        conn = get_db_connection()
        print("Database connection established for initialization")
        
        # Create tables if they don't exist
        conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_rooms (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(100),
                description TEXT,
                password VARCHAR(100),
                category VARCHAR(50),
                is_private BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                is_secret BOOLEAN DEFAULT 0,
                member_limit INTEGER DEFAULT 50,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(100),
                avatar_url VARCHAR(255),
                status_message VARCHAR(100),
                is_online BOOLEAN DEFAULT 1,
                last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_banned BOOLEAN DEFAULT 0,
                ban_reason VARCHAR(255),
                room_id VARCHAR(36),
                is_owner BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (room_id) REFERENCES chat_rooms(id)
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                edited_at DATETIME,
                is_deleted BOOLEAN DEFAULT 0,
                room_id VARCHAR(36),
                user_id INTEGER,
                parent_id INTEGER,
                file_attachment VARCHAR(255),
                file_type VARCHAR(50),
                FOREIGN KEY (room_id) REFERENCES chat_rooms(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (parent_id) REFERENCES messages(id)
            )
        ''')
        
        conn.commit()
        print("Database tables created successfully")
        conn.close()
        print("Database initialization completed")
        return True
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# Initialize database when app starts
print("Checking database on app startup...")
init_db()

def allowed_file(filename):
    """
    Check if the file extension is allowed based on the configuration.
    """
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

rate_limits = {}

def rate_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'No session'}), 403

        current_time = datetime.now()
        if user_id in rate_limits:
            last_request = rate_limits[user_id]
            if current_time - last_request < timedelta(seconds=1):
                return jsonify({'error': 'Rate limit exceeded'}), 429

        rate_limits[user_id] = current_time
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def before_request():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())

@app.route('/')
def index():
    print("Rendering index page")
    return render_template('chat.html')

@app.route('/create_room', methods=['POST'])
def create_room():
    """
    Create a new chat room
    """
    print("Create room route called")
    print(f"Form data: {request.form}")
    
    username = request.form.get('username')
    planet = request.form.get('planet', 'earth')
    custom_id = request.form.get('custom_id', '')
    member_limit_str = request.form.get('member_limit', '')
    
    print(f"Username: {username}, Planet: {planet}, Custom ID: {custom_id}, Member Limit: {member_limit_str}")
    
    if not username:
        print("Username is required but not provided")
        flash('Username is required')
        return redirect(url_for('index'))
    
    # Generate a unique room ID based on the planet
    planet_name = planet.capitalize()
    
    # Use custom ID if provided, otherwise generate one
    if custom_id:
        room_id = f"{planet_name}-{custom_id}"
    else:
        room_id = f"{planet_name}-{str(uuid.uuid4())[:8]}"
    
    print(f"Generated room ID: {room_id}")
    
    # Set room description based on planet
    room_description = f"A chat room on {planet_name}"
    
    # Set member limits based on planet or use custom limit if provided
    default_member_limits = {
        'Mercury': 10,
        'Venus': 20,
        'Earth': 50,
        'Mars': 30,
        'Jupiter': 100,
        'Saturn': 80,
        'Uranus': 40,
        'Neptune': 40
    }
    
    # Use custom member limit if provided
    if member_limit_str and member_limit_str.isdigit():
        member_limit = int(member_limit_str)
        print(f"Using custom member limit: {member_limit}")
    else:
        member_limit = default_member_limits.get(planet_name, 50)
        print(f"Using default member limit for {planet_name}: {member_limit}")
    
    # Debug output
    print(f"Creating room with: username={username}, planet={planet}, room_id={room_id}, member_limit={member_limit}")
    
    try:
        # Ensure database file exists
        if not os.path.exists('chat.db'):
            print("Database file doesn't exist, creating tables...")
            init_db()
            print("Database initialized")
        
        conn = get_db_connection()
        print("Database connection established")
        
        # Create the room
        conn.execute('''
            INSERT INTO chat_rooms (id, name, description, category, is_active, member_limit, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (room_id, f"{planet_name} Base", room_description, planet_name, 1, member_limit, datetime.now()))
        
        print(f"Room record created: {room_id}")
        
        # Create the user as owner
        conn.execute('''
            INSERT INTO users (username, room_id, is_owner, is_online, joined_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (username, room_id, 1, 1, datetime.now()))
        
        print(f"User record created: {username} as owner of {room_id}")
        
        conn.commit()
        print("Database transaction committed")
        conn.close()
        print(f"Room created successfully: {room_id}")
    except Exception as e:
        print(f"Error creating room: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error details: {e.__dict__}")
        import traceback
        traceback.print_exc()
        flash(f'Error creating room: {str(e)}')
        return redirect(url_for('index'))
    
    # Redirect to the join page
    redirect_url = f"/join_new_chat_room/{room_id}?username={username}"
    print(f"Redirecting to join_new_chat_room with URL: {redirect_url}")
    
    # Use direct redirect instead of url_for
    return redirect(redirect_url)

@app.route('/join_room', methods=['POST'])
def join_existing_room():
    print("Join existing room route called")
    print(f"Form data: {request.form}")
    
    username = request.form.get('username')
    room_id = request.form.get('room_id')
    
    print(f"Username: {username}, Room ID: {room_id}")
    
    if not username or not room_id:
        print("Missing username or room_id, redirecting to index")
        return redirect(url_for('index'))
    
    # Check if the room exists
    conn = get_db_connection()
    room = conn.execute('SELECT * FROM chat_rooms WHERE id = ? AND is_active = 1', (room_id,)).fetchone()
    
    if not room:
        print(f"Room not found or inactive: {room_id}")
        conn.close()
        flash('Chat room not found or is inactive')
        return redirect(url_for('index'))
    
    print(f"Room found: {room}")
    conn.close()
    
    # Redirect to the chat_existing_room page
    # Use a direct URL construction to avoid any issues with url_for
    redirect_url = f"/chat_existing_room/{room_id}?username={username}"
    print(f"Redirecting to chat_existing_room with URL: {redirect_url}")
    return redirect(redirect_url)

@app.route('/join_new_chat_room/<room_id>')
def join_new_chat_room(room_id):
    """
    Display the join new chat room page for a specific room
    """
    print(f"Join new chat room route called for room_id: {room_id}")
    username = request.args.get('username', '')
    print(f"Username from args: {username}")
    
    # Check if the room exists
    try:
        conn = get_db_connection()
        room = conn.execute('SELECT * FROM chat_rooms WHERE id = ? AND is_active = 1', (room_id,)).fetchone()
        
        if not room:
            print(f"Room not found or inactive: {room_id}")
            conn.close()
            flash('Chat room not found or is inactive')
            return redirect(url_for('index'))
        
        print(f"Room found: {room['id']}, Name: {room['name']}")
        
        # Check if the user is the owner
        is_owner = False
        role = "Crew Member"
        
        if username:
            user = conn.execute('SELECT * FROM users WHERE username = ? AND room_id = ?', 
                               (username, room_id)).fetchone()
            if user:
                is_owner = bool(user['is_owner'])
                role = "Major" if is_owner else "Crew Member"
                print(f"User found: {username}, is_owner: {is_owner}, role: {role}")
            else:
                print(f"User not found: {username}")
        
        conn.close()
        
        print(f"Rendering join_new_chat_room.html with room_id={room_id}, username={username}, is_owner={is_owner}, role={role}")
        return render_template('join_new_chat_room.html', 
                              room_id=room_id, 
                              username=username, 
                              is_owner=is_owner,
                              role=role)
    except Exception as e:
        print(f"Error in join_new_chat_room: {str(e)}")
        flash(f'Error joining chat room: {str(e)}')
        return redirect(url_for('index'))

@app.route('/chat_existing_room/<room_id>')
def chat_existing_room(room_id):
    """
    Display the chat room page for a specific room
    """
    print(f"Chat existing room route called for room_id: {room_id}")
    username = request.args.get('username', '')
    print(f"Username from args: {username}")
    
    # Check if the room exists
    conn = get_db_connection()
    room = conn.execute('SELECT * FROM chat_rooms WHERE id = ? AND is_active = 1', (room_id,)).fetchone()
    
    if not room:
        print(f"Room not found or inactive: {room_id}")
        conn.close()
        flash('Chat room not found or is inactive')
        return redirect(url_for('index'))
    
    print(f"Room found: {room}")
    
    # Check if the user is the owner
    is_owner = False
    
    if username:
        user = conn.execute('SELECT * FROM users WHERE username = ? AND room_id = ?', 
                           (username, room_id)).fetchone()
        if user:
            is_owner = bool(user['is_owner'])
            print(f"User found: {username}, is_owner: {is_owner}")
        else:
            print(f"User not found: {username}")
            # Create the user as a regular member
            try:
                conn.execute('''
                    INSERT INTO users (username, room_id, is_owner, is_online, joined_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (username, room_id, 0, 1, datetime.now()))
                conn.commit()
                print(f"Created new user: {username} for room: {room_id}")
            except Exception as e:
                print(f"Error creating user: {str(e)}")
    
    conn.close()
    
    print(f"Rendering chat_existing_room.html with room_id={room_id}, username={username}, is_owner={is_owner}")
    return render_template('chat_existing_room.html', 
                          room_id=room_id, 
                          username=username, 
                          is_owner=is_owner)

@app.route('/room/<room_id>')
def chat_room(room_id):
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))
    
    room = ChatRoom.query.get_or_404(room_id)
    if not room.is_active:
        return redirect(url_for('index'))
    
    # Check ownership through User model
    user = User.query.filter_by(room_id=room_id, username=username).first()
    is_owner = user and user.is_owner
    role = 'Major' if is_owner else 'Soldier'
    return render_template('chat_room.html', room_id=room_id, username=username, is_owner=is_owner, role=role)

@app.route('/api/room/<room_id>/close', methods=['POST'])
def close_room(room_id):
    """
    Close a chat room
    """
    conn = get_db_connection()
    
    # Check if the room exists
    room = conn.execute('SELECT * FROM chat_rooms WHERE id = ?', (room_id,)).fetchone()
    
    if not room:
        conn.close()
        return jsonify({'status': 'error', 'error': 'Room not found'})
    
    try:
        # Update room status
        conn.execute('UPDATE chat_rooms SET is_active = 0 WHERE id = ?', (room_id,))
        
        # Update all users in the room
        conn.execute('UPDATE users SET is_online = 0 WHERE room_id = ?', (room_id,))
        
        conn.commit()
        conn.close()
        
        # Notify all users in the room
        socketio.emit('room_closed', {}, room=room_id)
        
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'error', 'error': str(e)})

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('join')
def on_join(data):
    """
    Handle a user joining a room
    """
    username = data.get('username')
    room_id = data.get('room')
    
    if not username or not room_id:
        return
    
    # Join the room
    join_room(room_id)
    
    # Check if the room exists and is active
    conn = get_db_connection()
    room = conn.execute('SELECT * FROM chat_rooms WHERE id = ? AND is_active = 1', (room_id,)).fetchone()
    
    if not room:
        conn.close()
        return
    
    # Get or create user
    user = conn.execute('SELECT * FROM users WHERE username = ? AND room_id = ?', 
                       (username, room_id)).fetchone()
    
    is_owner = False
    
    if not user:
        # Create new user
        conn.execute('''
            INSERT INTO users (username, room_id, is_online, joined_at)
            VALUES (?, ?, ?, ?)
        ''', (username, room_id, 1, datetime.now()))
        conn.commit()
    else:
        # Update user status
        conn.execute('''
            UPDATE users SET is_online = 1 WHERE id = ?
        ''', (user['id'],))
        conn.commit()
        is_owner = bool(user['is_owner'])
    
    # Notify all users in the room
    emit('user_joined', {
        'username': username,
        'isOwner': is_owner
    }, room=room_id)
    
    # Send updated user list to all users in the room
    users = conn.execute('SELECT id, username, is_owner, is_online FROM users WHERE room_id = ?', 
                        (room_id,)).fetchall()
    
    user_list = []
    for user in users:
        user_list.append({
            'id': user['id'],
            'username': user['username'],
            'isOwner': bool(user['is_owner']),
            'isOnline': bool(user['is_online'])
        })
    
    emit('user_list_update', {'users': user_list}, room=room_id)
    
    conn.close()

@socketio.on('remove_user')
def handle_remove_user(data):
    room_id = data.get('room')
    username_to_remove = data.get('username')
    
    # Check ownership through User model
    user = User.query.filter_by(room_id=room_id, username=session.get('username')).first()
    if user and user.is_owner:
        if room_id in active_users and username_to_remove in active_users[room_id]:
            user_id = active_users[room_id][username_to_remove]
            del active_users[room_id][username_to_remove]
            emit('user_removed', {'username': username_to_remove}, room=room_id)

@socketio.on('request_permission')
def handle_permission_request(data):
    username = data.get('username')
    room = data.get('room')
    
    room_obj = ChatRoom.query.get(room)
    if not room_obj or not room_obj.is_active:
        emit('room_closed', {})
        return
    
    # Emit permission request to the Major
    emit('permission_requested', {
        'username': username,
        'timestamp': datetime.now().strftime("%H:%M")
    }, room=room)

@socketio.on('grant_permission')
def handle_grant_permission(data):
    username = data.get('username')
    room = data.get('room')
    granted = data.get('granted', False)
    
    # Check ownership through User model
    user = User.query.filter_by(room_id=room, username=session.get('username')).first()
    if user and user.is_owner:
        if granted:
            if room not in soldiers_with_permission:
                soldiers_with_permission[room] = set()
            soldiers_with_permission[room].add(username)
        else:
            if room in soldiers_with_permission:
                soldiers_with_permission[room].discard(username)
        
        emit('permission_response', {
            'username': username,
            'granted': granted
        }, room=room)

@socketio.on('message')
def handle_message(data):
    """
    Handle a message sent by a user
    """
    try:
        # Check if required data is present
        username = data.get('username')
        room_id = data.get('room')
        message_text = data.get('message')
        
        if not username or not room_id or not message_text:
            emit('message_error', {'error': 'Missing required data'})
            return
        
        # Check if the room exists and is active
        conn = get_db_connection()
        room = conn.execute('SELECT * FROM chat_rooms WHERE id = ? AND is_active = 1', (room_id,)).fetchone()
        
        if not room:
            conn.close()
            emit('message_error', {'error': 'Room not found or inactive'})
            return
        
        # Get or create user
        user = conn.execute('SELECT * FROM users WHERE username = ? AND room_id = ?', 
                           (username, room_id)).fetchone()
        
        if not user:
            # Create new user
            conn.execute('''
                INSERT INTO users (username, room_id, is_online, joined_at)
                VALUES (?, ?, ?, ?)
            ''', (username, room_id, 1, datetime.now()))
            conn.commit()
            
            # Get the new user
            user = conn.execute('SELECT * FROM users WHERE username = ? AND room_id = ?', 
                               (username, room_id)).fetchone()
        
        # Create message
        timestamp = datetime.now()
        conn.execute('''
            INSERT INTO messages (content, timestamp, room_id, user_id)
            VALUES (?, ?, ?, ?)
        ''', (message_text, timestamp, room_id, user['id']))
        conn.commit()
        
        # Broadcast message to all users in the room
        emit('message', {
            'username': username,
            'content': message_text,
            'timestamp': timestamp.isoformat()
        }, room=room_id)
        
        conn.close()
    except Exception as e:
        print(f"Error handling message: {str(e)}")
        emit('message_error', {'error': 'Failed to save message'})

@socketio.on('disconnect')
def on_disconnect():
    """
    Handle a user disconnecting
    """
    # This is a bit tricky since we don't have the room and username directly
    # We'll need to find all rooms the user is in and update their status
    
    for room_id in rooms():
        # Skip the default room
        if room_id == request.sid:
            continue
        
        # Find the user in the room
        conn = get_db_connection()
        
        # We don't know the username, so we'll update all users with this session ID
        conn.execute('''
            UPDATE users SET is_online = 0 WHERE room_id = ?
        ''', (room_id,))
        conn.commit()
        
        # Send updated user list to all users in the room
        users = conn.execute('SELECT id, username, is_owner, is_online FROM users WHERE room_id = ?', 
                            (room_id,)).fetchall()
        
        user_list = []
        for user in users:
            user_list.append({
                'id': user['id'],
                'username': user['username'],
                'isOwner': bool(user['is_owner']),
                'isOnline': bool(user['is_online'])
            })
        
        emit('user_list_update', {'users': user_list}, room=room_id)
        
        conn.close()
        
        # Leave the room
        leave_room(room_id)

@app.route('/api/messages/<room_id>', methods=['GET'])
def get_messages(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    if not room.is_active:
        return jsonify({'error': 'Room is closed'}), 404
    
    messages = Message.query.filter_by(room_id=room_id).order_by(Message.timestamp.asc()).all()
    return jsonify({
        'messages': [{
            'id': msg.id,
            'message': msg.content,
            'timestamp': msg.timestamp.strftime("%H:%M"),
            'username': msg.author.username if msg.author else 'Unknown'
        } for msg in messages]
    })

@app.route('/api/messages/<room_id>', methods=['POST'])
@rate_limit
def post_message(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    message_content = request.json.get('message', '').strip()
    if not message_content:
        return jsonify({'error': 'Empty message'}), 400

    # Get user from database or create if doesn't exist
    username = session.get('username')
    user = User.query.filter_by(username=username, room_id=room_id).first()
    if not user:
        user = User(username=username, room_id=room_id)
        db.session.add(user)
        db.session.commit()

    new_message = Message(
        content=message_content,
        user_id=user.id,
        room_id=room_id,
        timestamp=datetime.utcnow()
    )
    db.session.add(new_message)
    db.session.commit()
    return jsonify({'status': 'success'})

# Cleanup old messages periodically
@app.before_request
def cleanup():
    if random.random() < 0.01:  # 1% chance to cleanup on each request
        cleanup_old_messages()

# Add member limit functionality
@app.route('/api/room/<room_id>/set_limit', methods=['POST'])
def set_room_limit(room_id):
    """
    Set the member limit for a room
    """
    data = request.json
    limit = data.get('limit', 50)
    
    if limit < 1 or limit > 100:
        return jsonify({'status': 'error', 'error': 'Limit must be between 1 and 100'})
    
    conn = get_db_connection()
    try:
        conn.execute('UPDATE chat_rooms SET member_limit = ? WHERE id = ?', (limit, room_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/create_secret_room', methods=['POST'])
def create_secret_room():
    username = request.form.get('username')
    if not username:
        return redirect(url_for('index'))
    
    room_id = generate_room_id()
    # Create new room
    room = ChatRoom(
        id=room_id,
        is_active=True,
        is_secret=True,
        member_limit=request.form.get('member_limit', type=int) or 10
    )
    
    # Create user and set as owner
    user = User(
        username=username,
        is_owner=True,
        is_active=True,
        room_id=room_id
    )
    
    db.session.add(room)
    db.session.add(user)
    db.session.commit()
    
    session['username'] = username
    session['user_id'] = str(user.id)
    return redirect(url_for('secret_chat_room', room_id=room_id))

@app.route('/secret_room/<room_id>')
def secret_chat_room(room_id):
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))
    
    room = ChatRoom.query.get_or_404(room_id)
    if not room.is_active or not room.is_secret:
        return redirect(url_for('index'))
    
    # Check member limit
    if len(active_users.get(room_id, {})) >= room.member_limit:
        flash('Room has reached maximum capacity')
        return redirect(url_for('index'))
    
    # Check ownership through User model
    user = User.query.filter_by(room_id=room_id, username=username).first()
    is_owner = user and user.is_owner
    role = 'Major' if is_owner else 'Soldier'
    return render_template('secret_chat_room.html', room_id=room_id, username=username, is_owner=is_owner, role=role)

@app.route('/api/room/<room_id>/messages')
def get_room_messages(room_id):
    try:
        messages = Message.query.filter_by(room_id=room_id)\
            .order_by(Message.timestamp.desc())\
            .all()
        
        return jsonify([{
            'username': msg.author.username if msg.author else 'Unknown',
            'message': msg.content,
            'timestamp': msg.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        } for msg in messages])
    except Exception as e:
        print(f"Error fetching messages: {e}")
        return jsonify({'error': 'Failed to fetch messages'}), 500

@app.route('/api/room/<room_id>/users')
def get_room_users(room_id):
    """
    Get all users in a specific room
    """
    conn = get_db_connection()
    users = conn.execute('SELECT id, username, is_owner, is_online FROM users WHERE room_id = ?', 
                        (room_id,)).fetchall()
    
    user_list = []
    for user in users:
        user_list.append({
            'id': user['id'],
            'username': user['username'],
            'isOwner': bool(user['is_owner']),
            'isOnline': bool(user['is_online'])
        })
    
    conn.close()
    return jsonify(user_list)

# Message Management
@app.route('/api/message/<message_id>/edit', methods=['PUT'])
def edit_message(message_id):
    message = Message.query.get_or_404(message_id)
    if message.user_id != session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    content = request.json.get('content')
    if not content:
        return jsonify({'error': 'Empty message'}), 400
    
    message.content = content
    message.edited_at = datetime.now()
    db.session.commit()
    
    return jsonify({'status': 'success'})

@app.route('/api/message/<message_id>/delete', methods=['DELETE'])
def delete_message(message_id):
    message = Message.query.get_or_404(message_id)
    if message.user_id != session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    message.is_deleted = True
    db.session.commit()
    
    return jsonify({'status': 'success'})

@app.route('/api/message/<message_id>/react', methods=['POST'])
def react_to_message(message_id):
    emoji = request.json.get('emoji')
    user_id = session.get('user_id')
    
    existing_reaction = MessageReaction.query.filter_by(
        message_id=message_id,
        user_id=user_id,
        emoji=emoji
    ).first()
    
    if existing_reaction:
        db.session.delete(existing_reaction)
    else:
        reaction = MessageReaction(
            message_id=message_id,
            user_id=user_id,
            emoji=emoji
        )
        db.session.add(reaction)
    
    db.session.commit()
    return jsonify({'status': 'success'})

# File Upload
@app.route('/api/message/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return jsonify({
            'status': 'success',
            'file_url': url_for('uploaded_file', filename=filename)
        })

    return jsonify({'error': 'Invalid file type'}), 400

# User Profile
@app.route('/api/user/profile', methods=['GET', 'PUT'])
def user_profile():
    user = User.query.get(session.get('user_id'))
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if request.method == 'GET':
        return jsonify({
            'username': user.username,
            'avatar_url': user.avatar_url,
            'status_message': user.status_message
        })

    # Update profile
    data = request.json
    if 'avatar_url' in data:
        user.avatar_url = data['avatar_url']
    if 'status_message' in data:
        user.status_message = data['status_message']

    db.session.commit()
    return jsonify({'status': 'success'})

# Room Management
@app.route('/api/room/<room_id>/announce', methods=['POST'])
def create_announcement(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    # Check ownership through User model
    user = User.query.filter_by(room_id=room_id, username=session.get('username')).first()
    if not user or not user.is_owner:
        return jsonify({'error': 'Unauthorized'}), 403

    content = request.json.get('content')
    expires_at = request.json.get('expires_at')

    announcement = Announcement(
        content=content,
        room_id=room_id,
        expires_at=datetime.fromisoformat(expires_at) if expires_at else None
    )
    db.session.add(announcement)
    db.session.commit()

    return jsonify({'status': 'success'})

@app.route('/api/room/<room_id>/pin/<message_id>', methods=['POST'])
def pin_message(room_id, message_id):
    room = ChatRoom.query.get_or_404(room_id)
    # Check ownership through User model
    user = User.query.filter_by(room_id=room_id, username=session.get('username')).first()
    if not user or not user.is_owner:
        return jsonify({'error': 'Unauthorized'}), 403

    pinned = PinnedMessage(
        message_id=message_id,
        room_id=room_id,
        pinned_by_id=session.get('user_id')
    )
    db.session.add(pinned)
    db.session.commit()

    return jsonify({'status': 'success'})

# Typing Indicators
@socketio.on('typing')
def handle_typing(data):
    """
    Handle a typing notification
    """
    username = data.get('username')
    room = data.get('room')

    if username and room:
        emit('typing', {'username': username}, room=room, include_self=False)

# Activity Logging
def log_activity(room_id, user_id, action, details=None):
    log = ActivityLog(
        room_id=room_id,
        user_id=user_id,
        action=action,
        details=details
    )
    db.session.add(log)
    db.session.commit()

# Admin Functions
@app.route('/api/admin/room/<room_id>/ban/<user_id>', methods=['POST'])
def ban_user(room_id, user_id):
    room = ChatRoom.query.get_or_404(room_id)
    # Check ownership through User model
    admin_user = User.query.filter_by(room_id=room_id, username=session.get('username')).first()
    if not admin_user or not admin_user.is_owner:
        return jsonify({'error': 'Unauthorized'}), 403

    user = User.query.get_or_404(user_id)
    user.is_banned = True
    user.ban_reason = request.json.get('reason', 'No reason provided')
    db.session.commit()

    log_activity(room_id, session.get('user_id'), 'ban_user', f'Banned user: {user.username}')
    return jsonify({'status': 'success'})

@app.route('/api/room/<room_id>/export', methods=['GET'])
def export_chat_logs(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    # Check ownership through User model
    user = User.query.filter_by(room_id=room_id, username=session.get('username')).first()
    if not user or not user.is_owner:
        return jsonify({'error': 'Unauthorized'}), 403

    messages = Message.query.filter_by(room_id=room_id).order_by(Message.timestamp.asc()).all()
    logs = [{
        'username': msg.author.username,
        'content': msg.content,
        'timestamp': msg.timestamp.isoformat(),
        'edited': msg.edited_at is not None
    } for msg in messages]

    return jsonify(logs)

@app.route('/api/room/<room_id>/update_id', methods=['POST'])
def update_room_id(room_id):
    """
    Update the room ID
    """
    data = request.json
    new_room_id = data.get('new_room_id', '')

    if not new_room_id:
        return jsonify({'status': 'error', 'error': 'New room ID is required'})

    if len(new_room_id) < 3 or len(new_room_id) > 30:
        return jsonify({'status': 'error', 'error': 'Room ID must be between 3 and 30 characters'})

    conn = get_db_connection()

    # Check if the new room ID already exists
    existing_room = conn.execute('SELECT id FROM chat_rooms WHERE id = ?', (new_room_id,)).fetchone()
    if existing_room:
        conn.close()
        return jsonify({'status': 'error', 'error': 'Room ID already exists'})

    try:
        # Update the room ID
        conn.execute('UPDATE chat_rooms SET id = ? WHERE id = ?', (new_room_id, room_id))

        # Update all users in the room
        conn.execute('UPDATE users SET room_id = ? WHERE room_id = ?', (new_room_id, room_id))

        # Update all messages in the room
        conn.execute('UPDATE messages SET room_id = ? WHERE room_id = ?', (new_room_id, room_id))

        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'new_room_id': new_room_id})
    except Exception as e:
        conn.close()
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/api/planets/stats', methods=['GET'])
def get_planet_stats():
    """Get statistics for all planets including active users and capacity."""
    try:
        # Get all active rooms
        rooms = ChatRoom.query.filter_by(is_active=True).all()

        # Initialize stats for each planet
        planet_stats = {
            'mercury': {'active_users': 0, 'capacity': 10, 'rooms': 0},
            'venus': {'active_users': 0, 'capacity': 20, 'rooms': 0},
            'earth': {'active_users': 0, 'capacity': 50, 'rooms': 0},
            'mars': {'active_users': 0, 'capacity': 30, 'rooms': 0},
            'jupiter': {'active_users': 0, 'capacity': 100, 'rooms': 0},
            'saturn': {'active_users': 0, 'capacity': 80, 'rooms': 0},
            'uranus': {'active_users': 0, 'capacity': 40, 'rooms': 0},
            'neptune': {'active_users': 0, 'capacity': 40, 'rooms': 0}
        }

        # Count active users and rooms for each planet
        for room in rooms:
            planet = room.category.lower()
            if planet in planet_stats:
                # Count active users in this room
                active_users_count = User.query.filter_by(room_id=room.id, is_active=True).count()

                # Update planet stats
                planet_stats[planet]['active_users'] += active_users_count
                planet_stats[planet]['rooms'] += 1

        return jsonify(planet_stats)
    except Exception as e:
        print(f"Error getting planet stats: {str(e)}")
        return jsonify({'error': 'Failed to get planet statistics'}), 500

@app.route('/debug')
def debug_info():
    """
    Debug route to show information about the application
    """
    info = {
        'routes': [rule.rule for rule in app.url_map.iter_rules()],
        'session': dict(session),
        'request': {
            'path': request.path,
            'method': request.method,
            'args': dict(request.args),
            'form': dict(request.form),
            'cookies': dict(request.cookies),
            'headers': dict(request.headers)
        }
    }
    return jsonify(info)

@app.route('/test_redirect')
def test_redirect():
    """
    Test route to verify that redirection is working
    """
    return redirect('/debug')

@app.route('/test_form')
def test_form():
    """
    A simple test page for form submission
    """
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Form Test</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            form { margin-bottom: 20px; padding: 20px; border: 1px solid #ccc; }
            input, button { margin: 5px; padding: 8px; }
            h2 { color: #333; }
        </style>
    </head>
    <body>
        <h1>Form Test Page</h1>
        
        <h2>Create Room Form</h2>
        <form action="/create_room" method="post">
            <div>
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" required>
            </div>
            <div>
                <label for="planet">Planet:</label>
                <input type="text" id="planet" name="planet" value="earth">
            </div>
            <button type="submit">Create Room</button>
        </form>
        
        <h2>Join Room Form</h2>
        <form action="/join_room" method="post">
            <div>
                <label for="join_username">Username:</label>
                <input type="text" id="join_username" name="username" required>
            </div>
            <div>
                <label for="room_id">Room ID:</label>
                <input type="text" id="room_id" name="room_id" required>
            </div>
            <button type="submit">Join Room</button>
        </form>
    </body>
    </html>
    """

@app.route('/debug_form', methods=['GET', 'POST'])
def debug_form():
    """
    Debug route to test form submission
    """
    if request.method == 'POST':
        # Log all form data
        print("Form data received:")
        for key, value in request.form.items():
            print(f"  {key}: {value}")
        
        # Create a response with the form data
        response = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Form Debug</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                pre {{ background-color: #f0f0f0; padding: 10px; }}
            </style>
        </head>
        <body>
            <h1>Form Data Received</h1>
            <pre>{request.form}</pre>
            
            <h2>What would happen next:</h2>
            <p>If this was a create_room request, you would be redirected to: 
               /join_new_chat_room/ROOM_ID?username={request.form.get('username', '')}</p>
            
            <p>If this was a join_room request, you would be redirected to:
               /chat_existing_room/ROOM_ID?username={request.form.get('username', '')}</p>
            
            <p><a href="/">Back to home</a></p>
        </body>
        </html>
        """
        return response
    
    # GET request - show a form
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Debug Form</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            form { margin-bottom: 20px; padding: 20px; border: 1px solid #ccc; }
            input, button { margin: 5px; padding: 8px; }
            h2 { color: #333; }
        </style>
    </head>
    <body>
        <h1>Debug Form</h1>
        
        <h2>Create Room Form</h2>
        <form action="/debug_form" method="post">
            <input type="hidden" name="form_type" value="create_room">
            <div>
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" required>
            </div>
            <div>
                <label for="planet">Planet:</label>
                <input type="text" id="planet" name="planet" value="earth">
            </div>
            <button type="submit">Create Room</button>
        </form>
        
        <h2>Join Room Form</h2>
        <form action="/debug_form" method="post">
            <input type="hidden" name="form_type" value="join_room">
            <div>
                <label for="join_username">Username:</label>
                <input type="text" id="join_username" name="username" required>
            </div>
            <div>
                <label for="room_id">Room ID:</label>
                <input type="text" id="room_id" name="room_id" required>
            </div>
            <button type="submit">Join Room</button>
        </form>
    </body>
    </html>
    """

@app.route('/simple_test')
def simple_test():
    """
    A super simple test page with direct form submission
    """
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Simple Form Test</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            form { margin-bottom: 20px; padding: 20px; border: 1px solid #ccc; }
            input, button { margin: 5px; padding: 8px; }
            h2 { color: #333; }
        </style>
    </head>
    <body>
        <h1>Super Simple Form Test</h1>
        
        <h2>Create Room Form</h2>
        <form action="/create_room" method="post">
            <div>
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" value="TestUser" required>
            </div>
            <div>
                <label for="planet">Planet:</label>
                <input type="text" id="planet" name="planet" value="earth">
            </div>
            <button type="submit">Create Room</button>
        </form>
        
        <h2>Join Room Form</h2>
        <form action="/join_room" method="post">
            <div>
                <label for="join_username">Username:</label>
                <input type="text" id="join_username" name="username" value="TestUser" required>
            </div>
            <div>
                <label for="room_id">Room ID:</label>
                <input type="text" id="room_id" name="room_id" value="Earth-12345678" required>
            </div>
            <button type="submit">Join Room</button>
        </form>
    </body>
    </html>
    """

@app.route('/direct_links')
def direct_links():
    """
    A page with direct links to test the functionality
    """
    # Create a test room ID
    test_room_id = f"Earth-{str(uuid.uuid4())[:8]}"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Direct Links Test</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .link-box {{ margin-bottom: 20px; padding: 20px; border: 1px solid #ccc; }}
            a {{ display: block; margin: 10px 0; padding: 10px; background-color: #f0f0f0; text-decoration: none; color: #333; }}
            a:hover {{ background-color: #e0e0e0; }}
            h2 {{ color: #333; }}
        </style>
    </head>
    <body>
        <h1>Direct Links Test</h1>
        
        <div class="link-box">
            <h2>Test Pages</h2>
            <a href="/">Home Page</a>
            <a href="/test_form">Test Form</a>
            <a href="/simple_test">Simple Test</a>
            <a href="/debug_form">Debug Form</a>
        </div>
        
        <div class="link-box">
            <h2>Direct Links to Join Pages</h2>
            <a href="/join_new_chat_room/{test_room_id}?username=TestUser">Join New Chat Room (may not work without creating room first)</a>
            <a href="/chat_existing_room/{test_room_id}?username=TestUser">Chat Existing Room (may not work without creating room first)</a>
        </div>
        
        <div class="link-box">
            <h2>Create Room with Form</h2>
            <form action="/create_room" method="post">
                <input type="text" name="username" value="DirectLinkUser" required>
                <input type="hidden" name="planet" value="earth">
                <button type="submit">Create Room</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.route('/test_db')
def test_db():
    """
    Test route to check if the database is working
    """
    try:
        # Ensure database is initialized
        if not os.path.exists('chat.db'):
            init_db()
        
        # Test connection and query
        conn = get_db_connection()
        
        # Get room count
        room_count = conn.execute('SELECT COUNT(*) FROM chat_rooms').fetchone()[0]
        
        # Get user count
        user_count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        
        # Get a list of rooms
        rooms = conn.execute('SELECT id, name, category FROM chat_rooms LIMIT 10').fetchall()
        room_list = [dict(room) for room in rooms]
        
        conn.close()
        
        # Return database status
        return jsonify({
            'status': 'success',
            'database_exists': True,
            'room_count': room_count,
            'user_count': user_count,
            'recent_rooms': room_list
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'error_type': type(e).__name__
        }), 500

if __name__ == '__main__':
    socketio.run(app, debug=True)