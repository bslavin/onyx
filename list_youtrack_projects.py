#!/usr/bin/env python3
"""
Standalone script to list available YouTrack projects.
This helps identify project IDs for configuring the YouTrack connector.

Usage:
    python list_youtrack_projects.py --url https://youtrack.example.com --token your-permanent-token --pretty

Parameters:
    --url: YouTrack instance URL (required)
    --token: YouTrack permanent token (required)
    --pretty: Format JSON output for readability (optional)
"""

import argparse
import json
import sys
import requests
from typing import Dict, List, Any


def list_projects(base_url: str, token: str) -> List[Dict[str, Any]]:
    """List all available YouTrack projects.
    
    Args:
        base_url: Base URL of the YouTrack instance
        token: API token for authentication
        
    Returns:
        List of project data objects
    """
    api_url = f"{base_url}/api"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    # API endpoint for projects - requesting specific fields
    url = f"{api_url}/admin/projects?fields=id,name,shortName,description,archived,createdBy,created"
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        projects = response.json()
        
        if not isinstance(projects, list):
            print(f"Error: Unexpected API response format: {type(projects)}")
            return []
        
        return projects
    
    except requests.exceptions.RequestException as e:
        print(f"Error making API request: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response status code: {e.response.status_code}")
            print(f"Response text: {e.response.text}")
        return []


def display_projects(projects: List[Dict[str, Any]]) -> None:
    """Display projects in a readable format.
    
    Args:
        projects: List of project data objects
    """
    if not projects:
        print("No projects found or error occurred.")
        return
    
    print(f"\nFound {len(projects)} YouTrack projects:\n")
    print(f"{'ID':<10} | {'Short Name':<15} | {'Name':<40} | {'Created':<24}")
    print("-" * 95)
    
    for project in projects:
        project_id = project.get("id", "N/A")
        short_name = project.get("shortName", "N/A")
        name = project.get("name", "N/A")
        created = project.get("created", "N/A")
        
        print(f"{project_id:<10} | {short_name:<15} | {name:<40} | {created}")
    
    print("\nUse these project IDs in the YouTrack connector configuration.")
    print("For multiple projects, use comma-separated IDs: PROJECT-1,PROJECT-2")


def main():
    parser = argparse.ArgumentParser(description="List YouTrack projects")
    parser.add_argument("--url", required=True, help="YouTrack instance URL")
    parser.add_argument("--token", required=True, help="YouTrack permanent token")
    parser.add_argument("--pretty", action="store_true", help="Format JSON output")
    
    args = parser.parse_args()
    
    # Make sure URL doesn't end with a slash
    base_url = args.url.rstrip('/')
    
    # Get projects
    projects = list_projects(base_url, args.token)
    
    # Display projects
    display_projects(projects)
    
    # Save to file
    if projects:
        indent = 2 if args.pretty else None
        with open('youtrack_projects.json', 'w') as f:
            json.dump(projects, f, indent=indent)
        print(f"\nSaved full details to youtrack_projects.json")


if __name__ == "__main__":
    main()
