#!/bin/bash
# Weather Station Daemon Installation Script
# Run with sudo on Raspberry Pi

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_USER="weather"
DAEMON_GROUP="weather"
CONFIG_DIR="/etc/weather-station"
LOG_DIR="/var/log/weather-station"
INSTALL_DIR="/opt/weather-station"
DAEMON_SCRIPT="$INSTALL_DIR/weather_daemon.py"
SERVICE_FILE="/etc/systemd/system/weather-station.service"

echo "Weather Station Daemon Installation"
echo "==================================="

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)"
   exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv python3-serial python3-full

# Try to install via apt first (preferred method)
echo "Installing Python packages via apt..."
apt-get install -y python3-serial python3-requests python3-urllib3 python3-dateutil python3-setuptools

# Check if influxdb-client is available via apt, if not use virtual environment
echo "Setting up Python environment..."
if ! python3 -c "import influxdb_client" 2>/dev/null; then
    echo "InfluxDB client not available via apt, creating virtual environment..."
    NEED_VENV=true
else
    echo "Using system packages"
    NEED_VENV=false
fi

# Create dedicated user for the service
if ! id "$DAEMON_USER" &>/dev/null; then
    echo "Creating user $DAEMON_USER..."
    useradd -r -s /bin/false -d /var/lib/weather-station -m $DAEMON_USER
else
    echo "User $DAEMON_USER already exists"
fi

# Add user to dialout group for serial access
usermod -a -G dialout $DAEMON_USER

# Create directories
echo "Creating directories..."
mkdir -p "$CONFIG_DIR"
mkdir -p "$LOG_DIR"
mkdir -p "$INSTALL_DIR"

# Set up virtual environment if needed
if [ "$NEED_VENV" = true ]; then
    echo "Creating virtual environment for InfluxDB client..."
    python3 -m venv "$INSTALL_DIR/venv"
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install influxdb-client
    PYTHON_EXEC="$INSTALL_DIR/venv/bin/python3"
else
    PYTHON_EXEC="/usr/bin/python3"
fi

# Copy daemon script
echo "Installing daemon script..."
cp "$SCRIPT_DIR/weather_daemon.py" "$DAEMON_SCRIPT"
chmod +x "$DAEMON_SCRIPT"

# Create systemd service file
echo "Installing systemd service..."
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Weather Station Data Collector
Documentation=Weather station serial data collector for InfluxDB
After=network-online.target
Wants=network-online.target
Requires=network.target

[Service]
Type=simple
User=weather
Group=weather
ExecStart=$PYTHON_EXEC /opt/weather-station/weather_daemon.py
Restart=always
RestartSec=10
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=30
WorkingDirectory=/opt/weather-station

# Security settings
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/log/weather-station /etc/weather-station
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictRealtime=yes
RestrictNamespaces=yes

# Allow access to serial devices
SupplementaryGroups=dialout

# Resource limits
MemoryMax=256M
TasksMax=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=weather-station

[Install]
WantedBy=multi-user.target
EOF

# Set ownership and permissions
echo "Setting up permissions..."
chown -R $DAEMON_USER:$DAEMON_GROUP "$CONFIG_DIR"
chown -R $DAEMON_USER:$DAEMON_GROUP "$LOG_DIR"
chown -R $DAEMON_USER:$DAEMON_GROUP "$INSTALL_DIR"
chown root:root "$SERVICE_FILE"

# Set permissions
chmod 755 "$CONFIG_DIR"
chmod 755 "$LOG_DIR"
chmod 755 "$INSTALL_DIR"
chmod 644 "$SERVICE_FILE"
chmod 755 "$DAEMON_SCRIPT"

# Detect serial port
echo "Detecting serial devices..."
SERIAL_DEVICES=$(ls /dev/ttyUSB* /dev/ttyACM* /dev/ttyAMA* 2>/dev/null || true)
if [ -n "$SERIAL_DEVICES" ]; then
    echo "Found serial devices:"
    for device in $SERIAL_DEVICES; do
        echo "  $device"
    done
    FIRST_DEVICE=$(echo $SERIAL_DEVICES | cut -d' ' -f1)
    echo "Using $FIRST_DEVICE as default serial port"
else
    echo "No serial devices found. You may need to configure manually."
    FIRST_DEVICE="/dev/ttyUSB0"
fi

# Create initial config if it doesn't exist
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    echo "Creating initial configuration..."
    cat > "$CONFIG_DIR/config.json" << EOF
{
    "serial": {
        "port": "$FIRST_DEVICE",
        "baudrate": 9600,
        "timeout": 5
    },
    "influxdb": {
        "url": "http://weather.local:8086",
        "token": "YOUR_INFLUXDB_TOKEN_HERE",
        "org": "rotse-test",
        "bucket": "weather"
    },
    "logging": {
        "level": "INFO",
        "file": "/var/log/weather-station/weather.log",
        "max_bytes": 10485760,
        "backup_count": 5
    },
    "collection": {
        "retry_delay": 5,
        "loop_delay": 0.1
    }
}
EOF
    chown $DAEMON_USER:$DAEMON_GROUP "$CONFIG_DIR/config.json"
    chmod 644 "$CONFIG_DIR/config.json"
fi

# Install control script
if [ -f "$SCRIPT_DIR/weather-station-ctl.sh" ]; then
    cp "$SCRIPT_DIR/weather-station-ctl.sh" /usr/local/bin/weather-station-ctl
    chmod +x /usr/local/bin/weather-station-ctl
    echo "Control script installed as: weather-station-ctl"
fi

# Enable service
echo "Configuring systemd service..."
systemctl daemon-reload
systemctl enable weather-station.service

# Test Python imports
echo "Testing Python dependencies..."
$PYTHON_EXEC -c "import serial; import influxdb_client; print('✓ Python dependencies OK')" || {
    echo "❌ Python dependency test failed"
    echo "Trying alternative installation method..."

    if [ "$NEED_VENV" = false ]; then
        echo "Creating virtual environment as fallback..."
        python3 -m venv "$INSTALL_DIR/venv"
        "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
        "$INSTALL_DIR/venv/bin/pip" install pyserial influxdb-client

        # Update service file to use venv
        PYTHON_EXEC="$INSTALL_DIR/venv/bin/python3"
        sed -i "s|ExecStart=.*|ExecStart=$PYTHON_EXEC /opt/weather-station/weather_daemon.py|" "$SERVICE_FILE"

        # Test again
        $PYTHON_EXEC -c "import serial; import influxdb_client; print('✓ Python dependencies OK with venv')" || {
            echo "❌ Still failed. You may need to install manually."
            exit 1
        }
    else
        exit 1
    fi
}

echo ""
echo "✅ Installation completed successfully!"
echo ""
echo "Next steps:"
echo "1. Edit the configuration file: sudo nano $CONFIG_DIR/config.json"
echo "2. Update your InfluxDB token and other settings as needed"
echo "3. Start the service: sudo systemctl start weather-station"
echo "4. Check status: sudo systemctl status weather-station"
echo "5. View logs: sudo journalctl -u weather-station -f"
echo ""
echo "Management commands (if control script was installed):"
echo "  weather-station-ctl start|stop|restart|status|logs|config|test"
echo ""
echo "The service will automatically start on boot."
echo ""
echo "Configuration file: $CONFIG_DIR/config.json"
echo "Log files: $LOG_DIR/"
echo "Installation directory: $INSTALL_DIR"
echo ""
echo "To uninstall:"
echo "  sudo systemctl stop weather-station"
echo "  sudo systemctl disable weather-station"
echo "  sudo rm -f $SERVICE_FILE"
echo "  sudo rm -rf $INSTALL_DIR $CONFIG_DIR $LOG_DIR"
echo "  sudo deluser $DAEMON_USER"