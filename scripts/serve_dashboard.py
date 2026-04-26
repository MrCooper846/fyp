#!/usr/bin/env python3
"""
Simple web server to view benchmark contacts dashboard.

Usage:
    python serve_dashboard.py
    
Then open: http://localhost:8000/view_benchmark_contacts.html
"""

import http.server
import socketserver
import webbrowser
import os
from pathlib import Path

PORT = 8000

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler to serve files and enable directory listing."""
    
    def end_headers(self):
        # Enable CORS for local development
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        super().end_headers()


def serve():
    # Change to the project directory
    project_dir = Path(__file__).parent
    os.chdir(project_dir)
    
    Handler = MyHTTPRequestHandler
    
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        url = f"http://localhost:{PORT}/view_benchmark_contacts.html"
        print(f"╔═══════════════════════════════════════════════════════════════╗")
        print(f"║  🌐 Benchmark Contacts Dashboard Server                      ║")
        print(f"╚═══════════════════════════════════════════════════════════════╝")
        print(f"\n✓ Server running at: {url}")
        print(f"✓ Press Ctrl+C to stop the server\n")
        print(f"Opening dashboard in your browser...")
        
        # Open browser automatically
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"Could not open browser automatically: {e}")
            print(f"Please manually navigate to: {url}")
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n✓ Server stopped")


if __name__ == "__main__":
    serve()
