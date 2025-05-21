#!/usr/bin/env python3
"""
Test script to verify multi-project functionality for the YouTrack connector.
This script directly tests the connector's ability to handle multiple projects.

Usage:
    python test_youtrack_multi_project.py --url https://youtrack.example.com --token your-permanent-token --projects PROJECT-1,PROJECT-2

Parameters:
    --url: YouTrack instance URL (required)
    --token: YouTrack permanent token (required)
    --projects: Comma-separated list of project IDs to test (required)
    --query: Custom query to filter issues (optional)
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import requests
from bs4 import BeautifulSoup


def clean_html_content(html_content: str) -> str:
    """
    Cleans HTML content, extracting plain text.
    Uses BeautifulSoup to parse HTML and get text.
    """
    if not html_content:
        return ""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        text_parts = [p.get_text(separator=" ", strip=True) for p in soup.find_all(['p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])]
        if not text_parts:
            return soup.get_text(separator=" ", strip=True)
        return "\n".join(text_parts)
    except Exception as e:
        print(f"Error cleaning HTML with BeautifulSoup: {e}")
        return html_content


def fetch_issues_from_project(base_url: str, token: str, project_id: str, custom_query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch issues from a specific project.
    
    Args:
        base_url: Base URL of the YouTrack instance
        token: API token for authentication
        project_id: Project ID to fetch issues from
        custom_query: Optional query to filter issues
        
    Returns:
        List of issue data objects
    """
    api_url = f"{base_url}/api"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    # Build query
    query = f"project: {project_id}"
    if custom_query:
        query += f" {custom_query}"
    
    # First fetch to get total
    url = f"{api_url}/issues"
    params = {
        "query": query,
        "$top": 1,
        "fields": "id,idReadable,summary,description,created,updated"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        print(f"Testing access to project {project_id}...")
        
        # Now fetch all issues
        all_issues = []
        skip = 0
        top = 30  # Number of issues per batch
        
        while True:
            params = {
                "query": query,
                "$skip": skip,
                "$top": top,
                "fields": "id,idReadable,summary,description,created,updated,customFields,reporter(name)"
            }
            
            print(f"Fetching issues from project {project_id}, skip {skip}...")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            
            issues = response.json()
            if not issues:
                break
                
            all_issues.extend(issues)
            print(f"Retrieved {len(issues)} issues (total so far: {len(all_issues)})")
            
            if len(issues) < top:
                break
                
            skip += top
            time.sleep(1)  # Rate limiting
        
        return all_issues
    
    except requests.exceptions.RequestException as e:
        print(f"Error fetching issues from project {project_id}: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response status code: {e.response.status_code}")
            print(f"Response text: {e.response.text}")
        return []


def test_multi_project(base_url: str, token: str, project_ids: List[str], custom_query: Optional[str] = None) -> None:
    """Test fetching issues from multiple projects.
    
    Args:
        base_url: Base URL of the YouTrack instance
        token: API token for authentication
        project_ids: List of project IDs to fetch issues from
        custom_query: Optional query to filter issues
    """
    print(f"\nTesting multi-project functionality for {len(project_ids)} projects: {project_ids}")
    
    all_issues = []
    project_stats = {}
    
    # Process each project
    for project_id in project_ids:
        issues = fetch_issues_from_project(base_url, token, project_id, custom_query)
        all_issues.extend(issues)
        project_stats[project_id] = len(issues)
        
        # Brief pause to avoid rate limiting
        time.sleep(1)
    
    # Print summary statistics
    print("\n" + "=" * 60)
    print(f"Multi-Project Test Results")
    print("=" * 60)
    print(f"Total projects tested: {len(project_ids)}")
    print(f"Total issues found: {len(all_issues)}")
    print("\nIssues per project:")
    
    for project_id, count in project_stats.items():
        print(f"  {project_id}: {count} issues")
    
    # Save sample data
    if all_issues:
        with open('youtrack_issues_sample.json', 'w') as f:
            sample_size = min(5, len(all_issues))
            sample_issues = all_issues[:sample_size]
            json.dump(sample_issues, f, indent=2)
        print(f"\nSaved {sample_size} sample issues to youtrack_issues_sample.json")
    
    # Save statistics
    stats = {
        "timestamp": datetime.now().isoformat(),
        "projects_tested": len(project_ids),
        "total_issues": len(all_issues),
        "issues_per_project": project_stats,
        "custom_query": custom_query,
        "query_url": f"{base_url}/api/issues"
    }
    
    with open('youtrack_test_stats.json', 'w') as f:
        json.dump(stats, f, indent=2)
    print(f"Saved test statistics to youtrack_test_stats.json")


def main():
    parser = argparse.ArgumentParser(description="Test YouTrack multi-project functionality")
    parser.add_argument("--url", required=True, help="YouTrack instance URL")
    parser.add_argument("--token", required=True, help="YouTrack permanent token")
    parser.add_argument("--projects", required=True, help="Comma-separated list of project IDs")
    parser.add_argument("--query", help="Custom query to filter issues")
    
    args = parser.parse_args()
    
    # Make sure URL doesn't end with a slash
    base_url = args.url.rstrip('/')
    
    # Parse project IDs
    project_ids = [pid.strip() for pid in args.projects.split(',') if pid.strip()]
    
    if not project_ids:
        print("Error: No valid project IDs provided")
        sys.exit(1)
    
    test_multi_project(base_url, args.token, project_ids, args.query)


if __name__ == "__main__":
    main()
