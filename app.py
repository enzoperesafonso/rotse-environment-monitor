from flask import Flask, render_template, jsonify
import influxdb_client
from influxdb_client.client.query_api import QueryApi
import json
from datetime import datetime, timedelta
import os
from threading import Thread
import time
import traceback

app = Flask(__name__)

# InfluxDB configuration - UPDATE THESE VALUES
INFLUXDB_URL = 'http://weather.local:8086'
INFLUXDB_TOKEN = 'n9Kn1y9G2Tsq85Xhkh-EP_-oBSs7GXDXx33SsrSshFYcIcfa7nTrv3oYAWF9Twz3MCfsL2rOomPtKv1iyJ_nKA=='  # Get from InfluxDB2 setup
INFLUXDB_ORG = 'rotse-test'
INFLUXDB_BUCKET = 'weather'

# AllSky image path - UPDATE THIS PATH
ALLSKY_IMAGE_PATH = "/home/pi/allsky/image.jpg"  # Change to your actual allsky image path

# Enable debug mode
DEBUG_MODE = False


def debug_print(message):
    if DEBUG_MODE:
        print(f"[DEBUG] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}")


# Initialize InfluxDB client with error handling
try:
    client = influxdb_client.InfluxDBClient(
        url=INFLUXDB_URL,
        token=INFLUXDB_TOKEN,
        org=INFLUXDB_ORG
    )
    query_api = client.query_api()
    debug_print("InfluxDB client initialized successfully")
except Exception as e:
    debug_print(f"Failed to initialize InfluxDB client: {e}")
    client = None
    query_api = None

# Global variable to store latest data
latest_data = {
    'temperature': None,
    'humidity': None,
    'pressure': None,
    'windspeed': None,
    'timestamp': None,
    'error': None
}


def test_influxdb_connection():
    """Test InfluxDB connection and list available buckets"""
    try:
        if not client:
            return False, "InfluxDB client not initialized"

        # Test connection by trying to list buckets
        buckets_api = client.buckets_api()
        buckets = buckets_api.find_buckets()

        bucket_names = [bucket.name for bucket in buckets.buckets] if buckets.buckets else []
        debug_print(f"Available buckets: {bucket_names}")

        if INFLUXDB_BUCKET not in bucket_names:
            return False, f"Bucket '{INFLUXDB_BUCKET}' not found. Available buckets: {bucket_names}"

        return True, "Connection successful"
    except Exception as e:
        return False, f"Connection failed: {e}"


def get_latest_weather():
    """Get the latest weather measurements from InfluxDB"""
    try:
        if not query_api:
            return {}

        # First, let's try a simple query to see what data exists
        debug_query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
        |> range(start: -24h)
        |> limit(n: 10)
        '''

        debug_print(f"Running debug query: {debug_query}")
        debug_result = query_api.query(debug_query)

        # Print what we find
        measurements = set()
        fields = set()
        for table in debug_result:
            for record in table.records:
                measurements.add(record.get_measurement())
                fields.add(record.get_field())

        debug_print(f"Found measurements: {measurements}")
        debug_print(f"Found fields: {fields}")

        # Now try the actual query
        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
        |> range(start: -1h)
        |> filter(fn: (r) => r["_measurement"] == "weather")
        |> filter(fn: (r) => r["_field"] == "temperature" or r["_field"] == "humidity" or r["_field"] == "pressure" or r["_field"] == "windspeed")
        |> last()
        '''

        debug_print(f"Running main query: {query}")
        result = query_api.query(query)
        data = {}

        for table in result:
            for record in table.records:
                field = record.get_field()
                value = record.get_value()
                timestamp = record.get_time()
                data[field] = {
                    'value': value,
                    'timestamp': timestamp
                }
                debug_print(f"Found data - {field}: {value} at {timestamp}")

        return data
    except Exception as e:
        debug_print(f"Error fetching latest weather: {e}")
        debug_print(f"Traceback: {traceback.format_exc()}")
        return {}


def get_historical_data(hours=24):
    """Get historical weather data for plotting"""
    try:
        if not query_api:
            return {}

        query = f'''
        from(bucket: "{INFLUXDB_BUCKET}")
        |> range(start: -{hours}h)
        |> filter(fn: (r) => r["_measurement"] == "weather")
        |> filter(fn: (r) => r["_field"] == "temperature" or r["_field"] == "humidity" or r["_field"] == "pressure" or r["_field"] == "windspeed")
        |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
        |> yield(name: "mean")
        '''

        debug_print(f"Running historical query for {hours} hours")
        result = query_api.query(query)
        data = {
            'temperature': [],
            'humidity': [],
            'pressure': [],
            'windspeed': [],
            'timestamps': []
        }

        # Collect all data points first
        all_records = []
        for table in result:
            for record in table.records:
                all_records.append({
                    'field': record.get_field(),
                    'value': record.get_value(),
                    'timestamp': record.get_time()
                })

        # Sort by timestamp
        all_records.sort(key=lambda x: x['timestamp'])

        # Group by timestamp
        timestamps_dict = {}
        for record in all_records:
            timestamp = record['timestamp']
            if timestamp not in timestamps_dict:
                timestamps_dict[timestamp] = {}
            timestamps_dict[timestamp][record['field']] = record['value']

        # Build final data structure
        for timestamp in sorted(timestamps_dict.keys()):
            data['timestamps'].append(timestamp.isoformat())
            record_data = timestamps_dict[timestamp]

            data['temperature'].append(record_data.get('temperature'))
            data['humidity'].append(record_data.get('humidity'))
            data['pressure'].append(record_data.get('pressure'))
            data['windspeed'].append(record_data.get('windspeed'))

        debug_print(
            f"Historical data points - Temperature: {len(data['temperature'])}, Humidity: {len(data['humidity'])}, Pressure: {len(data['pressure'])}, Wind: {len(data['windspeed'])}")
        debug_print(f"Sample timestamps: {data['timestamps'][:3] if data['timestamps'] else 'None'}")
        return data
    except Exception as e:
        debug_print(f"Error fetching historical data: {e}")
        debug_print(f"Traceback: {traceback.format_exc()}")
        return {}


def update_latest_data():
    """Background thread to update latest data every 30 seconds"""
    global latest_data

    # Test connection first
    conn_success, conn_message = test_influxdb_connection()
    debug_print(f"Connection test: {conn_message}")

    while True:
        try:
            data = get_latest_weather()
            if data:
                latest_data = {
                    'temperature': data.get('temperature', {}).get('value'),
                    'humidity': data.get('humidity', {}).get('value'),
                    'pressure': data.get('pressure', {}).get('value'),
                    'windspeed': data.get('windspeed', {}).get('value'),
                    'timestamp': data.get('temperature', {}).get('timestamp'),
                    'error': None
                }
                debug_print(f"Updated latest data: {latest_data}")
            else:
                latest_data['error'] = "No data returned from InfluxDB"
                debug_print("No data returned from InfluxDB")

        except Exception as e:
            latest_data['error'] = str(e)
            debug_print(f"Error updating latest data: {e}")

        time.sleep(30)  # Update every 30 seconds


@app.route('/')
def dashboard():
    """Main dashboard page"""
    return render_template('dashboard.html')


@app.route('/api/latest')
def api_latest():
    """API endpoint for latest weather data"""
    debug_print(f"API request for latest data: {latest_data}")

    # Convert datetime to string for JSON serialization
    data_for_json = latest_data.copy()
    if data_for_json['timestamp']:
        data_for_json['timestamp'] = data_for_json['timestamp'].isoformat()

    debug_print(f"Sending JSON data: {data_for_json}")
    return jsonify(data_for_json)


@app.route('/api/historical/<int:hours>')
def api_historical(hours):
    """API endpoint for historical weather data"""
    if hours > 168:  # Limit to 1 week
        hours = 168

    data = get_historical_data(hours)
    debug_print(f"API request for {hours}h historical data - returned {len(data.get('timestamps', []))} points")
    return jsonify(data)


@app.route('/api/debug')
def api_debug():
    """Debug endpoint to check system status"""
    conn_success, conn_message = test_influxdb_connection()

    debug_info = {
        'influxdb_url': INFLUXDB_URL,
        'influxdb_org': INFLUXDB_ORG,
        'influxdb_bucket': INFLUXDB_BUCKET,
        'connection_status': conn_message,
        'allsky_path': ALLSKY_IMAGE_PATH,
        'allsky_exists': os.path.exists(ALLSKY_IMAGE_PATH),
        'latest_data': latest_data,
        'current_time': datetime.now().isoformat()
    }

    return jsonify(debug_info)


@app.route('/allsky')
def allsky():
    """Serve the allsky camera image"""
    try:
        if os.path.exists(ALLSKY_IMAGE_PATH):
            debug_print(f"Serving allsky image from: {ALLSKY_IMAGE_PATH}")
            return app.send_file(ALLSKY_IMAGE_PATH, mimetype='image/jpeg')
        else:
            debug_print(f"Allsky image not found at: {ALLSKY_IMAGE_PATH}")
            # Create a simple SVG placeholder
            placeholder_svg = '''<svg width="400" height="400" xmlns="http://www.w3.org/2000/svg">
                <rect width="400" height="400" fill="#1a1a2e"/>
                <circle cx="200" cy="200" r="150" fill="none" stroke="#3498db" stroke-width="2"/>
                <text x="200" y="190" text-anchor="middle" fill="#3498db" font-family="Arial" font-size="16">All-Sky Camera</text>
                <text x="200" y="210" text-anchor="middle" fill="#95a5a6" font-family="Arial" font-size="12">Image not found</text>
                <text x="200" y="230" text-anchor="middle" fill="#95a5a6" font-family="Arial" font-size="10">{}</text>
            </svg>'''.format(ALLSKY_IMAGE_PATH)

            return placeholder_svg, 200, {'Content-Type': 'image/svg+xml'}
    except Exception as e:
        debug_print(f"Error serving allsky image: {e}")
        return f"Error loading allsky image: {e}", 500


@app.route('/test')
def test_page():
    """Test page with debug information"""
    conn_success, conn_message = test_influxdb_connection()

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Observatory Dashboard Debug</title></head>
    <body>
        <h1>Debug Information</h1>
        <h2>InfluxDB Configuration:</h2>
        <ul>
            <li>URL: {INFLUXDB_URL}</li>
            <li>Organization: {INFLUXDB_ORG}</li>
            <li>Bucket: {INFLUXDB_BUCKET}</li>
            <li>Connection Status: {conn_message}</li>
        </ul>

        <h2>AllSky Camera:</h2>
        <ul>
            <li>Path: {ALLSKY_IMAGE_PATH}</li>
            <li>Exists: {os.path.exists(ALLSKY_IMAGE_PATH)}</li>
        </ul>

        <h2>Latest Data:</h2>
        <pre>{json.dumps(latest_data, indent=2, default=str)}</pre>

        <h2>API Endpoints:</h2>
        <ul>
            <li><a href="/api/latest">Latest Data</a></li>
            <li><a href="/api/historical/6">Historical Data (6h)</a></li>
            <li><a href="/api/debug">Debug Info</a></li>
        </ul>
    </body>
    </html>
    """
    return html


if __name__ == '__main__':
    # Start background thread for data updates
    update_thread = Thread(target=update_latest_data, daemon=True)
    update_thread.start()

    # Create static directory if it doesn't exist
    if not os.path.exists('static'):
        os.makedirs('static')

    # Create templates directory if it doesn't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')

    print("=== Observatory Weather Dashboard Debug Mode ===")
    print(f"Dashboard URL: http://localhost:5000")
    print(f"Debug page URL: http://localhost:5000/test")
    print(f"Debug API URL: http://localhost:5000/api/debug")
    print("================================================")

    app.run(host='0.0.0.0', port=5050, debug=True)