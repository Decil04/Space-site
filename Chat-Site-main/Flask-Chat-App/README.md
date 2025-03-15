# Space Military Command Center Chat

A real-time chat application with a space military theme, built with Flask and Socket.IO.

## Features

### User Management
- Custom usernames and profiles
- User avatars and status messages
- Online/offline status indicators
- User roles (Major/Soldier)
- User banning system

### Chat Rooms
- Create Earth and Mars bases
- Custom room IDs and member limits
- Room ownership and management
- Room decommissioning with animations
- Room announcements and pinned messages

### Messages
- Real-time message delivery
- Message editing and deletion
- File attachments (images, documents)
- Message reactions (👍, ❤️, 🚀)
- Typing indicators
- Message history

### Administrative Features
- User moderation tools
- Chat log export
- Room activity monitoring
- User ban management

## Setup Instructions

1. Clone the repository:
```bash
git clone https://github.com/yourusername/space-military-chat.git
cd space-military-chat
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
# Create a .env file with the following variables
FLASK_APP=app.py
FLASK_ENV=development
SECRET_KEY=your-secret-key
DATABASE_URL=sqlite:///chat.db
```

5. Initialize the database:
```bash
flask db upgrade
```

6. Run the application:
```bash
python app.py
```

The application will be available at `http://localhost:5000`.

## Configuration

The application can be configured using environment variables or the `config.py` file:

- `FLASK_ENV`: Set to 'development' or 'production'
- `SECRET_KEY`: Secret key for session management
- `DATABASE_URL`: Database connection URL
- `MAX_ROOM_MEMBERS`: Maximum number of users per room (default: 50)
- `MESSAGE_HISTORY_LIMIT`: Number of messages to keep in history (default: 100)
- `MAX_CONTENT_LENGTH`: Maximum file upload size (default: 16MB)

## File Upload Support

Supported file types:
- Images: PNG, JPG, GIF
- Documents: PDF, TXT

Maximum file size: 16MB

## Security Features

- Secure session management
- CSRF protection
- XSS prevention
- Rate limiting
- Input validation
- File upload restrictions

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 