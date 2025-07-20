#!/bin/bash
# Weather Station Daemon Control Script

SERVICE_NAME="weather-station"
CONFIG_FILE="/etc/weather-station/config.json"
LOG_FILE="/var/log/weather-station/weather.log"

show_usage() {
    echo "Weather Station Daemon Control"
    echo "Usage: $0 {start|stop|restart|status|logs|config|test}"
    echo ""
    echo "Commands:"
    echo "  start    - Start the weather station service"
    echo "  stop     - Stop the weather station service"
    echo "  restart  - Restart the weather station service"
    echo "  status   - Show service status"
    echo "  logs     - Show recent logs (follow with -f for live)"
    echo "  config   - Edit configuration file"
    echo "  test     - Test configuration and connections"
    echo "  enable   - Enable service to start on boot"
    echo "  disable  - Disable service from starting on boot"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "This command requires root privileges (use sudo)"
        exit 1
    fi
}

case "$1" in
    start)
        check_root
        echo "Starting weather station service..."
        systemctl start $SERVICE_NAME
        systemctl status $SERVICE_NAME --no-pager -l
        ;;
    stop)
        check_root
        echo "Stopping weather station service..."
        systemctl stop $SERVICE_NAME
        systemctl status $SERVICE_NAME --no-pager -l
        ;;
    restart)
        check_root
        echo "Restarting weather station service..."
        systemctl restart $SERVICE_NAME
        systemctl status $SERVICE_NAME --no-pager -l
        ;;
    status)
        echo "Weather station service status:"
        systemctl status $SERVICE_NAME --no-pager -l
        ;;
    logs)
        if [ "$2" = "-f" ]; then
            echo "Following weather station logs (Ctrl+C to exit):"
            journalctl -u $SERVICE_NAME -f
        else
            echo "Recent weather station logs:"
            journalctl -u $SERVICE_NAME -n 50 --no-pager
        fi
        ;;
    config)
        check_root
        if command -v nano &> /dev/null; then
            nano "$CONFIG_FILE"
        elif command -v vi &> /dev/null; then
            vi "$CONFIG_FILE"
        else
            echo "No suitable editor found. Please edit $CONFIG_FILE manually."
        fi
        ;;
    test)
        echo "Testing weather station configuration..."

        # Check if config file exists and is readable
        if [ ! -r "$CONFIG_FILE" ]; then
            echo "ERROR: Configuration file not found or not readable: $CONFIG_FILE"
            exit 1
        fi

        echo "✓ Configuration file exists: $CONFIG_FILE"

        # Validate JSON
        if command -v python3 &> /dev/null; then
            if python3 -m json.tool "$CONFIG_FILE" > /dev/null 2>&1; then
                echo "✓ Configuration file is valid JSON"
            else
                echo "✗ Configuration file contains invalid JSON"
                exit 1
            fi
        fi

        # Check serial port
        SERIAL_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['serial']['port'])" 2>/dev/null)
        if [ -n "$SERIAL_PORT" ]; then
            if [ -e "$SERIAL_PORT" ]; then
                echo "✓ Serial port exists: $SERIAL_PORT"
                if [ -r "$SERIAL_PORT" ] && [ -w "$SERIAL_PORT" ]; then
                    echo "✓ Serial port is accessible"
                else
                    echo "⚠ Serial port permissions may need adjustment"
                fi
            else
                echo "✗ Serial port not found: $SERIAL_PORT"
            fi
        fi

        # Check log directory
        if [ -d "/var/log/weather-station" ]; then
            echo "✓ Log directory exists"
        else
            echo "✗ Log directory missing"
        fi

        # Test network connectivity to InfluxDB
        INFLUX_URL=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['influxdb']['url'])" 2>/dev/null)
        if [ -n "$INFLUX_URL" ]; then
            if command -v curl &> /dev/null; then
                if curl -s --connect-timeout 5 "$INFLUX_URL/health" > /dev/null; then
                    echo "✓ InfluxDB server is reachable"
                else
                    echo "⚠ InfluxDB server may not be reachable: $INFLUX_URL"
                fi
            else
                echo "⚠ Cannot test InfluxDB connectivity (curl not available)"
            fi
        fi

        echo "Configuration test completed."
        ;;
    enable)
        check_root
        echo "Enabling weather station service to start on boot..."
        systemctl enable $SERVICE_NAME
        echo "Service enabled."
        ;;
    disable)
        check_root
        echo "Disabling weather station service from starting on boot..."
        systemctl disable $SERVICE_NAME
        echo "Service disabled."
        ;;
    *)
        show_usage
        exit 1
        ;;
esac