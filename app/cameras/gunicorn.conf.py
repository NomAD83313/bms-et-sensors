import os


bind = f"0.0.0.0:{os.getenv('CAMERAS_PORT', '3090')}"
workers = 1
threads = 8
timeout = 120
