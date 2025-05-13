# Freshdesk Knowledge Base Connector for Onyx

This connector allows Onyx to index Freshdesk Knowledge Base articles from specific folders.

## Features

- Bulk indexing of all solution articles from a specified folder
- Support for incremental updates through polling
- Slim document retrieval for pruning old documents
- HTML content cleaning to extract meaningful text from articles
- Support for agent and public URLs in metadata

## Prerequisites

- Freshdesk account with admin access or API permissions
- API key for your Freshdesk domain
- Folder ID of the Freshdesk Knowledge Base folder you want to index

## Setup

### Obtaining the Required Credentials

1. **Freshdesk API Key**: In your Freshdesk portal, go to Profile settings → API Key
2. **Freshdesk Domain**: This is the subdomain of your Freshdesk account (e.g., `yourdomain` in `https://yourdomain.freshdesk.com`)
3. **Folder ID**: 
   - Navigate to the Solution folder you want to index in your Freshdesk portal
   - The folder ID is in the URL (e.g., in `https://yourdomain.freshdesk.com/a/solutions/folders/12345678`, the folder ID is `12345678`)
4. **Portal URL** (optional): The URL of your customer-facing portal (e.g., `https://support.yourdomain.com`)
5. **Portal ID** (optional): Found in the Help Center settings or the portal URL

### Configuring the Connector in Onyx

1. Go to Admin → Connectors → Add Connector
2. Select "Freshdesk Knowledge Base"
3. Fill in the required credentials:
   - Freshdesk Domain
   - Freshdesk API Key
   - Folder ID
   - Portal URL (optional)
   - Portal ID (optional)
4. Configure access settings
5. Save the connector

## How it Works

The connector retrieves articles from the specified Freshdesk Knowledge Base folder, processes the HTML content to extract text, and indexes them in Onyx. Article metadata is preserved, including links to the original articles.

### Authentication

The connector uses Basic Authentication with your API key as the username and "X" as the password, which is the standard authentication method for Freshdesk API.

### Polling Mechanism

For incremental updates, the connector uses a polling mechanism to retrieve articles that have been updated since the last poll.

## Troubleshooting

- Verify your API key has the necessary permissions
- Check that the folder ID exists and contains articles
- Ensure your credentials are entered correctly
- Review the logs for any error messages

## API References

- [Freshdesk Solutions API Documentation](https://developers.freshdesk.com/api/#solutions)
