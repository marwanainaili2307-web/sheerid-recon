#!/usr/bin/env python3
"""
SheerID Verification Automation using browser-use API v4 runs
Runs the agent directly on browser-use cloud (no local Playwright needed)
"""

import os
import json
import time
from browser_use_sdk.v4 import BrowserUse


def run_sheerid_verification():
    api_key = os.environ.get('BROWSER_USE_API_KEY')
    captcha_key = os.environ.get('CAPTCHA_API_KEY')
    
    if not api_key:
        print("BROWSER_USE_API_KEY not set")
        return
    
    client = BrowserUse(api_key=api_key)
    
    # Build the task
    task = f"""
Go to https://services.sheerid.com/verify/67c8c14f5f17a83b745e3f82/?verificationId=<TEST_VID_REDACTED>&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2

Complete the student verification form:
1. First name: Mohamed
2. Last name: Benali  
3. Email: mohamed.benali@example.com
4. Country: Algeria (select from dropdown)
5. Date of birth: June 15, 2000 (select month from dropdown, type day/year)
6. School: University of Science and Technology Houari Boumediene (type and select from autocomplete)
7. Solve the Turnstile CAPTCHA (use 2Captcha if needed)

Click "Verify student status" button and wait for the result.
Return the final URL and verification status.
"""
    
    print("Creating browser-use run...")
    run = client.runs.create(
        task=task,
        # Optional: configure browser
        # browser_settings={
        #     "viewport": {"width": 390, "height": 844},
        #     "user_agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36"
        # }
    )
    
    print(f"Run created: {run.id}")
    print(f"Live URL: {run.live_url}")
    print("Waiting for completion...")
    
    # Wait for completion
    result = client.runs.wait_for_completion(run.id, timeout=300)
    
    print("\n=== RESULT ===")
    print(f"Status: {result.status}")
    print(f"Result: {result.result}")
    print(f"Error: {result.error}")
    print(f"Recording: {result.recording_url}")
    
    return result


if __name__ == "__main__":
    run_sheerid_verification()