import os
import re
import requests
import urllib.parse
import psycopg2
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from functools import wraps
import json

app = Flask(__name__)
CORS(app, supports_credentials=True)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://vofodb_user:Y7MQfAWwEtsiHQLiGHFV7ikOI2ruTv3u@dpg-d5lm4ongi27c7390kq40-a/vofodb')

# Simple in-memory cache for search results (for development)
search_cache = {}

# Initialize database connection
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

# Initialize database tables
def init_database():
    conn = get_db_connection()
    if not conn:
        print("Warning: Could not connect to database. Using in-memory storage.")
        return
    
    try:
        cur = conn.cursor()
        
        # Users table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                email VARCHAR(100),
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                preferences JSONB DEFAULT '{}'
            )
        ''')
        
        # Search history table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS search_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                query TEXT NOT NULL,
                result_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Play history table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS play_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                video_id VARCHAR(20) NOT NULL,
                title TEXT,
                artist TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        cur.close()
        conn.close()
        print("Database tables initialized successfully")
        
    except Exception as e:
        print(f"Database initialization error: {e}")

# YouTube search functions
def extract_video_id(url):
    """Extract YouTube video ID from URL"""
    patterns = [
        r'(?:youtube\.com\/watch\?v=)([^&]+)',
        r'(?:youtu\.be\/)([^?]+)',
        r'(?:youtube\.com\/embed\/)([^?]+)',
        r'(?:youtube\.com\/v\/)([^?]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

def search_youtube(query, max_results=10):
    """Search YouTube using web scraping approach"""
    try:
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.youtube.com/results?search_query={encoded_query}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=10)
        html_content = response.text
        
        videos = []
        
        # Look for video IDs in the HTML
        video_id_pattern = r'"videoId":"([^"]{11})"'
        video_ids = list(set(re.findall(video_id_pattern, html_content)))
        
        # Get titles
        title_pattern = r'"title":{"runs":\[{"text":"([^"]+)"'
        titles = re.findall(title_pattern, html_content)
        
        # Get channels
        channel_pattern = r'"ownerText":{"runs":\[{"text":"([^"]+)"'
        channels = re.findall(channel_pattern, html_content)
        
        # Get durations
        duration_pattern = r'"lengthText":{"simpleText":"([^"]+)"'
        durations = re.findall(duration_pattern, html_content)
        
        # Combine data
        for i, video_id in enumerate(video_ids[:max_results]):
            title = titles[i] if i < len(titles) else "Unknown Title"
            channel = channels[i] if i < len(channels) else "Unknown Artist"
            duration = durations[i] if i < len(durations) else "N/A"
            
            videos.append({
                'id': video_id,
                'title': title[:80],
                'artist': channel,
                'channel': channel,
                'duration': duration,
                'thumbnail': f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                'url': f'https://www.youtube.com/watch?v={video_id}'
            })
        
        return videos
    
    except Exception as e:
        print(f"Search error: {e}")
        return []

# Authentication decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Check for token in Authorization header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        # Fallback to session
        if not token and 'user_id' in session:
            token = session.get('token')
        
        if not token:
            return jsonify({'error': 'Token is missing'}), 401
        
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute('SELECT id, username, email FROM users WHERE id = %s', (data['user_id'],))
                user = cur.fetchone()
                cur.close()
                conn.close()
                
                if user:
                    current_user = {
                        'id': user[0],
                        'username': user[1],
                        'email': user[2]
                    }
                    return f(current_user, *args, **kwargs)
        except:
            return jsonify({'error': 'Token is invalid'}), 401
        
        return jsonify({'error': 'Token is invalid'}), 401
    return decorated

# Routes
@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/api/register', methods=['POST'])
def register():
    """Register a new user"""
    try:
        data = request.json
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'error': 'Username and password are required'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        password_hash = generate_password_hash(password)
        
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    'INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id',
                    (username, email, password_hash)
                )
                user_id = cur.fetchone()[0]
                conn.commit()
                cur.close()
                
                # Create token
                token = jwt.encode(
                    {'user_id': user_id, 'username': username},
                    app.config['SECRET_KEY'],
                    algorithm='HS256'
                )
                
                # Store in session
                session['user_id'] = user_id
                session['token'] = token
                session['username'] = username
                
                return jsonify({
                    'success': True,
                    'message': 'Registration successful',
                    'user': {
                        'id': user_id,
                        'username': username,
                        'email': email
                    },
                    'token': token
                })
                
            except psycopg2.IntegrityError:
                return jsonify({'error': 'Username already exists'}), 400
            finally:
                conn.close()
        else:
            # Fallback to in-memory storage if database is not available
            return jsonify({'error': 'Database connection failed'}), 500
    
    except Exception as e:
        print(f"Registration error: {e}")
        return jsonify({'error': 'Registration failed'}), 500

@app.route('/api/login', methods=['POST'])
def login():
    """Login user"""
    try:
        data = request.json
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'error': 'Username and password are required'}), 400
        
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute(
                'SELECT id, username, email, password_hash FROM users WHERE username = %s',
                (username,)
            )
            user = cur.fetchone()
            cur.close()
            conn.close()
            
            if user and check_password_hash(user[3], password):
                # Create token
                token = jwt.encode(
                    {'user_id': user[0], 'username': user[1]},
                    app.config['SECRET_KEY'],
                    algorithm='HS256'
                )
                
                # Store in session
                session['user_id'] = user[0]
                session['token'] = token
                session['username'] = user[1]
                
                return jsonify({
                    'success': True,
                    'message': 'Login successful',
                    'user': {
                        'id': user[0],
                        'username': user[1],
                        'email': user[2]
                    },
                    'token': token
                })
            else:
                return jsonify({'error': 'Invalid username or password'}), 401
        else:
            return jsonify({'error': 'Database connection failed'}), 500
    
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': 'Login failed'}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    """Logout user"""
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/check-auth', methods=['GET'])
def check_auth():
    """Check if user is authenticated"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    
    if not token and 'token' in session:
        token = session.get('token')
    
    if not token:
        return jsonify({'authenticated': False})
    
    try:
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute('SELECT id, username, email FROM users WHERE id = %s', (data['user_id'],))
            user = cur.fetchone()
            cur.close()
            conn.close()
            
            if user:
                return jsonify({
                    'authenticated': True,
                    'user': {
                        'id': user[0],
                        'username': user[1],
                        'email': user[2]
                    }
                })
    except:
        pass
    
    return jsonify({'authenticated': False})

@app.route('/api/search', methods=['GET'])
def search():
    """Search for YouTube videos"""
    try:
        query = request.args.get('q', '').strip()
        
        if not query:
            return jsonify([])
        
        print(f"Searching for: {query}")
        
        # Check cache first
        cache_key = query.lower()
        if cache_key in search_cache:
            print("Returning cached results")
            return jsonify(search_cache[cache_key])
        
        # Search YouTube
        videos = search_youtube(query, max_results=15)
        
        if not videos:
            # Try a fallback search
            print("No videos found with regex, trying fallback...")
            videos = [{
                'id': 'dQw4w9WgXcQ',  # Rick Astley - Never Gonna Give You Up
                'title': 'Rick Astley - Never Gonna Give You Up',
                'artist': 'Rick Astley',
                'channel': 'Rick Astley',
                'duration': '3:32',
                'thumbnail': 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg',
                'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
            }, {
                'id': 'kJQP7kiw5Fk',  # Luis Fonsi - Despacito
                'title': 'Luis Fonsi - Despacito ft. Daddy Yankee',
                'artist': 'Luis Fonsi',
                'channel': 'Luis Fonsi',
                'duration': '4:41',
                'thumbnail': 'https://i.ytimg.com/vi/kJQP7kiw5Fk/hqdefault.jpg',
                'url': 'https://www.youtube.com/watch?v=kJQP7kiw5Fk'
            }, {
                'id': '09R8_2nJtjg',  # Maroon 5 - Sugar
                'title': 'Maroon 5 - Sugar',
                'artist': 'Maroon 5',
                'channel': 'Maroon 5',
                'duration': '5:01',
                'thumbnail': 'https://i.ytimg.com/vi/09R8_2nJtjg/hqdefault.jpg',
                'url': 'https://www.youtube.com/watch?v=09R8_2nJtjg'
            }]
        
        # Cache the results
        search_cache[cache_key] = videos
        
        # Log search history if user is authenticated
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token and 'token' in session:
            token = session.get('token')
        
        if token:
            try:
                data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
                conn = get_db_connection()
                if conn:
                    cur = conn.cursor()
                    cur.execute(
                        'INSERT INTO search_history (user_id, query, result_count) VALUES (%s, %s, %s)',
                        (data['user_id'], query, len(videos))
                    )
                    conn.commit()
                    cur.close()
                    conn.close()
            except:
                pass
        
        print(f"Found {len(videos)} videos")
        return jsonify(videos)
    
    except Exception as e:
        print(f"Search endpoint error: {str(e)}")
        return jsonify([])

@app.route('/api/play', methods=['POST'])
@token_required
def play(current_user):
    """Get stream URL for a video and log play history"""
    try:
        data = request.json
        video_id = data.get('url', '').strip()
        
        if not video_id:
            return jsonify({'error': 'No video ID provided'}), 400
        
        # For YouTube, we'll use the embed URL
        # Note: This is for demo purposes. For actual audio streaming,
        # you would need to use yt-dlp to extract audio streams
        stream_url = f"https://www.youtube.com/embed/{video_id}?autoplay=1&controls=0&modestbranding=1&rel=0"
        
        # Get video info for logging
        videos = search_youtube(video_id, max_results=1)
        video_info = videos[0] if videos else {
            'title': 'Unknown Title',
            'artist': 'Unknown Artist'
        }
        
        # Log play history
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO play_history (user_id, video_id, title, artist) VALUES (%s, %s, %s, %s)',
                (current_user['id'], video_id, video_info['title'], video_info['artist'])
            )
            conn.commit()
            cur.close()
            conn.close()
        
        return jsonify({
            'stream_url': stream_url,
            'video_id': video_id,
            'success': True
        })
    
    except Exception as e:
        print(f"Play error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/profile', methods=['GET'])
@token_required
def profile(current_user):
    """Get user profile"""
    try:
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            
            # Get play count
            cur.execute('SELECT COUNT(*) FROM play_history WHERE user_id = %s', (current_user['id'],))
            play_count = cur.fetchone()[0]
            
            # Get search count
            cur.execute('SELECT COUNT(*) FROM search_history WHERE user_id = %s', (current_user['id'],))
            search_count = cur.fetchone()[0]
            
            # Get recent plays
            cur.execute('''
                SELECT video_id, title, artist, created_at 
                FROM play_history 
                WHERE user_id = %s 
                ORDER BY created_at DESC 
                LIMIT 10
            ''', (current_user['id'],))
            recent_plays = cur.fetchall()
            
            cur.close()
            conn.close()
            
            return jsonify({
                'user': current_user,
                'stats': {
                    'play_count': play_count,
                    'search_count': search_count
                },
                'recent_plays': [
                    {
                        'video_id': play[0],
                        'title': play[1],
                        'artist': play[2],
                        'played_at': play[3].isoformat() if play[3] else None
                    }
                    for play in recent_plays
                ]
            })
        else:
            return jsonify({'error': 'Database connection failed'}), 500
    
    except Exception as e:
        print(f"Profile error: {e}")
        return jsonify({'error': 'Failed to get profile'}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Initialize database on startup
    init_database()
    
    print("🚀 VoFo Music Server Starting...")
    print("📡 API Endpoints:")
    print("   GET  /                    - Serve frontend")
    print("   POST /api/register        - Register new user")
    print("   POST /api/login           - Login user")
    print("   POST /api/logout          - Logout user")
    print("   GET  /api/check-auth      - Check authentication")
    print("   GET  /api/search?q=       - Search for music")
    print("   POST /api/play            - Play a song")
    print("   GET  /api/profile         - Get user profile")
    print("\n🔗 Open http://localhost:5000 in your browser")
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
