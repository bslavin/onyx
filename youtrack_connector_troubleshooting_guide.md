# YouTrack Connector Troubleshooting Guide

This guide helps with diagnosing and fixing common YouTrack connector issues.

## Authentication Issues

### Session Timeouts

If you see 401/403 errors in the logs after successful validation:

```
INFO:  Successfully validated YouTrack connector for project DMARC
ERROR: 401: Unauthorized
```

This is likely due to an Onyx application session timeout, not a YouTrack connector issue.

**Fix:**
1. Log out of Onyx and log back in
2. Try using a private/incognito browser window
3. Clear browser cookies and cache
4. If using API keys, ensure they haven't expired

### YouTrack Token Issues

If validation fails with authentication errors:

**Fix:**
1. Regenerate your YouTrack API token
2. Make sure the token has appropriate permissions:
   - Read access to projects
   - Read access to issues

## Indexing Problems

### Only Canary Document Appears

If only the "CANARY TEST DOCUMENT" appears in your search results, but no actual issues:

**Fix:**
1. Check logs for "CRITICAL DEBUG" messages to locate where processing fails
2. Verify project IDs are correctly formatted (see below)
3. Force reindexing (see end of document)

### Project ID Format Issues

The most common issue is using incorrect project identifiers:

- ✅ **Correct:** `DMARC` (project shortName only)
- ✅ **Correct:** `MAILHOP,NUREPLY,AUTOSPF` (multiple shortNames)
- ❌ **Incorrect:** `NUREPLY-155` (this is an issue ID, not a project ID)
- ❌ **Incorrect:** `https://duo.myjetbrains.com/youtrack/projects/MAILHOP` (URL, not ID)

To verify your project IDs:

```bash
python3 list_youtrack_projects.py --url https://your-youtrack-url --token your-token --pretty
```

Use the values from the "Short Name" column in your configuration.

### URL Format Issues

Ensure your YouTrack URL:
- Does not have trailing slashes
- Is the base URL only (not a specific path)

✅ Correct: `https://duo.myjetbrains.com`
❌ Incorrect: `https://duo.myjetbrains.com/`
❌ Incorrect: `https://duo.myjetbrains.com/youtrack`

## Performance Issues

If indexing is running very slowly:

1. Use a comma-separated list of specific project IDs rather than indexing all projects
2. If including comments, consider disabling them for faster indexing
3. Use a custom query to filter issues (e.g., `created: 2023-01-01 ..`)

## Force Reindexing

If all else fails, you can force a complete reindexing:

1. Go to Admin → Connectors
2. Find your YouTrack connector and click "Edit"
3. Switch to the "Indexing" tab
4. Click "Re-Index Connector"

## Debugging Tips

When submitting support requests, include:

1. Complete connector logs (especially "CRITICAL DEBUG" messages)
2. Output from the `list_youtrack_projects.py` script
3. The exact configuration values you're using (URL and project IDs)
4. Any error messages from the browser console

## Common Status Codes

- **200:** Success
- **401:** Authentication failure (invalid token)
- **403:** Permission denied (valid token, insufficient permissions)
- **404:** Not found (incorrect URL or project ID)
- **429:** Rate limited (too many requests)
