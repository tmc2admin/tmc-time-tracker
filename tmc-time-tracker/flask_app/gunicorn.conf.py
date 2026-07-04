import os
import multiprocessing

# Read the port from the environment variable, defaulting to 8000
port = os.environ.get("PORT", "8000")

# Server socket
bind = f"0.0.0.0:{port}"

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "gevent"

# Logging
loglevel = "info"
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr