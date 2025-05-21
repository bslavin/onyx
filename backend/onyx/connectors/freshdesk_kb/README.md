# Freshdesk Knowledge Base Connector

This connector allows you to index content from Freshdesk Knowledge Base folders into Onyx.

## Features

- Index articles from one or multiple Freshdesk Knowledge Base folders
- Automatically handles pagination and rate limits
- Supports incremental indexing (polling)
- Includes a utility script to list all available folders

## Setup

### 1. Prerequisites

You'll need the following credentials for the Freshdesk KB connector:

- **Freshdesk Domain**: Your Freshdesk domain (e.g., `company.freshdesk.com`)
- **API Key**: Your Freshdesk API key
- **Folder ID(s)**: The ID(s) of the folder(s) you want to index

### 2. Finding Available Folders

You have several options to list available folders in your Freshdesk Knowledge Base:

#### Option 1: Backend Script

Use the provided backend script:

```bash
python backend/scripts/list_freshdesk_kb_folders.py --domain your-domain.freshdesk.com --api-key your-api-key --pretty
```

This will output a list of all folders with their IDs and save the full details to `folders.json`.

#### Option 2: Standalone Scripts

For a more flexible approach, you can use the standalone scripts in the project root:

**List all folders across all categories:**
```bash
python standalone_list_freshdesk_folders.py --domain your-domain.freshdesk.com --api-key your-api-key --pretty
```

**List folders in a specific category (e.g., the "Internal" category):**
```bash
python list_category_folders.py
```

The `list_category_folders.py` script is particularly useful as it shows:
- Folder ID
- Folder name
- Description
- Article count
- Creation date
- URL to access the folder

It also saves a detailed JSON file (`category_5000116325_folders.json`) that you can use for future reference.

#### Common Folders (Category ID: 5000116325)

Here are some useful folders from the "Internal" category that you might want to index:

| Folder ID    | Name               | Article Count |
|--------------|--------------------|--------------:|
| 5000201824   | Internal General   | 153           |
| 5000258916   | Old Archived       | 110           |
| 5000278970   | WHMCS              | 51            |
| 5000256156   | Halon              | 49            |
| 5000184231   | Inbound Solutions  | 40            |
| 5000255116   | CallTek            | 39            |
| 5000247987   | Tickets and Tools  | 39            |
| 5000184232   | Outbound Solutions | 20            |

You can index multiple folders by combining their IDs with commas, such as: `5000184231,5000184232`

### 3. Configuration

When setting up the connector in the Onyx admin interface:

1. Use the credential with your Freshdesk domain and API key
2. In the "Folder IDs" field, enter one or more folder IDs (comma-separated for multiple folders)
3. Optionally, provide the Portal URL and Portal ID for better link generation

## Advanced Options

- **Single Folder ID**: For backward compatibility only. Use the main "Folder IDs" field instead.
- **Portal URL**: The URL of your Freshdesk portal (e.g., `https://support.your-company.com`)
- **Portal ID**: The ID of your Freshdesk portal, used for agent URLs

## Troubleshooting

If you encounter issues with the connector:

1. Check that your credentials (domain, API key) are correct
2. Verify that the folder IDs exist and are accessible with your API key
3. Look for error messages in the logs
4. Try indexing a single folder at a time to isolate any issues

## Implementation Details

The connector uses the Freshdesk API v2 to fetch articles from solution folders:

- Categories contain folders, which contain articles
- The connector first lists all available folders when using the folder listing script
- When indexing, it fetches articles directly from the specified folder IDs
- Each article is converted to an Onyx Document with appropriate metadata

## Performance Considerations

- Use multiple folder IDs when you need to index content from different categories
- The connector handles API rate limits automatically
- For large knowledge bases, indexing may take some time due to API pagination

## Changelog

### v1.5
- Added support for indexing multiple folders
- Improved error handling and recovery
- Added folder listing utility script
- Enhanced document yielding to prevent lost documents

### v1.0
- Initial implementation with single folder support
