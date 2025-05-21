# YouTrack Knowledge Base Integration Plan

This document outlines the plan for enhancing the YouTrack connector to include Knowledge Base articles.

## Overview

The YouTrack connector currently supports indexing issues from YouTrack projects. The enhancement will extend it to also index Knowledge Base articles available at URLs like `/youtrack/articles/MAILHOP`. This will provide a more comprehensive coverage of YouTrack content.

## API Investigation Results

Based on testing with your YouTrack instance, we found:

1. The API endpoint `/api/articles` lists all available KB articles
2. Individual articles can be retrieved via `/api/articles/{id}` with fields parameter
3. Articles contain rich information:
   - ID, summary (title), content (in Markdown)
   - Project association (ties to the same projects as issues)
   - Creation and update timestamps
   - Attachments with URLs
   - Visibility settings

## Implementation Plan

### 1. Backend Connector Enhancements

#### Add Knowledge Base Fetching Methods

```python
def _fetch_articles_from_project(self, project_id: str, updated_since: Optional[datetime] = None) -> Iterator[List[dict]]:
    """
    Fetches knowledge base articles from a specific project, handling pagination.
    Filters by 'updated_since' if provided.
    """
    if not self.api_url or not project_id:
        raise ConnectorMissingCredentialError("YouTrack connector not properly configured.")

    # Build query for articles in the project
    url = f"{self.api_url}/articles"
    params = {
        "fields": "id,summary,content(text),project(id,name,shortName),created,updated,attachments(id,name,url)",
        "$top": 30  # Batch size
    }
    
    # If filtering by project
    if project_id:
        params["query"] = f"project: {project_id}"
    
    # If filtering by update time
    if updated_since:
        updated_since_ms = int(updated_since.timestamp() * 1000)
        # Add timestamp filter to query
        if "query" in params:
            params["query"] += f" updated: {updated_since_ms} .."
        else:
            params["query"] = f"updated: {updated_since_ms} .."
    
    # Handle pagination
    skip = 0
    
    while True:
        params["$skip"] = skip
        response = self._make_api_request(url, params)
        
        if not response or not isinstance(response, list) or not response:
            break
            
        yield response
        
        if len(response) < params["$top"]:
            break
            
        skip += params["$top"]
        time.sleep(1)  # Basic rate limiting
```

#### Add Article Document Creation

```python
def _create_doc_from_article(self, article: dict, base_url: str) -> Document:
    """
    Creates an Onyx Document from a YouTrack KB article.
    """
    try:
        article_id = str(article.get("id", "UNKNOWN"))
        summary = article.get("summary", "Untitled Article")
        project = article.get("project", {})
        project_name = project.get("shortName", "") if project else ""
        content_obj = article.get("content", {})
        content = content_obj.get("text", "") if isinstance(content_obj, dict) else ""
        
        # Convert attachments to links and append to content
        attachments = article.get("attachments", [])
        if attachments:
            content += "\n\n## Attachments\n"
            for attachment in attachments:
                name = attachment.get("name", "")
                url = attachment.get("url", "")
                if name and url:
                    # Convert relative URLs to absolute
                    full_url = f"{base_url}{url}" if url.startswith("/") else url
                    content += f"\n- [{name}]({full_url})"
                    
        # Get timestamps
        doc_created_at = None
        doc_updated_at = None
        
        if "created" in article:
            try:
                # YouTrack uses milliseconds since epoch
                timestamp = int(article["created"]) / 1000.0
                doc_created_at = datetime.fromtimestamp(timestamp, timezone.utc)
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing article creation time: {e}")
        
        if "updated" in article:
            try:
                # YouTrack uses milliseconds since epoch
                timestamp = int(article["updated"]) / 1000.0
                doc_updated_at = datetime.fromtimestamp(timestamp, timezone.utc)
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing article update time: {e}")
                
        # Default to current time if parsing fails
        if not doc_created_at:
            doc_created_at = datetime.now(timezone.utc)
        if not doc_updated_at:
            doc_updated_at = datetime.now(timezone.utc)
            
        # Create document
        kb_link = f"{base_url}/articles/{project_name}-{article_id}" if project_name else f"{base_url}/articles/{article_id}"
        
        document = Document(
            id=f"{_YOUTRACK_ID_PREFIX}KB_{article_id}",
            sections=[
                TextSection(
                    link=kb_link,
                    text=content,
                )
            ],
            source=DocumentSource.YOUTRACK,
            semantic_identifier=f"[KB] {summary}",
            metadata={
                "id": article_id,
                "summary": summary,
                "type": "knowledge_base_article",
                "project": project,
                "kb_url": kb_link,
            },
            doc_created_at=doc_created_at,
            doc_updated_at=doc_updated_at,
        )
        
        return document
    except Exception as e:
        logger.error(f"Error processing KB article: {e}")
        # Create a basic error document as fallback
        error_id = f"ERROR_KB_{time.time()}"
        return Document(
            id=_YOUTRACK_ID_PREFIX + error_id,
            sections=[TextSection(link="", text=f"Error processing KB article: {str(e)}")],
            source=DocumentSource.YOUTRACK,
            semantic_identifier="Error KB Document",
            metadata={"error": "KB document creation failed"},
            doc_updated_at=datetime.now(timezone.utc),
        )
```

#### Integrate into Main Loading Method

```python
def _process_content(self, project_ids: List[str], start_time: Optional[datetime] = None) -> GenerateDocumentsOutput:
    """
    Process issues and KB articles from multiple projects, converting them to Onyx Documents.
    Accepts a list of project IDs to fetch from.
    """
    # ... existing code for processing issues ...
    
    # Process KB articles if enabled
    if self.include_kb_articles:
        for project_id in project_ids:
            logger.info(f"Processing KB articles for project ID: {project_id}")
            project_article_count = 0
            
            for article_batch in self._fetch_articles_from_project(project_id, start_time):
                if not article_batch:
                    continue
                    
                logger.info(f"Processing batch of {len(article_batch)} KB articles from project {project_id}")
                kb_batch = []
                
                for article_data in article_batch:
                    article_doc = self._create_doc_from_article(article_data, self.base_url)
                    kb_batch.append(article_doc)
                    project_article_count += 1
                
                if kb_batch:
                    logger.info(f"Yielding batch of {len(kb_batch)} KB articles")
                    yield kb_batch
            
            logger.info(f"Completed processing {project_article_count} KB articles for project {project_id}")
    
    # ... rest of the code ...
```

### 2. UI Configuration Updates

Update the connector configuration in `web/src/lib/connectors/connectors.tsx`:

```typescript
[ValidSources.YouTrack]: {
  serviceProvider: "YouTrack",
  displayName: "YouTrack",
  inputs: [
    {
      label: "Project IDs",
      name: "youtrack_project_ids",
      type: "text",
      helpText:
        "Comma-separated list of YouTrack project IDs to index (e.g., PROJECT-1,PROJECT-2)",
      required: true,
    },
    {
      label: "Custom Query",
      name: "custom_query",
      type: "text",
      helpText:
        "Optional YouTrack query to filter which issues are indexed (e.g., #Unresolved)",
      required: false,
    },
    {
      label: "Include Comments",
      name: "include_comments", 
      type: "toggle",
      helpText: "Whether to include issue comments in the indexed content",
      required: false,
      defaultValue: true
    },
    {
      label: "Include Knowledge Base Articles",
      name: "include_kb_articles",
      type: "toggle",
      helpText: "Whether to include Knowledge Base articles from indexed projects",
      required: false,
      defaultValue: true
    }
  ],
  credentialInputs: [
    // ... existing credential inputs ...
  ],
}
```

Update the connector configuration interface:

```typescript
export interface YouTrackConfig {
  youtrack_project_ids?: string;
  custom_query?: string;
  include_comments?: boolean;
  include_kb_articles?: boolean;
}
```

### 3. Constructor Update

Add the KB articles toggle parameter to the constructor:

```python
def __init__(
    self, 
    youtrack_url: Optional[str] = None,
    youtrack_token: Optional[str] = None,
    youtrack_project_id: Optional[str] = None,
    youtrack_project_ids: Optional[str] = None,
    project_id: Optional[str] = None,  # For alternative field naming
    custom_query: Optional[str] = None,
    include_comments: bool = True,
    include_kb_articles: bool = True,  # New parameter
    batch_size: int = INDEX_BATCH_SIZE,
    connector_specific_config: Optional[dict] = None,
    **kwargs
) -> None:
    """
    Initialize the YouTrack connector.
    
    Args:
        youtrack_url: Base URL of the YouTrack instance
        youtrack_token: API token for authentication
        youtrack_project_id: Single project ID (legacy)
        youtrack_project_ids: Comma-separated list of project IDs
        project_id: Alternative field for single project ID
        custom_query: Optional YouTrack query to filter issues
        include_comments: Whether to include issue comments
        include_kb_articles: Whether to include knowledge base articles
        batch_size: Number of documents to process in each batch
        connector_specific_config: Configuration specific to this connector
    """
    # ... existing initialization code ...
    self.include_kb_articles = include_kb_articles
    # ... rest of the code ...
```

## Testing Plan

1. Create a script to test KB article retrieval:
   - List articles available across projects
   - Fetch article details and content
   - Test document creation from articles

2. Test the integration:
   - Verify articles are retrieved and converted to documents
   - Check proper URL generation for Knowledge Base content
   - Test filtering by project and update time

## Next Steps

1. Implement the code changes in the YouTrack connector
2. Add the UI configuration updates
3. Create test scripts for KB article retrieval
4. Test the integration with your YouTrack instance
5. Update the YouTrack connector documentation
