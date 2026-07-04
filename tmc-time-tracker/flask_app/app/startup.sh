#!/bin/bash

# Install dependencies
pip install -r requirements.txt

# Start the app using Gunicorn
gunicorn --bind=0.0.0.0:$PORT wsgi:application
