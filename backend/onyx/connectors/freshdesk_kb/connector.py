"""Freshdesk Knowledge Base connector implementation for Onyx. (v1.5)"""

import json
import time
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

from onyx.configs.app_configs import INDEX_BATCH_SIZE
from onyx.configs.constants import DocumentSource
from onyx.connectors.interfaces import GenerateDocumentsOutput, LoadConnector, PollConnector, SecondsSinceUnixEpoch, SlimConnector, GenerateSlimDocumentOutput
from onyx.connectors.models import ConnectorMissingCredentialError, Document, TextSection, SlimDocument
from onyx.indexing.indexing_heartbeat import IndexingHeartbeatInterface
from onyx.utils.logger import setup_logger

logger = setup_logger()

_FRESHDESK_KB_ID_PREFIX = "FRESHDESK_KB_"

# Fields to extract from solution articles
_SOLUTION_ARTICLE_FIELDS_TO_INCLUDE = {
    "id",
    "title",
    "description", # HTML content
    "description_text", # Plain text content
    "folder_id",
    "category_id",
    "status", # 1: Draft, 2: Published
    "tags",
    "thumbs_up",
    "thumbs_down",
    "hits",
    "created_at",
    "updated_at",
}


def _clean_html_content(html_content: str) -> str:
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
        logger.error(f"Error cleaning HTML with BeautifulSoup: {e}")
        return html_content


def _create_metadata_from_article(article: dict, domain: str, portal_url: str, portal_id: str) -> dict:
    """
    Creates a metadata dictionary from a Freshdesk solution article.
    """
    metadata: dict[str, Any] = {}
    article_id = article.get("id")

    for key, value in article.items():
        if key not in _SOLUTION_ARTICLE_FIELDS_TO_INCLUDE:
            continue
        if value is None or (isinstance(value, list) and not value):  # Skip None or empty lists
            continue
        metadata[key] = value
    
    # Construct URLs
    if article_id:
        # Agent URL (the one with portalId)
        if portal_url and portal_id:
            portal_base = portal_url.rstrip('/')
            metadata["agent_url"] = f"{portal_base}/a/solutions/articles/{article_id}?portalId={portal_id}"
        else:
            logger.warning(f"Could not construct agent_url for article {article_id}: missing portal_url or portal_id.")

        # Public/API Domain URL
        if domain:
            public_portal_base = f"https://{domain.rstrip('/')}"
            metadata["public_url"] = f"{public_portal_base}/a/solutions/articles/{article_id}"
        else:
            logger.warning(f"Could not construct public_url for article {article_id}: missing domain.")
            
    # Convert status number to human-readable string
    status_number = article.get("status")
    if status_number == 1:
        metadata["status_string"] = "Draft"
    elif status_number == 2:
        metadata["status_string"] = "Published"
    else:
        metadata["status_string"] = "Unknown"

    return metadata


def _create_doc_from_article(article: dict, domain: str, portal_url: str, portal_id: str) -> Document:
    """
    Creates an Onyx Document from a Freshdesk solution article.
    """
    try:
        article_id = article.get("id")
        title = article.get("title", "Untitled Article")
        html_description = article.get("description", "")
        
        logger.info(f"Creating document from article: id={article_id}, title={title}")
        
        # Clean HTML content
        text_content = _clean_html_content(html_description)
        
        # Check if content was properly extracted
        if not text_content:
            logger.warning(f"No text content extracted from article {article_id}")
            text_content = article.get("description_text", "No content available")

        metadata = _create_metadata_from_article(article, domain, portal_url, portal_id)
        
        # Use agent_url as the primary link for the TextSection if available, else public_url
        link = metadata.get("agent_url") or metadata.get("public_url") or f"https://{domain}/a/solutions/articles/{article_id}"

        # Safely parse the updated_at date with multiple fallbacks
        doc_updated_at = datetime.now(timezone.utc)
        if article.get("updated_at"):
            try:
                # Multiple format handling for maximum compatibility
                updated_at_str = article["updated_at"]
                
                # Try different approaches to parse the date
                try:
                    # Method 1: Standard ISO format with Z timezone
                    if updated_at_str.endswith('Z'):
                        doc_updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                    # Method 2: Standard ISO format
                    else:
                        doc_updated_at = datetime.fromisoformat(updated_at_str)
                except (ValueError, TypeError):
                    try:
                        # Method 3: Try with dateutil parser which is more forgiving
                        from dateutil import parser
                        doc_updated_at = parser.parse(updated_at_str)
                    except (ImportError, ValueError, TypeError):
                        # Method 4: Manual parsing as last resort
                        import re
                        if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', updated_at_str):
                            # Format: 2020-02-05T08:55:42Z (common ISO format)
                            year, month, day = int(updated_at_str[0:4]), int(updated_at_str[5:7]), int(updated_at_str[8:10])
                            hour, minute, second = int(updated_at_str[11:13]), int(updated_at_str[14:16]), int(updated_at_str[17:19])
                            doc_updated_at = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
                        else:
                            # If all else fails, use current time
                            logger.warning(f"Could not parse date using any method: {updated_at_str}")
                            doc_updated_at = datetime.now(timezone.utc)
            except Exception as e:
                logger.warning(f"Date parsing completely failed for {article.get('updated_at')}: {e}")
                logger.warning(f"Using current time as fallback")

        document = Document(
            id=_FRESHDESK_KB_ID_PREFIX + str(article_id) if article_id else _FRESHDESK_KB_ID_PREFIX + "UNKNOWN",
            sections=[
                TextSection(
                    link=link,
                    text=text_content,
                )
            ],
            source=DocumentSource.FRESHDESK_KB,
            semantic_identifier=title,
            metadata=metadata,
            doc_updated_at=doc_updated_at,
        )
        
        logger.info(f"Successfully created document for article {article_id}")
        return document
    except Exception as e:
        logger.error(f"Error creating document from article {article.get('id')}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # Return a minimal document rather than raising an exception to avoid stopping the entire indexing process
        return Document(
            id=_FRESHDESK_KB_ID_PREFIX + str(article.get("id", "ERROR")),
            sections=[TextSection(link="", text="Error processing document")],
            source=DocumentSource.FRESHDESK_KB,
            semantic_identifier="Error Document",
            metadata={"error": str(e), "original_article_id": article.get("id")},
            doc_updated_at=datetime.now(timezone.utc),
        )


class FreshdeskKnowledgeBaseConnector(LoadConnector, PollConnector, SlimConnector):
    """
    Onyx Connector for fetching Freshdesk Knowledge Base (Solution Articles) from a specific folder.
    Implements LoadConnector for full indexing and PollConnector for incremental updates.
    """
    def __init__(
        self, 
        freshdesk_folder_id: Optional[str] = None,
        freshdesk_domain: Optional[str] = None,
        freshdesk_api_key: Optional[str] = None,
        freshdesk_portal_url: Optional[str] = None,
        freshdesk_portal_id: Optional[str] = None,
        batch_size: int = INDEX_BATCH_SIZE,
        connector_specific_config: Optional[dict] = None,
        **kwargs
    ) -> None:
        """
        Initialize the Freshdesk Knowledge Base connector.
        
        Args:
            freshdesk_folder_id: The ID of the folder to fetch articles from
            freshdesk_domain: Freshdesk domain (e.g., "company.freshdesk.com")
            freshdesk_api_key: API key for authentication
            freshdesk_portal_url: Optional URL for agent portal links
            freshdesk_portal_id: Optional ID for agent portal links
            batch_size: Number of documents to process in each batch
            connector_specific_config: Configuration specific to this connector
        """
        self.batch_size = batch_size
        self.api_key = freshdesk_api_key
        self.domain = freshdesk_domain
        self.password = "X"  # Freshdesk uses API key as username, 'X' as password
        
        # Get folder_id from constructor parameter or from connector_specific_config
        self.folder_id = freshdesk_folder_id
        if connector_specific_config:
            logger.info(f"connector_specific_config keys: {list(connector_specific_config.keys())}")
            
        if not self.folder_id and connector_specific_config and "freshdesk_folder_id" in connector_specific_config:
            self.folder_id = connector_specific_config.get("freshdesk_folder_id")
            logger.info(f"Using folder_id from connector_specific_config: {self.folder_id}")
        
        # Debug connector initialization params
        logger.info(f"Initializing Freshdesk KB connector with params:")
        logger.info(f"  - folder_id: {self.folder_id}")
        logger.info(f"  - domain: {self.domain}")
        logger.info(f"  - api_key: {'*****' + self.api_key[-4:] if self.api_key else None}")
        
        # Optional portal params
        self.portal_url = freshdesk_portal_url
        if not self.portal_url and connector_specific_config and "freshdesk_portal_url" in connector_specific_config:
            self.portal_url = connector_specific_config.get("freshdesk_portal_url")
            
        self.portal_id = freshdesk_portal_id
        if not self.portal_id and connector_specific_config and "freshdesk_portal_id" in connector_specific_config:
            self.portal_id = connector_specific_config.get("freshdesk_portal_id")
        
        self.headers = {"Content-Type": "application/json"}
        self.base_url = f"https://{self.domain}/api/v2" if self.domain else None
        self.auth = (self.api_key, self.password) if self.api_key else None

    def load_credentials(self, credentials: dict[str, str | int]) -> None:
        """Loads Freshdesk API credentials and configuration."""
        api_key = credentials.get("freshdesk_api_key")
        domain = credentials.get("freshdesk_domain")
        portal_url = credentials.get("freshdesk_portal_url")  # For constructing agent URLs
        portal_id = credentials.get("freshdesk_portal_id")    # For constructing agent URLs
        
        # Check credentials
        if not all(isinstance(cred, str) for cred in [domain, api_key] if cred is not None):
            missing = [
                name for name, val in {
                    "domain": domain, "api_key": api_key,
                }.items() if not isinstance(val, str)
            ]
            raise ConnectorMissingCredentialError(
                f"Required Freshdesk KB credentials must be strings. Missing/invalid: {missing}"
            )

        self.api_key = str(api_key)
        self.domain = str(domain)
        # Handle optional parameters
        self.portal_url = str(portal_url) if portal_url is not None else None
        self.portal_id = str(portal_id) if portal_id is not None else None
        self.base_url = f"https://{self.domain}/api/v2"
        self.auth = (self.api_key, self.password)
        
        # Log that credentials were loaded
        logger.info(f"CREDENTIALS LOADED: domain={self.domain}, api_key={'*****' + self.api_key[-4:] if self.api_key else None}")

    def validate_connector_settings(self) -> None:
        """
        Validate connector settings by testing API connectivity.
        """
        # Critical validation - check for domain and API key
        if not self.domain:
            logger.error("CRITICAL ERROR: Missing Freshdesk domain - check credentials!")
            raise ConnectorMissingCredentialError(
                "Missing required Freshdesk domain in credentials"
            )
            
        if not self.api_key:
            logger.error("CRITICAL ERROR: Missing Freshdesk API key - check credentials!")
            raise ConnectorMissingCredentialError(
                "Missing required Freshdesk API key in credentials"
            )

        # Get folder_id from connector config if not already set via constructor
        if not self.folder_id:
            logger.error("Missing folder_id in connector settings - this should come from connector_specific_config")
            raise ConnectorMissingCredentialError(
                "Missing folder_id in connector settings. Please configure the folder ID in connector settings."
            )
        
        # Log validation attempt with all parameters for debugging
        logger.info(f"Validating Freshdesk KB connector with:")
        logger.info(f"  domain: {self.domain}")
        logger.info(f"  folder_id: {self.folder_id}")
        logger.info(f"  api_key present: {'Yes' if self.api_key else 'No'}")
        logger.info(f"  base_url: {self.base_url}")
        
        try:
            # Test API by trying to fetch one article from the folder
            url = f"{self.base_url}/solutions/folders/{self.folder_id}/articles"
            params = {"page": 1, "per_page": 1}
            
            logger.info(f"Making validation request to: {url}")
            response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
            
            # Log the response for debugging
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    logger.info(f"Validation successful - got {len(data)} articles in response")
                    if len(data) > 0:
                        # Log details of first article
                        logger.info(f"First article: id={data[0].get('id')}, title={data[0].get('title')}")
                else:
                    logger.warning(f"Unexpected response format: {type(data)}")
            
            response.raise_for_status()
            logger.info(f"Successfully validated Freshdesk KB connector for folder {self.folder_id}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to validate Freshdesk KB connector: {e}")
            logger.error(f"Response: {response.text if 'response' in locals() else 'No response'}")
            if 'response' in locals():
                logger.error(f"Status code: {response.status_code}")
            raise ConnectorMissingCredentialError(
                f"Could not connect to Freshdesk API: {e}"
            )

    def _make_api_request(self, url: str, params: Optional[Dict[str, Any]] = None) -> Optional[List[Dict[str, Any]]]:
        """Makes a GET request to the Freshdesk API with rate limit handling."""
        if not self.auth:
            raise ConnectorMissingCredentialError("Freshdesk KB credentials not loaded.")
        
        # Verify the URL doesn't have duplicated domains (which could cause SSL errors)
        if ".freshdesk.com.freshdesk.com" in url:
            url = url.replace(".freshdesk.com.freshdesk.com", ".freshdesk.com")
            logger.warning(f"Fixed malformed URL containing duplicate domain: {url}")
        
        retries = 3
        for attempt in range(retries):
            try:
                response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
                response.raise_for_status()

                if response.status_code == 429:  # Too Many Requests
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limit exceeded. Retrying after {retry_after} seconds.")
                    time.sleep(retry_after)
                    continue
                
                return response.json()
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP error: {e} - {response.text if 'response' in locals() else 'No response'} for URL {url} with params {params}")
                return None
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e} for URL {url}")
                if attempt < retries - 1:
                    time.sleep(5 * (attempt + 1))
                else:
                    return None
        return None

    def _fetch_articles_from_folder(self, folder_id: str, updated_since: Optional[datetime] = None) -> Iterator[List[dict]]:
        """
        Fetches solution articles from a specific folder, handling pagination.
        Filters by 'updated_since' if provided.
        """
        if not self.base_url or not folder_id:
            raise ConnectorMissingCredentialError("Freshdesk KB connector not properly configured (base_url or folder_id missing).")

        page = 1
        while True:
            url = f"{self.base_url}/solutions/folders/{folder_id}/articles"
            params: dict[str, Any] = {"page": page, "per_page": 30}

            logger.info(f"Fetching articles from Freshdesk KB folder {folder_id}, page {page}...")
            article_batch = self._make_api_request(url, params)

            if article_batch is None:  # Error occurred
                logger.error(f"Failed to fetch articles for folder {folder_id}, page {page}.")
                break
            
            if not isinstance(article_batch, list):
                logger.error(f"Unexpected API response format for articles: {type(article_batch)}. Expected list.")
                break

            if not article_batch:  # No more articles
                logger.info(f"No more articles found for folder {folder_id} on page {page}.")
                break
            
            # If updated_since is provided, filter locally
            if updated_since:
                filtered_batch = []
                for article in article_batch:
                    if article.get("updated_at"):
                        article_updated_at = datetime.fromisoformat(article["updated_at"].replace("Z", "+00:00"))
                        if article_updated_at >= updated_since:
                            filtered_batch.append(article)
                
                if filtered_batch:
                    logger.info(f"Fetched {len(filtered_batch)} articles updated since {updated_since.isoformat()} from folder {folder_id}, page {page}.")
                    yield filtered_batch
            else:
                logger.info(f"Fetched {len(article_batch)} articles from folder {folder_id}, page {page}.")
                yield article_batch

            if len(article_batch) < params["per_page"]:
                logger.info(f"Last page reached for folder {folder_id}.")
                break
            
            page += 1
            time.sleep(1)  # Basic rate limiting

    def _process_articles(self, folder_id_to_fetch: str, start_time: Optional[datetime] = None) -> GenerateDocumentsOutput:
        """
        Processes articles from a folder, converting them to Onyx Documents.
        'start_time' is for filtering articles updated since that time.
        """
        if not self.domain:
            raise ConnectorMissingCredentialError("Freshdesk KB domain not loaded.")

        doc_batch: List[Document] = []
        article_count = 0
        processed_count = 0
        
        # Debug info about the connector state
        logger.info(f"Processing articles with folder_id={folder_id_to_fetch}, domain={self.domain}")
        logger.info(f"Using auth credentials: api_key={'*****' + self.api_key[-4:] if self.api_key else 'None'}")
        logger.info(f"Optional params: portal_url={self.portal_url}, portal_id={self.portal_id}")
        
        # Use portal_url and portal_id if available, otherwise use None
        portal_url = self.portal_url if self.portal_url else None
        portal_id = self.portal_id if self.portal_id else None
        
        try:
            for article_list_from_api in self._fetch_articles_from_folder(folder_id_to_fetch, start_time):
                article_count += len(article_list_from_api)
                logger.info(f"Received batch of {len(article_list_from_api)} articles from API")
                
                if len(article_list_from_api) > 0:
                    # Log sample article to help debug
                    sample = article_list_from_api[0]
                    logger.info(f"Sample article: id={sample.get('id')}, title={sample.get('title')}")
                    logger.info(f"Article keys: {list(sample.keys())}")
                
                for article_data in article_list_from_api:
                    try:
                        doc = _create_doc_from_article(article_data, self.domain, portal_url, portal_id)
                        doc_batch.append(doc)
                        processed_count += 1
                    except Exception as e:
                        logger.error(f"Error creating document for article ID {article_data.get('id')}: {e}")
                        logger.error(f"Article data: {article_data}")
                        continue

                    if len(doc_batch) >= self.batch_size:
                        logger.info(f"Yielding batch of {len(doc_batch)} documents")
                        yield doc_batch
                        doc_batch = []
        except Exception as e:
            logger.error(f"Error in _process_articles: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        if doc_batch:  # Yield any remaining documents
            logger.info(f"Yielding final batch of {len(doc_batch)} documents")
            yield doc_batch
        
        logger.info(f"Article processing complete: {processed_count}/{article_count} articles processed successfully")

    def load_from_state(self) -> GenerateDocumentsOutput:
        """Loads all solution articles from the configured folder."""
        if not self.folder_id:
            raise ConnectorMissingCredentialError("Freshdesk KB folder_id not configured for load_from_state.")
            
        # Double check credentials before starting indexing
        if not self.domain or not self.api_key:
            logger.error(f"CRITICAL ERROR: Missing credentials in load_from_state! domain={self.domain}, api_key_present={'Yes' if self.api_key else 'No'}")
            logger.error(f"Base URL: {self.base_url}, Auth: {bool(self.auth)}")
            raise ConnectorMissingCredentialError("Missing required Freshdesk credentials for indexing")
            
        logger.info(f"Loading all solution articles from Freshdesk KB folder: {self.folder_id}")
        logger.info(f"Using domain: {self.domain} and folder_id: {self.folder_id}")
        
        # Explicitly log that we're starting to yield documents
        logger.info(f"Starting to yield documents from Freshdesk KB folder: {self.folder_id}")
        yield from self._process_articles(self.folder_id)

    def poll_source(self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch) -> GenerateDocumentsOutput:
        """
        Polls for solution articles updated within the given time range.
        """
        if not self.folder_id:
            raise ConnectorMissingCredentialError("Freshdesk KB folder_id not configured for poll_source.")
            
        # Double check credentials before starting polling
        if not self.domain or not self.api_key:
            logger.error(f"CRITICAL ERROR: Missing credentials in poll_source! domain={self.domain}, api_key_present={'Yes' if self.api_key else 'No'}")
            logger.error(f"Base URL: {self.base_url}, Auth: {bool(self.auth)}")
            raise ConnectorMissingCredentialError("Missing required Freshdesk credentials for polling")
        
        start_datetime = datetime.fromtimestamp(start, tz=timezone.utc)
        
        logger.info(f"Polling Freshdesk KB folder {self.folder_id} for updates since {start_datetime.isoformat()}")
        logger.info(f"Using domain: {self.domain} and folder_id: {self.folder_id}")
        yield from self._process_articles(self.folder_id, start_datetime)

    def _get_slim_documents_for_article_batch(self, articles: List[Dict[str, Any]]) -> List[SlimDocument]:
        """Convert a batch of articles to SlimDocuments."""
        slim_docs = []
        for article in articles:
            article_id = article.get("id")
            if article_id:
                # All we need is the ID - no permissions data needed for this connector
                slim_docs.append(
                    SlimDocument(
                        id=_FRESHDESK_KB_ID_PREFIX + str(article_id),
                        perm_sync_data=None,
                    )
                )
        return slim_docs

    def retrieve_all_slim_documents(
        self,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
        callback: IndexingHeartbeatInterface | None = None,
    ) -> GenerateSlimDocumentOutput:
        """
        Retrieves all document IDs for pruning purposes.
        """
        if not self.folder_id:
            raise ConnectorMissingCredentialError("Freshdesk KB folder_id not configured for slim document retrieval.")
        
        start_datetime = datetime.fromtimestamp(start, tz=timezone.utc) if start else None
        
        slim_batch: List[SlimDocument] = []
        for article_batch in self._fetch_articles_from_folder(self.folder_id, start_datetime):
            # Convert to slim documents
            new_slim_docs = self._get_slim_documents_for_article_batch(article_batch)
            slim_batch.extend(new_slim_docs)
            
            # Heartbeat callback if provided
            if callback:
                callback.heartbeat()
            
            if len(slim_batch) >= self.batch_size:
                yield slim_batch
                slim_batch = []
        
        if slim_batch:
            yield slim_batch
