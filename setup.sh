#!/usr/bin/env bash
set -e

echo "==================================================="
echo "Setting up Notebook Intelligence System (NIS)..."
echo "==================================================="

python3 -m venv venv
source venv/bin/activate

echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

if [ ! -f .env ]; then
    echo "Copying .env.example to .env..."
    cp .env.example .env
fi

echo ""
echo "==================================================="
echo "Setup complete! To start the application:"
echo "Run: ./run.sh"
echo "==================================================="
