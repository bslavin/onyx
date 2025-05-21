# YouTrack Connector for Onyx

This connector allows you to index content from YouTrack projects into Onyx, with support for querying multiple projects simultaneously.

## Features

- Index issues from one or multiple YouTrack projects
- Support for custom queries to refine which issues are indexed
- Automatically handles pagination and rate limits
- Supports incremental indexing (polling)
- Includes utility scripts to list available projects

## Setup

### 1. Prerequisites

You'll need the following credentials for the YouTrack connector:

- **YouTrack URL**: Your YouTrack instance URL (e.g., `https://youtrack.example.com`)
- **API Token**: A permanent token with read access to your projects
- **Project ID(s)**: The ID(s) of the project(s) you want to index

### 2. Finding Available Projects

To list all available projects in your YouTrack instance, use the provided script:

```bash
python list_youtrack_projects.py --url https://youtrack.example.com --token your-api-token
```

This will output a list of all projects with their IDs and save the full details to `youtrack_projects.json`.

### 3. Configuration

When setting up the connector in the Onyx admin interface:

1. Use the credential with your YouTrack URL and API token
2. In the "Project IDs" field, enter one or more project IDs (comma-separated for multiple projects)
3. Optionally, provide a custom query to filter which issues are indexed

## Advanced Options

- **Custom Query**: A YouTrack query string to filter which issues are indexed (e.g., `#Unresolved`)
- **Include Comments**: Whether to include issue comments in the indexed content

## Multi-Project Support

The connector supports indexing multiple projects simultaneously by specifying comma-separated project IDs:

```
PROJECT-1,PROJECT-2,PROJECT-3
```

This allows you to:

1. **Consolidate Information**: Search across multiple projects in a single query
2. **Create Logical Groupings**: Combine related projects based on teams or workstreams
3. **Reduce Overhead**: No need to create separate connectors for each project

## Implementation Details

The connector uses the YouTrack REST API v2023.1 to fetch issues from projects:

- The API is documented at: https://www.jetbrains.com/help/youtrack/devportal/youtrack-rest-api.html
- Requests authenticate using a permanent token
- Issues and their fields are converted to Onyx Documents with appropriate metadata

## Troubleshooting

If you encounter issues with the connector:

1. Check that your credentials (URL, API token) are correct
2. Verify that the project IDs exist and are accessible with your API token
3. Look for error messages in the logs
4. Try indexing a single project at a time to isolate any issues

## References

- [YouTrack REST API Documentation](https://www.jetbrains.com/help/youtrack/devportal/youtrack-rest-api.html)
- [YouTrack Authentication Documentation](https://www.jetbrains.com/help/youtrack/devportal/authentication-rest-api.html)
- [YouTrack Issue Fields Reference](https://www.jetbrains.com/help/youtrack/devportal/api-entity-IssueFields.html)
