#!/usr/bin/env python3
"""
SheerID Automation Runner
Uses the built-in browser_use tool for automation
"""

import json
import sys
import os

# This script is designed to be run via the browser_use tool in the agent environment
# It generates the sequence of browser automation steps

def generate_automation_steps(student_data, verification_id, captcha_key=None):
    """Generate the browser automation steps"""
    
    steps = [
        {
            "action": "navigate",
            "url": f"https://services.sheerid.com/verify/67c8c14f5f17a83b745e3f82/?verificationId={verification_id}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2",
            "description": "Load verification page"
        },
        {
            "action": "wait_for_selector",
            "selector": 'iframe[src*="challenges.cloudflare.com"]',
            "timeout": 30000,
            "description": "Wait for Turnstile"
        },
        {
            "action": "execute_js",
            "script": """
            () => {
                if (window.turnstile && window.turnstile._private) {
                    for (const [key, val] of Object.entries(window.turnstile._private)) {
                        if (val.params?.sitekey) return val.params.sitekey;
                    }
                }
                return null;
            }
            """,
            "description": "Extract Turnstile sitekey"
        }
    ]
    
    # CAPTCHA solving (requires 2Captcha)
    if captcha_key:
        steps.append({
            "action": "solve_captcha_2captcha",
            "sitekey": "<EXTRACTED_FROM_PREVIOUS>",
            "pageurl": f"https://services.sheerid.com/verify/67c8c14f5f17a83b745e3f82/?verificationId={verification_id}&redirectUrl=https%3A%2F%2Fone.google.com%2Fai-student&authuser=2",
            "api_key": captcha_key,
            "description": "Solve Turnstile via 2Captcha"
        })
        steps.append({
            "action": "execute_js",
            "script": """
            (token) => {
                if (window.turnstile && window.turnstile._private) {
                    for (const [key, val] of Object.entries(window.turnstile._private)) {
                        if (val.widgetId) {
                            // Token auto-picked up
                        }
                    }
                }
                const hiddenInput = document.querySelector('input[name="cf-turnstile-response"]');
                if (hiddenInput) hiddenInput.value = token;
            }
            """,
            "description": "Inject Turnstile token"
        })
    else:
        steps.append({
            "action": "wait",
            "seconds": 60,
            "description": "Manual CAPTCHA solve time"
        })
    
    # Fill form fields
    steps.extend([
        {
            "action": "click",
            "selector": '#sid-first-name',
            "description": "Focus first name"
        },
        {
            "action": "type",
            "selector": '#sid-first-name',
            "text": student_data["first_name"],
            "description": "Enter first name"
        },
        {
            "action": "click",
            "selector": '#sid-last-name',
            "description": "Focus last name"
        },
        {
            "action": "type",
            "selector": '#sid-last-name',
            "text": student_data["last_name"],
            "description": "Enter last name"
        },
        {
            "action": "click",
            "selector": '#sid-email',
            "description": "Focus email"
        },
        {
            "action": "type",
            "selector": '#sid-email',
            "text": student_data["email"],
            "description": "Enter email"
        },
        {
            "action": "click",
            "selector": '.sid-field.sid-country .sid-input-select-button',
            "description": "Open country dropdown"
        },
        {
            "action": "wait_for_selector",
            "selector": '[role="option"]',
            "timeout": 5000
        },
        {
            "action": "execute_js",
            "script": f"""
            () => {{
                const options = document.querySelectorAll('[role="option"]');
                for (let i = 0; i < options.length; i++) {{
                    if (options[i].textContent.includes('{student_data["country_name"]}')) {{
                        options[i].click();
                        return options[i].textContent;
                    }}
                }}
                return 'not found';
            }}
            """,
            "description": f"Select country: {student_data['country_name']}"
        },
        {
            "action": "click",
            "selector": '.sid-birthdate__month .sid-input-select-button',
            "description": "Open month dropdown"
        },
        {
            "action": "wait_for_selector",
            "selector": '[role="option"]',
            "timeout": 5000
        },
        {
            "action": "execute_js",
            "script": f"""
            () => {{
                const options = document.querySelectorAll('[role="option"]');
                if (options.length > {student_data["birth_month"] - 1}) {{
                    options[{student_data["birth_month"] - 1}].click();
                    return options[{student_data["birth_month"] - 1}].textContent;
                }}
                return 'not found';
            }}
            """,
            "description": f"Select birth month: {student_data['birth_month']}"
        },
        {
            "action": "type",
            "selector": '#sid-birthdate-day',
            "text": str(student_data["birth_day"]),
            "description": "Enter birth day"
        },
        {
            "action": "type",
            "selector": '#sid-birthdate-year',
            "text": str(student_data["birth_year"]),
            "description": "Enter birth year"
        },
        {
            "action": "click",
            "selector": '#sid-college-name',
            "description": "Focus school search"
        },
        {
            "action": "type",
            "selector": '#sid-college-name',
            "text": student_data["organization_name"],
            "description": "Enter university name"
        },
        {
            "action": "wait",
            "seconds": 3,
            "description": "Wait for autocomplete results"
        },
        {
            "action": "execute_js",
            "script": """
            () => {
                const menu = document.getElementById('sid-college-name-menu');
                if (menu) {
                    const options = menu.querySelectorAll('[role="option"]');
                    if (options.length > 0) {
                        options[0].click();
                        return options[0].textContent;
                    }
                }
                return 'no options';
            }
            """,
            "description": "Select first organization result"
        },
        {
            "action": "click",
            "selector": '#sid-submit-wrapper__collect-info',
            "description": "Submit form"
        },
        {
            "action": "wait_for_load_state",
            "state": "networkidle",
            "timeout": 30000,
            "description": "Wait for submission"
        },
        {
            "action": "execute_js",
            "script": """
            () => {
                const stepEl = document.querySelector('[data-testid^="sid-step-"]');
                return {
                    step: stepEl ? stepEl.getAttribute('data-testid') : 'unknown',
                    url: window.location.href
                };
            }
            """,
            "description": "Check current step"
        }
    ])
    
    return steps


if __name__ == "__main__":
    # Example usage
    student = {
        "first_name": "Mohamed",
        "last_name": "Benali",
        "email": "mohamed.benali@example.com",
        "country_code": "DZ",
        "country_name": "Algeria",
        "organization_name": "University of Science and Technology Houari Boumediene",
        "birth_month": 6,
        "birth_day": 15,
        "birth_year": 2000
    }
    
    steps = generate_automation_steps(student, "<TEST_VID_REDACTED>")
    print(json.dumps(steps, indent=2))