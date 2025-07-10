#!/bin/bash

# Weather Station Collector Installation Script
# Run as root or with sudo

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
SERVICE_NAME="rotse-weatherd"
SERVICE_USER="weather"
SERVICE_GROUP="weather"
INSTALL_DIR="/opt/rotse-weatherd"
CONFIG_DIR="/etc/rotse-weatherd"
LOG_DIR="/var/log"
DATA_DIR="/var/lib/rotse-weatherd"

echo -e "${GREEN} ROTSE Weather Station Daemon Installation${NC}"
echo "========================================"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root${NC}"
   exit 1
fi

# Check if Python 3.7+ is available
echo -e "${YELLOW}Checking Python version...${NC}"
if ! python3 --version | grep -qE "Python 3\.[7-9]|Python 3\.[1-9][0-9]"; then
    echo -e "${RED}Python 3.7 or higher is required${NC}"
    exit 1
fi

# Check if pip is available
if ! command -v pip3 &> /dev/null; then
    echo -e "${YELLOW}Installing pip...${NC}"
    if command -v apt-get &> /dev/null; then
        apt-get update
        apt-get install -y python3-pip
    elif command -v yum &> /dev/null; then
        yum install -y python3-pip
    elif command -v dnf &> /dev/null; then
        dnf install -y python3-pip
    else
        echo -e "${RED}Could not install pip. Please install it manually.${NC}"
        exit 1
    fi
fi

# Create service user
echo -e "${YELLOW}Creating service user...${NC}"
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/false "$SERVICE_USER"
    echo -e "${GREEN}Created user: $SERVICE_USER${NC}"
else
    echo -e "${GREEN}User $SERVICE_USER already exists${NC}"
fi

# Create directories
echo -e "${YELLOW}Creating directories...${NC}"
mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_DIR"

# Set permissions
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR"
chmod 755 "$CONFIG_DIR"

# Install Python package
echo -e "${YELLOW}Installing Python package...${NC}"
pip3 install -e .

# Copy configuration files
echo -e "${YELLOW}Installing configuration files...${NC}"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cp config/config.yaml "$CONFIG_DIR/"
    echo -e "${GREEN}Installed default configuration${NC}"
else
    echo -e "${YELLOW}Configuration file already exists, skipping${NC}"
fi

# Install systemd service
echo -e "${YELLOW}Installing systemd service...${NC}"
cp systemd/weather-collector.service /etc/systemd/system/
systemctl daemon-reload

# Add user to dialout group for serial port access
echo -e "${YELLOW}Adding $SERVICE_USER to dialout group...${NC}"
usermod -a -G dialout "$SERVICE_USER"

# Create log file with proper permissions
touch "$LOG_DIR/weather-collector.log"
chown "$SERVICE_USER:$SERVICE_GROUP" "$LOG_DIR/weather-collector.log"

echo -e "${GREEN}Installation completed successfully!${NC}"
echo ""
echo "Next steps:"
echo "1. Edit the configuration file: $CONFIG_DIR/config.yaml"
echo "2. Update your InfluxDB token and other settings"
echo "3. Test the configuration: weather-collector --validate-config --config $CONFIG_DIR/config.yaml"
echo "4. Enable and start the service:"
echo "   systemctl enable $SERVICE_NAME"
echo "   systemctl start $SERVICE_NAME"
echo "5. Check service status: systemctl status $SERVICE_NAME"
echo "6. View logs: journalctl -u $SERVICE_NAME -f"
echo ""
echo -e "${YELLOW}Configuration file location: $CONFIG_DIR/config.yaml${NC}"
echo -e "${YELLOW}Log file location: $LOG_DIR/weather-collector.log${NC}"