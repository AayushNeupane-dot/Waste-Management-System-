#!/usr/bin/env python3
"""
Smart Waste Management System - Python Backend
Handles data logging, web server, and advanced analytics
"""

import serial
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit
import sqlite3
import matplotlib.pyplot as plt
import pandas as pd
from collections import deque
import os

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'smart_waste_2024'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables
current_data = {
    'fillLevel': 0,
    'temperature': 0,
    'humidity': 0,
    'moisture': 0,
    'tampered': False,
    'lidOpen': False,
    'personDetected': False,
    'timestamp': datetime.now().isoformat()
}

# Data storage for real-time charts
data_buffer = deque(maxlen=50)  # Store last 50 readings
arduino_serial = None

class WasteDataLogger:
    def __init__(self, db_name='waste_data.db'):
        self.db_name = db_name
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS waste_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                fill_level INTEGER,
                temperature REAL,
                humidity REAL,
                moisture INTEGER,
                tampered BOOLEAN,
                lid_open BOOLEAN,
                person_detected BOOLEAN
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                alert_type TEXT,
                message TEXT,
                resolved BOOLEAN DEFAULT FALSE
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def log_reading(self, data):
        """Log sensor reading to database"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO waste_readings 
            (fill_level, temperature, humidity, moisture, tampered, lid_open, person_detected)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['fillLevel'],
            data['temperature'],
            data['humidity'],
            data['moisture'],
            data['tampered'],
            data['lidOpen'],
            data['personDetected']
        ))
        
        conn.commit()
        conn.close()
    
    def log_alert(self, alert_type, message):
        """Log alert to database"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO alerts (alert_type, message)
            VALUES (?, ?)
        ''', (alert_type, message))
        
        conn.commit()
        conn.close()
    
    def get_recent_data(self, hours=24):
        """Get recent data for analytics"""
        conn = sqlite3.connect(self.db_name)
        
        df = pd.read_sql_query('''
            SELECT * FROM waste_readings 
            WHERE timestamp > datetime('now', '-{} hours')
            ORDER BY timestamp
        '''.format(hours), conn)
        
        conn.close()
        return df

class SerialReader:
    def __init__(self, port='COM3', baudrate=9600):  # Adjust port as needed
        self.port = port
        self.baudrate = baudrate
        self.serial_connection = None
        self.running = False
        self.data_logger = WasteDataLogger()
    
    def connect(self):
        """Connect to Arduino"""
        try:
            self.serial_connection = serial.Serial(self.port, self.baudrate, timeout=1)
            time.sleep(2)  # Wait for connection
            self.running = True
            print(f"Connected to Arduino on {self.port}")
            return True
        except Exception as e:
            print(f"Failed to connect to Arduino: {e}")
            return False
    
    def read_data(self):
        """Read data from Arduino"""
        global current_data, data_buffer
        
        while self.running:
            try:
                if self.serial_connection and self.serial_connection.in_waiting:
                    line = self.serial_connection.readline().decode('utf-8').strip()
                    
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            data = json.loads(line)
                            data['timestamp'] = datetime.now().isoformat()
                            
                            # Update current data
                            current_data.update(data)
                            
                            # Add to buffer for charts
                            data_buffer.append({
                                'timestamp': datetime.now(),
                                'fillLevel': data['fillLevel'],
                                'temperature': data['temperature'],
                                'humidity': data['humidity']
                            })
                            
                            # Log to database
                            self.data_logger.log_reading(data)
                            
                            # Check for alerts
                            self.check_alerts(data)
                            
                            # Emit to web clients
                            socketio.emit('sensor_update', current_data)
                            
                        except json.JSONDecodeError:
                            print(f"Invalid JSON: {line}")
                
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Error reading serial data: {e}")
                time.sleep(1)
    
    def check_alerts(self, data):
        """Check for alert conditions"""
        if data['fillLevel'] >= 85:
            self.data_logger.log_alert('FULL', f"Bin is {data['fillLevel']}% full")
            socketio.emit('alert', {
                'type': 'full',
                'message': f"Bin is {data['fillLevel']}% full - needs emptying!"
            })
        
        if data['tampered']:
            self.data_logger.log_alert('TAMPER', "Tampering detected")
            socketio.emit('alert', {
                'type': 'tamper',
                'message': "Tampering detected - security alert!"
            })
        
        if data['temperature'] > 35:  # High temperature alert
            self.data_logger.log_alert('TEMP', f"High temperature: {data['temperature']}°C")
            socketio.emit('alert', {
                'type': 'temperature',
                'message': f"High temperature detected: {data['temperature']}°C"
            })

# Initialize serial reader
serial_reader = SerialReader()

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/current')
def get_current_data():
    """API endpoint for current sensor data"""
    return jsonify(current_data)

@app.route('/api/history/<int:hours>')
def get_history(hours):
    """API endpoint for historical data"""
    try:
        data_logger = WasteDataLogger()
        df = data_logger.get_recent_data(hours)
        
        # Convert to JSON-friendly format
        history = []
        for _, row in df.iterrows():
            history.append({
                'timestamp': row['timestamp'],
                'fillLevel': row['fill_level'],
                'temperature': row['temperature'],
                'humidity': row['humidity'],
                'moisture': row['moisture']
            })
        
        return jsonify(history)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/analytics')
def get_analytics():
    """API endpoint for analytics data"""
    try:
        data_logger = WasteDataLogger()
        df = data_logger.get_recent_data(24)  # Last 24 hours
        
        if df.empty:
            return jsonify({'error': 'No data available'}), 404
        
        analytics = {
            'avg_fill_level': df['fill_level'].mean(),
            'max_fill_level': df['fill_level'].max(),
            'avg_temperature': df['temperature'].mean(),
            'avg_humidity': df['humidity'].mean(),
            'total_tamper_events': df['tampered'].sum(),
            'total_readings': len(df),
            'fill_trend': 'increasing' if df['fill_level'].iloc[-1] > df['fill_level'].iloc[0] else 'decreasing'
        }
        
        return jsonify(analytics)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print('Client connected')
    emit('sensor_update', current_data)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print('Client disconnected')

@socketio.on('request_data')
def handle_data_request():
    """Handle data request from client"""
    emit('sensor_update', current_data)

def generate_report():
    """Generate daily report"""
    try:
        data_logger = WasteDataLogger()
        df = data_logger.get_recent_data(24)
        
        if df.empty:
            return
        
        # Create visualizations
        plt.figure(figsize=(12, 8))
        
        # Fill level over time
        plt.subplot(2, 2, 1)
        plt.plot(pd.to_datetime(df['timestamp']), df['fill_level'])
        plt.title('Fill Level Over Time')
        plt.ylabel('Fill Level (%)')
        plt.xticks(rotation=45)
        
        # Temperature and humidity
        plt.subplot(2, 2, 2)
        plt.plot(pd.to_datetime(df['timestamp']), df['temperature'], label='Temperature')
        plt.plot(pd.to_datetime(df['timestamp']), df['humidity'], label='Humidity')
        plt.title('Environmental Conditions')
        plt.ylabel('Value')
        plt.legend()
        plt.xticks(rotation=45)
        
        # Moisture levels
        plt.subplot(2, 2, 3)
        plt.plot(pd.to_datetime(df['timestamp']), df['moisture'])
        plt.title('Moisture Levels')
        plt.ylabel('Moisture')
        plt.xticks(rotation=45)
        
        # Alert summary
        plt.subplot(2, 2, 4)
        alert_counts = [
            df['tampered'].sum(),
            (df['fill_level'] >= 85).sum(),
            (df['temperature'] > 35).sum()
        ]
        alert_labels = ['Tamper', 'Full', 'High Temp']
        plt.bar(alert_labels, alert_counts)
        plt.title('Alert Summary')
        plt.ylabel('Count')
        
        plt.tight_layout()
        plt.savefig(f'reports/daily_report_{datetime.now().strftime("%Y%m%d")}.png')
        plt.close()
        
        print(f"Report generated for {datetime.now().strftime('%Y-%m-%d')}")
        
    except Exception as e:
        print(f"Error generating report: {e}")

def start_background_tasks():
    """Start background tasks"""
    # Create reports directory
    os.makedirs('reports', exist_ok=True)
    
    # Connect to Arduino
    if serial_reader.connect():
        # Start serial reading in background thread
        serial_thread = threading.Thread(target=serial_reader.read_data)
        serial_thread.daemon = True
        serial_thread.start()
    else:
        print("Running without Arduino connection (simulation mode)")
        # Start simulation thread for demo purposes
        sim_thread = threading.Thread(target=simulate_data)
        sim_thread.daemon = True
        sim_thread.start()

def simulate_data():
    """Simulate data for demo purposes"""
    global current_data, data_buffer
    import random
    
    while True:
        # Simulate sensor readings
        current_data.update({
            'fillLevel': min(100, current_data['fillLevel'] + random.randint(-2, 3)),
            'temperature': 20 + random.uniform(-2, 8),
            'humidity': 45 + random.uniform(-10, 20),
            'moisture': random.randint(200, 800),
            'tampered': random.random() < 0.05,  # 5% chance
            'lidOpen': random.random() < 0.1,    # 10% chance
            'personDetected': random.random() < 0.15,  # 15% chance
            'timestamp': datetime.now().isoformat()
        })
        
        # Add to buffer
        data_buffer.append({
            'timestamp': datetime.now(),
            'fillLevel': current_data['fillLevel'],
            'temperature': current_data['temperature'],
            'humidity': current_data['humidity']
        })
        
        # Emit to clients
        socketio.emit('sensor_update', current_data)
        
        time.sleep(2)

if __name__ == '__main__':
    print("Starting Smart Waste Management System...")
    print("Python Backend Server")
    print("=" * 50)
    
    # Start background tasks
    start_background_tasks()
    
    # Run Flask app
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
    
    
    